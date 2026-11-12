"""
Call orchestration: one caller utterance in, one spoken reply out.

Order of precedence for each turn:
1. If a consent sentence is outstanding, the caller's reply goes to the deterministic consent
   classifier, not to the LLM. A YES is stored as a consent event and only then is the saved
   wallet charged; the Guard re-reads that stored event. Barge-in during the sentence repeats it.
2. Otherwise the LLM chooses tools. Tool arguments are validated with Pydantic; a validation
   failure is fed back and further tool calls are disabled for the turn, forcing the model to
   ask the caller instead of guessing.
3. Repeated low-confidence turns (validation errors, unmatched items) hand off to a human.

Every turn is persisted with model, prompt version, measured LLM latency / TTFT and tokens.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import ValidationError

from apps.agent.llm import LLMClient
from apps.agent.prompts import (
    CONSENT_DECLINED,
    CONSENT_GIVE_UP,
    GREETING_NEW,
    GREETING_RETURNING,
    HANDOFF_LINE,
    LLM_UNAVAILABLE,
    PROMPT_VERSION,
    SILENCE_GOODBYE,
    SILENCE_REPROMPT,
    SYSTEM_PROMPT,
)
from apps.agent.tools import TOOL_MODELS, TOOL_SPECS, AgentTools, CallState
from apps.api.db.models import CallModel, CallTurnModel, OrderModel, Shop, utc_now
from apps.api.db.repository import OrderRepository
from apps.core import identity
from apps.core.container import Container
from apps.core.telemetry import record_client_span, tracer
from packages.domain.consent import classify_consent
from packages.domain.models import ConsentDecision
from packages.domain.pricing import format_cents

MAX_TOOL_STEPS = 4
MAX_HISTORY = 30
LOW_CONFIDENCE_HANDOFF = 3
MAX_CONSENT_ATTEMPTS = 2


class LLMUnavailableError(RuntimeError):
    pass


@dataclass
class TurnMetrics:
    model: Optional[str] = None
    llm_ms: int = 0
    ttft_ms: Optional[int] = None
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    valid_tool_calls: int = 0
    invalid_tool_calls: int = 0
    low_confidence: bool = False


@dataclass
class TurnResult:
    speech: str
    order: Optional[dict[str, Any]]
    ended: bool
    handoff_reason: Optional[str]
    awaiting: Optional[str]
    metrics: TurnMetrics
    latency_ms: int


class CallSession:
    def __init__(self, container: Container, call_id: str, state: CallState, llm: Optional[LLMClient] = None):
        self.c = container
        self.call_id = call_id
        self.state = state
        self.llm = llm if llm is not None else container.llm

    # ------------------------------------------------------------------ lifecycle

    @classmethod
    async def start(
        cls,
        container: Container,
        *,
        caller_phone: str,
        channel: str,
        shop_id: Optional[str] = None,
        llm: Optional[LLMClient] = None,
    ) -> tuple["CallSession", str]:
        shop_id = shop_id or container.settings.default_shop_id
        key = container.settings.caller_hash_key.get_secret_value()
        chash = identity.caller_hash(caller_phone, key)
        call_id = f"call_{uuid.uuid4().hex[:16]}"
        async with container.session_maker() as session, session.begin():
            shop = await session.get(Shop, shop_id)
            if shop is None:
                raise ValueError(f"Unknown shop {shop_id}")
            mandate = await OrderRepository(session).active_mandate(chash, shop_id)
            state = CallState(shop_id=shop_id, caller_hash=chash, channel=channel, has_saved_paypal=mandate is not None)
            greeting = (GREETING_RETURNING if mandate else GREETING_NEW).format(shop_name=shop.name)
            state.messages.append({"role": "assistant", "content": greeting})
            state.turn_seq = 1
            session.add(CallModel(id=call_id, shop_id=shop_id, caller_hash=chash, caller_last4=identity.last4(caller_phone),
                                  channel=channel, state_json=state.to_json()))
            session.add(CallTurnModel(call_id=call_id, seq=1, speaker="agent", text=greeting))
        return cls(container, call_id, state, llm), greeting

    @classmethod
    async def resume(cls, container: Container, call_id: str, llm: Optional[LLMClient] = None) -> Optional["CallSession"]:
        async with container.session_maker() as session:
            call = await session.get(CallModel, call_id)
            if call is None:
                return None
            return cls(container, call_id, CallState.from_json(call.state_json), llm)

    async def _save(self) -> None:
        async with self.c.session_maker() as session, session.begin():
            call = await session.get(CallModel, self.call_id)
            assert call is not None
            call.state_json = self.state.to_json()
            if self.state.ended or self.state.handoff_reason:
                call.ended_at = call.ended_at or utc_now()
                call.outcome = "handoff" if self.state.handoff_reason else (call.outcome or "ended")

    async def _record_turn(self, speaker: str, text: str, metrics: Optional[TurnMetrics] = None, *,
                           latency_ms: Optional[int] = None, stt_ms: Optional[int] = None,
                           tts_ms: Optional[int] = None) -> None:
        self.state.turn_seq += 1
        row = CallTurnModel(call_id=self.call_id, seq=self.state.turn_seq, speaker=speaker, text=text,
                            latency_ms=latency_ms, stt_ms=stt_ms, tts_ms=tts_ms)
        if metrics is not None and metrics.model:
            row.model = metrics.model
            row.prompt_version = PROMPT_VERSION
            row.llm_ms = metrics.llm_ms
            row.llm_ttft_ms = metrics.ttft_ms
            row.input_tokens = metrics.input_tokens
            row.output_tokens = metrics.output_tokens
            row.tool_calls_json = json.dumps(metrics.tool_calls)
            row.validation_errors = metrics.invalid_tool_calls
        async with self.c.session_maker() as session, session.begin():
            session.add(row)

    # ------------------------------------------------------------------ public turn API

    async def handle_turn(
        self,
        text: str,
        *,
        interrupted: bool = False,
        stt_ms: Optional[int] = None,
        tts_ms: Optional[int] = None,
        caller_phone: Optional[str] = None,
    ) -> TurnResult:
        t0 = time.perf_counter()
        metrics = TurnMetrics()
        with tracer.start_as_current_span("agent.turn") as span:
            span.set_attribute("call.id", self.call_id)
            span.set_attribute("prompt.version", PROMPT_VERSION)
            record_client_span("stt", stt_ms, **{"call.id": self.call_id})
            record_client_span("tts", tts_ms, **{"call.id": self.call_id})
            if self.state.ended:
                return await self._finish("This call has ended.", metrics, t0)
            await self._record_turn("caller", text, stt_ms=stt_ms, tts_ms=tts_ms)
            self.state.silence_count = 0
            self.state.messages.append({"role": "user", "content": text})

            if self.state.pending == "consent":
                speech = await self._handle_consent_reply(text, interrupted)
            else:
                speech = await self._llm_turn(metrics, caller_phone)

            if metrics.low_confidence:
                self.state.low_confidence_streak += 1
            elif metrics.valid_tool_calls:
                self.state.low_confidence_streak = 0
            if self.state.low_confidence_streak >= LOW_CONFIDENCE_HANDOFF and not self.state.handoff_reason:
                self.state.handoff_reason = "low_confidence"
            if self.state.handoff_reason:
                speech = HANDOFF_LINE
            span.set_attribute("llm.model", metrics.model or "none")
            span.set_attribute("tool.calls.invalid", metrics.invalid_tool_calls)
            return await self._finish(speech, metrics, t0)

    async def handle_silence(self) -> TurnResult:
        t0 = time.perf_counter()
        self.state.silence_count += 1
        if self.state.silence_count >= 2:
            self.state.ended = True
            speech = SILENCE_GOODBYE
        elif self.state.pending == "consent" and self.state.consent_prompt:
            speech = self.state.consent_prompt
        else:
            speech = SILENCE_REPROMPT
        return await self._finish(speech, TurnMetrics(), t0)

    async def _finish(self, speech: str, metrics: TurnMetrics, t0: float, append: bool = True) -> TurnResult:
        latency = int((time.perf_counter() - t0) * 1000)
        if append:
            last = self.state.messages[-1] if self.state.messages else None
            if last is not None and last["role"] == "assistant" and not last.get("tool_calls"):
                last["content"] = speech  # keep roles alternating; the spoken text is authoritative
            else:
                self.state.messages.append({"role": "assistant", "content": speech})
        self._trim_history()
        await self._record_turn("agent", speech, metrics, latency_ms=latency)
        await self._save()
        return TurnResult(
            speech=speech,
            order=await self._order_snapshot(),
            ended=self.state.ended,
            handoff_reason=self.state.handoff_reason,
            awaiting=self.state.pending,
            metrics=metrics,
            latency_ms=latency,
        )

    def _trim_history(self) -> None:
        msgs = self.state.messages
        if len(msgs) <= MAX_HISTORY:
            return
        cut = len(msgs) - MAX_HISTORY
        while cut < len(msgs) and msgs[cut]["role"] != "user":
            cut += 1
        self.state.messages = msgs[cut:]

    async def _order_snapshot(self) -> Optional[dict[str, Any]]:
        if not self.state.order_id:
            return None
        async with self.c.session_maker() as session:
            order = await session.get(OrderModel, self.state.order_id)
            if order is None:
                return None
            return {
                "id": order.id,
                "state": order.state,
                "total_cents": order.total_cents,
                "subtotal_cents": order.subtotal_cents,
                "tax_cents": order.tax_cents,
                "currency": order.currency,
                "approval_url": order.approval_url,
                "lines": [{"name": line.name, "qty": line.qty, "line_total_cents": line.line_total_cents,
                           "modifiers": [m["name"] for m in json.loads(line.modifiers_json or "[]")]}
                          for line in order.lines],
            }

    # ------------------------------------------------------------------ consent

    async def _handle_consent_reply(self, text: str, interrupted: bool) -> str:
        prompt = self.state.consent_prompt or ""
        if interrupted:
            # The caller spoke over the confirmation sentence: they may not have heard the amount.
            return prompt
        decision = classify_consent(text)
        assert self.state.order_id and self.state.quote_hash and self.state.total_cents is not None
        async with self.c.session_maker() as session, session.begin():
            await OrderRepository(session).record_consent(
                call_id=self.call_id, order_id=self.state.order_id, quote_hash=self.state.quote_hash,
                amount_cents=self.state.total_cents, prompt_text=prompt, transcript=text, decision=decision,
            )
        if decision == ConsentDecision.YES:
            self.state.pending = None
            result = await self.c.payments.charge_saved_payment(self.state.order_id, self.state.quote_hash,
                                                                call_id=self.call_id)
            if result.ok:
                return (f"Done, {format_cents(self.state.total_cents)} was charged to your saved PayPal. "
                        "The kitchen has your order now. Thanks for calling!")
            if result.state == "PAYMENT_PENDING":
                return "PayPal is still processing that payment. We'll start your order as soon as it clears."
            return f"I couldn't charge your saved PayPal: {result.reason}. Would you like a payment link by text instead?"
        if decision == ConsentDecision.NO:
            self.state.pending = None
            return CONSENT_DECLINED
        self.state.consent_attempts += 1
        if self.state.consent_attempts >= MAX_CONSENT_ATTEMPTS:
            self.state.pending = None
            return CONSENT_GIVE_UP
        return "Sorry, I need a clear yes or no. " + prompt

    # ------------------------------------------------------------------ LLM loop

    async def _system_prompt(self) -> str:
        async with self.c.session_maker() as session:
            shop = await session.get(Shop, self.state.shop_id)
            menu = await OrderRepository(session).load_menu(self.state.shop_id)
        lines = []
        for item in menu.values():
            if not item.available:
                continue
            mods = "; ".join(f"{m.id} {m.name} +{format_cents(m.price_delta_cents)}" for m in item.modifiers)
            lines.append(f"{item.id} | {item.name} | {format_cents(item.price_cents)} | {mods or '-'}")
        return SYSTEM_PROMPT.format(
            shop_name=shop.name if shop else "the restaurant",
            menu="\n".join(lines) or "(menu is empty)",
            state=json.dumps(self.state.public_view()),
        )

    async def _llm_turn(self, metrics: TurnMetrics, caller_phone: Optional[str]) -> str:
        if self.llm is None:
            raise LLMUnavailableError(LLM_UNAVAILABLE)
        tools = AgentTools(self.c, self.call_id, self.state, caller_phone=caller_phone)
        tools_disabled = False
        for _ in range(MAX_TOOL_STEPS):
            system = await self._system_prompt()
            with tracer.start_as_current_span("llm.complete") as span:
                result = await self.llm.complete(
                    system=system, messages=self.state.messages, tools=TOOL_SPECS,
                    tool_choice="none" if tools_disabled else None,
                )
                span.set_attribute("llm.model", result.model)
                span.set_attribute("llm.ttft_ms", result.ttft_ms or -1)
                span.set_attribute("llm.input_tokens", result.input_tokens)
                span.set_attribute("llm.output_tokens", result.output_tokens)
            metrics.model = result.model
            metrics.llm_ms += result.latency_ms
            metrics.ttft_ms = metrics.ttft_ms if metrics.ttft_ms is not None else result.ttft_ms
            metrics.input_tokens += result.input_tokens
            metrics.output_tokens += result.output_tokens

            calls = [] if tools_disabled else result.tool_calls
            self.state.messages.append({
                "role": "assistant",
                "content": result.text,
                "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls],
            })
            if not calls:
                return result.text or "Sorry, could you say that again?"

            verbatim: Optional[str] = None
            for call in calls:
                entry: dict[str, Any] = {"name": call.name}
                spec = TOOL_MODELS.get(call.name)
                if spec is None or call.raw_arguments is not None:
                    error = f"Unknown tool {call.name}" if spec is None else "Arguments were not valid JSON"
                    content, is_error = json.dumps({"error": error, "instruction": "Ask the caller instead."}), True
                    metrics.invalid_tool_calls += 1
                    metrics.low_confidence = True
                    tools_disabled = True
                    entry["valid"] = False
                else:
                    try:
                        args = spec[0].model_validate(call.arguments)
                    except ValidationError as exc:
                        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
                        content = json.dumps({"error": f"Invalid arguments: {problems}",
                                              "instruction": "Do not guess. Ask the caller to clarify."})
                        is_error = True
                        metrics.invalid_tool_calls += 1
                        metrics.low_confidence = True
                        tools_disabled = True
                        entry["valid"] = False
                    else:
                        with tracer.start_as_current_span(f"tool.{call.name}"):
                            outcome = await tools.run(call.name, args)
                        content, is_error = json.dumps(outcome.data, default=str), not outcome.ok
                        metrics.valid_tool_calls += 1
                        metrics.low_confidence = metrics.low_confidence or outcome.low_confidence
                        verbatim = outcome.speak_verbatim or verbatim
                        entry.update(valid=True, ok=outcome.ok)
                metrics.tool_calls.append(entry)
                self.state.messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                            "content": content, "is_error": is_error})
            if verbatim:
                return verbatim  # the system, not the model, says the consent sentence
            if self.state.handoff_reason or self.state.ended:
                final = await self.llm.complete(system=await self._system_prompt(), messages=self.state.messages,
                                                tools=TOOL_SPECS, tool_choice="none")
                metrics.llm_ms += final.latency_ms
                metrics.input_tokens += final.input_tokens
                metrics.output_tokens += final.output_tokens
                return final.text or "Goodbye!"
        tools_disabled = True
        final = await self.llm.complete(system=await self._system_prompt(), messages=self.state.messages,
                                        tools=TOOL_SPECS, tool_choice="none")
        metrics.llm_ms += final.latency_ms
        return final.text or "Sorry, could you say that again?"
