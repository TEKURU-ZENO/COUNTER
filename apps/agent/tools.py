"""
Typed tools the LLM may call. The LLM's only job is to choose a tool and fill these arguments;
every argument model forbids extra fields, so "price", "discount" or "amount" arguments are
rejected before any code runs. Money-moving decisions happen in PaymentService and the Guard.

There is deliberately no tool that charges a saved wallet: that only happens after the caller's
own reply to the verbatim consent sentence is classified YES and stored (see session.py).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.agent import catalog_match
from apps.agent.llm import ToolSpec
from apps.api.db.models import CallModel, ToolInvocationModel
from apps.api.db.repository import OrderRepository
from apps.core import identity
from apps.core.container import Container
from packages.domain.consent import consent_prompt, quote_short_code
from packages.domain.guard import Guard
from packages.domain.models import CartItem, GuardDecisionKind, ModifierSelection, OrderState
from packages.domain.pricing import PricingError, calculate_quote, format_cents

# ---------------------------------------------------------------------- argument models

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "a": 1, "an": 1, "single": 1, "couple": 2, "pair": 2, "dozen": 12,
}
_AMBIGUOUS_QTY = {"to", "too", "for", "fore", "won", "tree", "ate"}


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CartLine(_Args):
    item: str = Field(min_length=1, max_length=120, description="Menu item id, or the caller's words for it")
    qty: int = Field(ge=1, le=20, description="How many. Ask if unsure.")
    modifiers: list[str] = Field(default_factory=list, max_length=6, description="Modifier ids or names")

    @field_validator("qty", mode="before")
    @classmethod
    def _qty_words(cls, v: Any) -> Any:
        if isinstance(v, str):
            word = v.strip().lower()
            if word in _AMBIGUOUS_QTY:
                raise ValueError(f"'{v}' is not a clear quantity; ask the caller how many")
            if word in _NUMBER_WORDS:
                return _NUMBER_WORDS[word]
        return v


class SearchMenuArgs(_Args):
    query: str = Field(min_length=1, max_length=120)


class SetCartArgs(_Args):
    items: list[CartLine] = Field(min_length=1, max_length=15, description="The complete cart; replaces any previous cart")


class SendPaymentLinkArgs(_Args):
    quote_hash: str = Field(pattern=r"^[0-9a-f]{64}$", description="quote_hash from the current call state")
    save_paypal_for_next_time: bool = Field(
        default=False, description="Only true if the caller explicitly asked to save PayPal for future phone orders"
    )


class UseSavedPayPalArgs(_Args):
    quote_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class VerifySmsCodeArgs(_Args):
    code: str = Field(description="The six digits the caller read out")

    @field_validator("code")
    @classmethod
    def _six_digits(cls, v: str) -> str:
        digits = re.sub(r"[^0-9]", "", v)
        if len(digits) != 6:
            raise ValueError("code must be exactly six digits; ask the caller to repeat it")
        return digits


class CancelOrderArgs(_Args):
    pass


class TransferArgs(_Args):
    reason: str = Field(min_length=1, max_length=200)


class EndCallArgs(_Args):
    pass


TOOL_MODELS: dict[str, tuple[type[_Args], str]] = {
    "search_menu": (SearchMenuArgs, "Look up menu items matching the caller's words."),
    "set_cart": (SetCartArgs, "Set the complete cart and get the priced quote and read-back."),
    "send_payment_link": (SendPaymentLinkArgs, "Text the caller a PayPal payment link for the current quote."),
    "use_saved_paypal": (UseSavedPayPalArgs, "Start paying with the caller's saved PayPal (needs an SMS code)."),
    "verify_sms_code": (VerifySmsCodeArgs, "Check the six-digit code the caller read back."),
    "cancel_order": (CancelOrderArgs, "Cancel this caller's latest order; refunds follow shop policy."),
    "transfer_to_human": (TransferArgs, "Hand the call to restaurant staff."),
    "end_call": (EndCallArgs, "End the call after saying goodbye."),
}


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


TOOL_SPECS = [ToolSpec(name, desc, _schema(model)) for name, (model, desc) in TOOL_MODELS.items()]


# ---------------------------------------------------------------------- call state


@dataclass
class CallState:
    shop_id: str
    caller_hash: str
    channel: str = "web"
    messages: list[dict[str, Any]] = field(default_factory=list)
    order_id: str | None = None
    quote_hash: str | None = None
    total_cents: int | None = None
    readback: str | None = None
    has_saved_paypal: bool = False
    pending: str | None = None  # None | "otp" | "consent"
    consent_prompt: str | None = None
    consent_attempts: int = 0
    low_confidence_streak: int = 0
    silence_count: int = 0
    ended: bool = False
    handoff_reason: str | None = None
    approval_url: str | None = None
    turn_seq: int = 0
    tool_seq: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> CallState:
        return cls(**json.loads(raw))

    def public_view(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "quote_hash": self.quote_hash,
            "quote_short_code": quote_short_code(self.quote_hash) if self.quote_hash else None,
            "total": format_cents(self.total_cents) if self.total_cents is not None else None,
            "readback": self.readback,
            "caller_has_saved_paypal": self.has_saved_paypal,
            "awaiting": self.pending,
        }


@dataclass
class ToolOutcome:
    ok: bool
    data: dict[str, Any]
    low_confidence: bool = False
    speak_verbatim: str | None = None


# ---------------------------------------------------------------------- tool implementations


class AgentTools:
    def __init__(self, container: Container, call_id: str, state: CallState, caller_phone: str | None = None):
        self.c = container
        self.call_id = call_id
        self.state = state
        # The raw number is held only for this request (to send SMS); it is never persisted.
        self.caller_phone = caller_phone
        self.guard = Guard()

    async def run(self, name: str, args: _Args) -> ToolOutcome:
        t0 = time.perf_counter()
        outcome = await getattr(self, f"_{name}")(args)
        await self._log(name, args, outcome, int((time.perf_counter() - t0) * 1000))
        return outcome

    async def _log(self, name: str, args: _Args, outcome: ToolOutcome, latency_ms: int) -> None:
        self.state.tool_seq += 1
        safe_args = args.model_dump()
        if name == "verify_sms_code":
            safe_args = {"code": "******"}
        async with self.c.session_maker() as session, session.begin():
            session.add(
                ToolInvocationModel(
                    call_id=self.call_id,
                    seq=self.state.tool_seq,
                    tool=name,
                    args_json=json.dumps(safe_args),
                    guard_decision="allow" if outcome.ok else "deny",
                    reason=str(outcome.data.get("error", ""))[:500],
                    result_json=json.dumps(outcome.data, default=str)[:4000],
                    latency_ms=latency_ms,
                )
            )

    # -- menu

    async def _search_menu(self, args: SearchMenuArgs) -> ToolOutcome:
        async with self.c.session_maker() as session:
            menu = await OrderRepository(session).load_menu(self.state.shop_id)
        options = [(m.id, m.name) for m in menu.values() if m.available]
        ranked = catalog_match.rank(args.query, options, limit=5)
        results = [
            {"item_id": c.id, "name": c.name, "price": format_cents(menu[c.id].price_cents), "confidence": c.score}
            for c in ranked
            if c.score >= catalog_match.SUGGEST
        ]
        return ToolOutcome(True, {"results": results, "note": "Nothing similar on the menu" if not results else ""})

    async def _set_cart(self, args: SetCartArgs) -> ToolOutcome:
        if self.state.pending in ("otp", "consent") or self._order_locked():
            return ToolOutcome(False, {"error": "The order is already being paid; it can't be changed now."})
        async with self.c.session_maker() as session:
            menu = await OrderRepository(session).load_menu(self.state.shop_id)
        options = [(m.id, m.name) for m in menu.values() if m.available]
        cart: list[CartItem] = []
        confirm: list[dict[str, Any]] = []
        unknown: list[str] = []
        for line in args.items:
            hit, candidates = catalog_match.resolve(line.item, options)
            if hit is None:
                if candidates:
                    confirm.append({"heard": line.item, "did_you_mean": [c.name for c in candidates],
                                    "item_ids": [c.id for c in candidates]})
                else:
                    unknown.append(line.item)
                continue
            item = menu[hit.id]
            mod_options = [(m.id, m.name) for m in item.modifiers]
            selections = []
            for spoken_mod in line.modifiers:
                mod_hit, mod_candidates = catalog_match.resolve(spoken_mod, mod_options)
                if mod_hit is None:
                    if mod_candidates:
                        confirm.append({"heard": spoken_mod, "for_item": item.name,
                                        "did_you_mean": [c.name for c in mod_candidates]})
                    else:
                        unknown.append(f"{spoken_mod} (option for {item.name})")
                    continue
                definition = next(m for m in item.modifiers if m.id == mod_hit.id)
                selections.append(ModifierSelection(definition.id, definition.name, definition.price_delta_cents))
            cart.append(CartItem(item.id, item.name, line.qty, tuple(selections)))

        if unknown or confirm:
            return ToolOutcome(
                False,
                {"error": "Some items need the caller's confirmation before pricing",
                 "not_on_menu": unknown, "needs_confirmation": confirm,
                 "instruction": "Ask the caller; then call set_cart again with item ids."},
                low_confidence=True,
            )
        try:
            quote = calculate_quote(self.state.shop_id, cart, menu, tax_rate_bps=self.c.settings.tax_rate_bps)
        except PricingError as exc:
            return ToolOutcome(False, {"error": str(exc)})
        decision = self.guard.evaluate_quote(quote)
        if decision.kind == GuardDecisionKind.NEEDS_FLOW:
            return ToolOutcome(False, {"error": "This is a catering-size order; transfer to staff for a deposit invoice."})
        if not decision.allowed:
            return ToolOutcome(False, {"error": decision.reason})

        async with self.c.session_maker() as session, session.begin():
            repo = OrderRepository(session)
            order = await repo.get_order(self.state.order_id) if self.state.order_id else None
            if order is None or OrderState(order.state) not in (OrderState.DRAFT, OrderState.QUOTED):
                order = await repo.create_order(
                    order_id=f"ord_{uuid.uuid4().hex[:12]}", shop_id=self.state.shop_id, call_id=self.call_id,
                    caller_hash=self.state.caller_hash, currency=self.c.settings.currency,
                )
            await repo.apply_quote(order, quote)
            self.state.order_id = order.id

        self.state.quote_hash = quote.quote_hash
        self.state.total_cents = quote.total_cents
        self.state.readback = quote.spoken_summary
        self.state.consent_attempts = 0
        return ToolOutcome(True, {
            "order_id": self.state.order_id,
            "readback": quote.spoken_summary,
            "total": format_cents(quote.total_cents),
            "quote_hash": quote.quote_hash,
            "caller_has_saved_paypal": self.state.has_saved_paypal,
        })

    def _order_locked(self) -> bool:
        return self.state.approval_url is not None

    # -- payment

    async def _send_payment_link(self, args: SendPaymentLinkArgs) -> ToolOutcome:
        if not self.state.order_id or args.quote_hash != self.state.quote_hash:
            return ToolOutcome(False, {"error": "quote_hash does not match the current quote; read back the order again"})
        result = await self.c.payments.send_payment_link(
            self.state.order_id, args.quote_hash, vault_opt_in=args.save_paypal_for_next_time
        )
        if not result.ok:
            return ToolOutcome(False, {"error": result.reason})
        url = result.data["approval_url"]
        self.state.approval_url = url
        delivered = "shown on screen"
        if self.state.channel == "phone" and self.caller_phone:
            await self.c.sms.send(self.caller_phone, f"Pay for your order here: {url}",
                                  inbox_key=self.state.caller_hash)
            delivered = "texted"
        return ToolOutcome(True, {"status": "link_sent", "delivered": delivered,
                                  "say": "Tell the caller the link was sent and the kitchen starts once PayPal confirms."})

    async def _use_saved_paypal(self, args: UseSavedPayPalArgs) -> ToolOutcome:
        if not self.state.order_id or args.quote_hash != self.state.quote_hash or self.state.total_cents is None:
            return ToolOutcome(False, {"error": "quote_hash does not match the current quote"})
        async with self.c.session_maker() as session, session.begin():
            repo = OrderRepository(session)
            mandate = await repo.active_mandate(self.state.caller_hash, self.state.shop_id)
            if mandate is None:
                return ToolOutcome(False, {"error": "No saved PayPal for this caller; offer a payment link."})
            if self.state.total_cents > mandate.max_charge_cents:
                return ToolOutcome(False, {"error": f"Total is above the saved-PayPal limit of "
                                                    f"{format_cents(mandate.max_charge_cents)}; offer a payment link."})
            if await repo.verified_step_up(self.call_id, mandate.id) is None:
                code = identity.new_otp()
                key = self.c.settings.secret_key.get_secret_value()
                await repo.create_challenge(call_id=self.call_id, mandate_id=mandate.id,
                                            code_hash=identity.otp_hash(code, key, self.call_id),
                                            ttl_seconds=self.c.settings.otp_ttl_seconds)
                mandate_id = mandate.id
            else:
                mandate_id = None
        if mandate_id is not None:
            try:
                await self.c.sms.send(self.caller_phone or "",
                                      f"Your order confirmation code is {code}. Never share it with anyone else.",
                                      inbox_key=self.state.caller_hash)
            except RuntimeError:
                return ToolOutcome(False, {"error": "Can't text a code right now; offer a payment link."})
            self.state.pending = "otp"
            return ToolOutcome(True, {"status": "code_sent",
                                      "say": "Ask the caller for the six-digit code texted to the number on file."})
        return self._arm_consent()

    async def _verify_sms_code(self, args: VerifySmsCodeArgs) -> ToolOutcome:
        if self.state.pending != "otp":
            return ToolOutcome(False, {"error": "No code is expected right now."})
        key = self.c.settings.secret_key.get_secret_value()
        async with self.c.session_maker() as session, session.begin():
            repo = OrderRepository(session)
            mandate = await repo.active_mandate(self.state.caller_hash, self.state.shop_id)
            challenge = await repo.open_challenge(self.call_id, mandate.id) if mandate else None
            if challenge is None:
                self.state.pending = None
                return ToolOutcome(False, {"error": "No active code; offer a payment link."})
            from apps.api.db.models import utc_now

            if challenge.expires_at < utc_now() or challenge.attempts >= self.c.settings.otp_max_attempts:
                self.state.pending = None
                return ToolOutcome(False, {"error": "The code expired or too many attempts; offer a payment link."})
            challenge.attempts += 1
            if not identity.otp_matches(args.code, challenge.code_hash, key, self.call_id):
                left = self.c.settings.otp_max_attempts - challenge.attempts
                return ToolOutcome(False, {"error": f"That code didn't match; {left} attempts left."}, low_confidence=True)
            challenge.verified_at = utc_now()
        return self._arm_consent()

    def _arm_consent(self) -> ToolOutcome:
        assert self.state.total_cents is not None and self.state.quote_hash is not None
        prompt = consent_prompt(self.state.total_cents, self.state.quote_hash)
        self.state.pending = "consent"
        self.state.consent_prompt = prompt
        self.state.consent_attempts = 0
        return ToolOutcome(True, {"status": "awaiting_spoken_consent"}, speak_verbatim=prompt)

    # -- cancellation

    async def _cancel_order(self, args: CancelOrderArgs) -> ToolOutcome:
        async with self.c.session_maker() as session:
            repo = OrderRepository(session)
            order = await repo.get_order(self.state.order_id) if self.state.order_id else None
            if order is None:
                order = await repo.latest_order_for_caller(self.state.caller_hash, self.state.shop_id)
            if order is None:
                return ToolOutcome(False, {"error": "I can't find an order from this number."})
            order_id, state = order.id, OrderState(order.state)
        if state in (OrderState.DRAFT, OrderState.QUOTED, OrderState.LINK_SENT):
            res = await self.c.payments.cancel_unpaid(order_id, actor=_agent())
            self.state.pending = None
            return ToolOutcome(res.ok, {"status": "cancelled" if res.ok else "not_cancelled", "error": "" if res.ok else res.reason})
        res = await self.c.payments.caller_refund(order_id, caller_hash=self.state.caller_hash)
        if res.data.get("needs_approval"):
            return ToolOutcome(True, {"status": "sent_to_owner_for_approval",
                                      "say": "Tell the caller the owner must approve this refund and will follow up."})
        if not res.ok:
            return ToolOutcome(False, {"error": res.reason})
        return ToolOutcome(True, {"status": "refunded", "amount": format_cents(res.data.get("refund_cents", 0))})

    # -- call control

    async def _transfer_to_human(self, args: TransferArgs) -> ToolOutcome:
        self.state.handoff_reason = args.reason
        return ToolOutcome(True, {"status": "transferring"})

    async def _end_call(self, args: EndCallArgs) -> ToolOutcome:
        self.state.ended = True
        return ToolOutcome(True, {"status": "ending"})


def _agent():  # small indirection to avoid importing ActorType at module top in two places
    from packages.domain.models import ActorType

    return ActorType.AGENT


async def load_call(container: Container, call_id: str) -> tuple[CallModel, CallState] | None:
    async with container.session_maker() as session:
        call = await session.get(CallModel, call_id)
        if call is None:
            return None
        return call, CallState.from_json(call.state_json)
