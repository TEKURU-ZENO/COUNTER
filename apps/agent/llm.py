"""
Provider-neutral tool-calling LLM interface.

Conversation history is stored in a neutral format so calls can be persisted between HTTP turns
and replayed against different models in evaluation:

    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments"}]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str, "is_error": bool}

Adapters translate that to Anthropic Messages or OpenAI-compatible chat completions. Time to
first token is measured from the stream, not estimated.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from apps.core.settings import LLMProvider, Settings


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str | None = None  # set when the model emitted unparseable JSON


@dataclass
class LLMResult:
    text: str
    tool_calls: list[ToolCall]
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    ttft_ms: int | None
    stop_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    model: str
    provider: str

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolSpec],
        max_tokens: int = 400,
        temperature: float = 0.2,
        tool_choice: str | None = None,
    ) -> LLMResult: ...


# ---------------------------------------------------------------------- Anthropic


def to_anthropic_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg["role"]
        if role == "user":
            out.append({"role": "user", "content": [{"type": "text", "text": msg["content"]}]})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for call in msg.get("tool_calls", []) or []:
                blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["arguments"]})
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": msg["tool_call_id"],
                "content": msg["content"],
                "is_error": bool(msg.get("is_error")),
            }
            if out and out[-1]["role"] == "user" and all(b.get("type") == "tool_result" for b in out[-1]["content"]):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, api_key: str, model: str):
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)
        self.model = model

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolSpec],
        max_tokens: int = 400,
        temperature: float = 0.2,
        tool_choice: str | None = None,
    ) -> LLMResult:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": to_anthropic_messages(messages),
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
            if tool_choice == "none":
                kwargs["tool_choice"] = {"type": "none"}
            elif tool_choice:
                kwargs["tool_choice"] = {"type": "tool", "name": tool_choice}
        t0 = time.perf_counter()
        ttft: int | None = None
        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if ttft is None and getattr(event, "type", "") in ("content_block_start", "content_block_delta", "text"):
                    ttft = int((time.perf_counter() - t0) * 1000)
            final = await stream.get_final_message()
        latency = int((time.perf_counter() - t0) * 1000)
        text_parts, calls = [], []
        for block in final.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
        return LLMResult(
            text="".join(text_parts).strip(),
            tool_calls=calls,
            model=final.model,
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
            latency_ms=latency,
            ttft_ms=ttft,
            stop_reason=final.stop_reason,
        )


# ---------------------------------------------------------------------- OpenAI-compatible


def to_openai_messages(system: str, messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for msg in messages:
        if msg["role"] == "user":
            out.append({"role": "user", "content": msg["content"]})
        elif msg["role"] == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or None}
            if msg.get("tool_calls"):
                entry["tool_calls"] = [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                    for c in msg["tool_calls"]
                ]
            out.append(entry)
        elif msg["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": msg["tool_call_id"], "content": msg["content"]})
    return out


class OpenAICompatibleLLM:
    """Works with OpenAI, Groq and other chat-completions-compatible endpoints."""

    provider = "openai_compatible"

    def __init__(self, api_key: str, model: str, base_url: str, http: httpx.AsyncClient):
        self._key = api_key
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._http = http

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolSpec],
        max_tokens: int = 400,
        temperature: float = 0.2,
        tool_choice: str | None = None,
    ) -> LLMResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(system, messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
            if tool_choice == "none":
                body["tool_choice"] = "none"
            elif tool_choice:
                body["tool_choice"] = {"type": "function", "function": {"name": tool_choice}}
        t0 = time.perf_counter()
        ttft: int | None = None
        text: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        model = self.model
        finish: str | None = None
        async with self._http.stream(
            "POST", self._url, json=body, headers={"Authorization": f"Bearer {self._key}"}, timeout=60.0
        ) as resp:
            if resp.status_code >= 400:
                detail = (await resp.aread())[:300]
                raise RuntimeError(f"LLM HTTP {resp.status_code}: {detail!r}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                chunk = json.loads(line[6:])
                model = chunk.get("model", model)
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    if ttft is None and (delta.get("content") or delta.get("tool_calls")):
                        ttft = int((time.perf_counter() - t0) * 1000)
                    if delta.get("content"):
                        text.append(delta["content"])
                    for tc in delta.get("tool_calls", []) or []:
                        slot = calls.setdefault(tc["index"], {"id": "", "name": "", "args": ""})
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function", {})
                        slot["name"] = fn.get("name") or slot["name"]
                        slot["args"] += fn.get("arguments") or ""
                    finish = choice.get("finish_reason") or finish
        tool_calls = []
        for slot in calls.values():
            try:
                args = json.loads(slot["args"] or "{}")
                tool_calls.append(ToolCall(slot["id"], slot["name"], args if isinstance(args, dict) else {}))
            except ValueError:
                tool_calls.append(ToolCall(slot["id"], slot["name"], {}, raw_arguments=slot["args"]))
        return LLMResult(
            text="".join(text).strip(),
            tool_calls=tool_calls,
            model=model,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            ttft_ms=ttft,
            stop_reason=finish,
        )


def build_llm(settings: Settings, http: httpx.AsyncClient, model: str | None = None) -> LLMClient | None:
    """Returns None when no provider is configured. Nothing falls back to keyword matching."""
    chosen = model or settings.llm_model
    if settings.llm_provider is None or not chosen:
        return None
    if settings.llm_provider == LLMProvider.ANTHROPIC:
        if not settings.anthropic_api_key:
            return None
        return AnthropicLLM(settings.anthropic_api_key.get_secret_value(), chosen)
    if not settings.openai_api_key:
        return None
    return OpenAICompatibleLLM(settings.openai_api_key.get_secret_value(), chosen, settings.openai_base_url, http)


class ScriptedLLM:
    """Deterministic stand-in for tests: returns pre-written results in order.

    Used to test the orchestration and, in the compromised-model suite, to play a model that
    does whatever an attacker says. It is never used to produce reported AI-quality numbers.
    """

    provider = "scripted"

    def __init__(self, script: Sequence[LLMResult | Any], model: str = "scripted"):
        self._script = list(script)
        self.model = model
        self.requests: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> LLMResult:
        self.requests.append(kwargs)
        if not self._script:
            return LLMResult("Sorry, could you say that again?", [], self.model, 0, 0, 0, 0)
        step = self._script.pop(0)
        return step(kwargs) if callable(step) else step


def say(text: str) -> LLMResult:
    return LLMResult(text, [], "scripted", 0, 0, 0, 0)


def call(name: str, args: dict[str, Any], call_id: str | None = None, text: str = "") -> LLMResult:
    return LLMResult(text, [ToolCall(call_id or f"call_{name}_{id(args)}", name, args)], "scripted", 0, 0, 0, 0)
