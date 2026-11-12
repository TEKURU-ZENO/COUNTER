"""
Ops copilot: an LLM with read-only tools over the ledger and PayPal reporting.

Grounding rule enforced in code, not just in the prompt: every number in the final answer must
appear in a tool result from this request (as a count, cents, or formatted amount). If any figure
cannot be traced, the answer is withheld and the raw tool results are returned instead.
Refunds are reported from the refunds table, never from order totals.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select

from apps.agent.llm import ToolSpec
from apps.api.db.models import OrderLineModel, OrderModel, PaymentModel, RefundModel
from apps.core.container import Container
from packages.domain.models import PAID_STATES
from packages.domain.money import MoneyError, value_to_cents
from packages.domain.pricing import format_cents

MAX_STEPS = 5


class CopilotUnavailable(RuntimeError):
    pass


class DateRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: str = Field(description="YYYY-MM-DD, inclusive")
    end_date: str = Field(description="YYYY-MM-DD, inclusive")

    @field_validator("start_date", "end_date")
    @classmethod
    def _iso(cls, v: str) -> str:
        date.fromisoformat(v)
        return v

    def bounds(self) -> tuple[datetime, datetime]:
        start = datetime.combine(date.fromisoformat(self.start_date), datetime.min.time())
        end = datetime.combine(date.fromisoformat(self.end_date), datetime.min.time()) + timedelta(days=1)
        if end <= start or end - start > timedelta(days=366):
            raise ValueError("Date range must be positive and at most one year")
        return start, end


class TopItemsArgs(DateRange):
    limit: int = Field(default=5, ge=1, le=20)


TOOLS: dict[str, tuple[type[BaseModel], str]] = {
    "ledger_summary": (DateRange, "Captured payments, refunds (from the refunds table), fees and net, in cents."),
    "orders_by_state": (DateRange, "Count of orders created in the range, grouped by state."),
    "top_items": (TopItemsArgs, "Best-selling items among paid orders: quantity and revenue in cents."),
    "paypal_transactions": (DateRange, "PayPal's own Transaction Search totals for the range (may lag ~3h)."),
}
SPECS = [ToolSpec(n, d, {k: v for k, v in m.model_json_schema().items() if k != "title"}) for n, (m, d) in TOOLS.items()]

SYSTEM = """You answer a restaurant owner's questions about their orders and money.
Today is {today}. Use the tools to get data; they are read-only. Amounts in tool results are
integer cents. When you state a money amount, write it in dollars with two decimals (e.g. 1450 cents -> $14.50).
Only state figures that appear in tool results; do not compute new figures such as averages or
percentages. If the tools cannot answer the question, say so plainly and suggest what data is missing.
Answer in at most three sentences."""


async def _ledger_summary(c: Container, args: DateRange) -> dict[str, Any]:
    start, end = args.bounds()
    async with c.session_maker() as s:
        paid = (await s.execute(select(func.count(PaymentModel.id), func.coalesce(func.sum(PaymentModel.amount_cents), 0),
                                       func.coalesce(func.sum(PaymentModel.fee_cents), 0))
                                .where(PaymentModel.status == "COMPLETED", PaymentModel.created_at >= start,
                                       PaymentModel.created_at < end))).one()
        refunds = (await s.execute(select(func.count(RefundModel.id), func.coalesce(func.sum(RefundModel.amount_cents), 0))
                                   .where(RefundModel.status == "COMPLETED", RefundModel.created_at >= start,
                                          RefundModel.created_at < end))).one()
    captured_cents, refunded_cents = int(paid[1]), int(refunds[1])
    return {"captured_count": int(paid[0]), "captured_cents": captured_cents, "paypal_fees_cents": int(paid[2]),
            "refund_count": int(refunds[0]), "refunded_cents": refunded_cents,
            "net_after_refunds_cents": captured_cents - refunded_cents}


async def _orders_by_state(c: Container, args: DateRange) -> dict[str, Any]:
    start, end = args.bounds()
    async with c.session_maker() as s:
        rows = (await s.execute(select(OrderModel.state, func.count(OrderModel.id))
                                .where(OrderModel.created_at >= start, OrderModel.created_at < end)
                                .group_by(OrderModel.state))).all()
    return {"orders_by_state": {state: int(n) for state, n in rows}}


async def _top_items(c: Container, args: TopItemsArgs) -> dict[str, Any]:
    start, end = args.bounds()
    async with c.session_maker() as s:
        rows = (await s.execute(
            select(OrderLineModel.name, func.sum(OrderLineModel.qty), func.sum(OrderLineModel.line_total_cents))
            .join(OrderModel, OrderModel.id == OrderLineModel.order_id)
            .where(OrderModel.state.in_([st.value for st in PAID_STATES]),
                   OrderModel.created_at >= start, OrderModel.created_at < end)
            .group_by(OrderLineModel.name).order_by(func.sum(OrderLineModel.qty).desc()).limit(args.limit))).all()
    return {"items": [{"name": n, "qty": int(q), "revenue_cents": int(r)} for n, q, r in rows]}


async def _paypal_transactions(c: Container, args: DateRange) -> dict[str, Any]:
    start, end = args.bounds()
    res = await c.paypal.search_transactions(start.strftime("%Y-%m-%dT%H:%M:%S-0000"),
                                             (end - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S-0000"))
    gross = 0
    count = 0
    for d in res.get("transaction_details", []):
        info = d.get("transaction_info", {})
        try:
            cents = value_to_cents(str(info.get("transaction_amount", {}).get("value", "")).lstrip("-"))
        except MoneyError:
            continue
        if str(info.get("transaction_amount", {}).get("value", "")).startswith("-"):
            continue
        gross += cents
        count += 1
    return {"paypal_positive_transaction_count": count, "paypal_gross_cents": gross}


_IMPL = {"ledger_summary": _ledger_summary, "orders_by_state": _orders_by_state, "top_items": _top_items,
         "paypal_transactions": _paypal_transactions}


def _allowed_figures(results: list[dict[str, Any]], question: str) -> set[str]:
    allowed: set[str] = set()

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, bool):
            return
        elif isinstance(value, int):
            allowed.add(str(value))
            allowed.add(f"{value:,}")
            if key.endswith("cents"):
                dollars = format_cents(value).lstrip("-$")
                allowed.update({dollars, f"{value // 100:,}.{value % 100:02d}", str(value // 100) if value % 100 == 0 else ""})

    for r in results:
        walk(r)
    allowed.update(re.findall(r"\d[\d,]*(?:\.\d+)?", question))  # numbers the owner used (dates, limits)
    allowed.discard("")
    return allowed


def figures_in(text: str) -> list[str]:
    return [m.rstrip(".,") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]


async def answer(c: Container, question: str) -> dict[str, Any]:
    if c.llm is None:
        raise CopilotUnavailable("No LLM is configured, so the copilot is unavailable.")
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    trace: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    system = SYSTEM.format(today=date.today().isoformat())
    text = ""
    for _ in range(MAX_STEPS):
        res = await c.llm.complete(system=system, messages=messages, tools=SPECS, max_tokens=500, temperature=0)
        messages.append({"role": "assistant", "content": res.text,
                         "tool_calls": [{"id": t.id, "name": t.name, "arguments": t.arguments} for t in res.tool_calls]})
        if not res.tool_calls:
            text = res.text
            break
        for call in res.tool_calls:
            spec = TOOLS.get(call.name)
            try:
                if spec is None:
                    raise ValueError(f"unknown tool {call.name}")
                args = spec[0].model_validate(call.arguments)
                args.bounds()  # type: ignore[attr-defined]
                out = await _IMPL[call.name](c, args)  # type: ignore[arg-type]
                results.append(out)
                content, err = json.dumps(out), False
            except (ValidationError, ValueError) as exc:
                content, err = json.dumps({"error": str(exc)}), True
                out = {"error": str(exc)}
            trace.append({"tool": call.name, "args": call.arguments, "result": out})
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content, "is_error": err})

    allowed = _allowed_figures(results, question)
    figures = figures_in(text)
    untraced = [f for f in figures if f not in allowed]
    grounded = bool(text) and not untraced
    if not grounded:
        text = ("I couldn't verify every figure in my answer against your data, so I'm not showing it. "
                "The raw results are below." if text else "I couldn't answer that from the available data.")
    return {"answer": text, "grounded": grounded, "untraced_figures": untraced, "trace": trace,
            "model": getattr(c.llm, "model", None)}
