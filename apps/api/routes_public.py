"""Public and customer-facing routes: menu, web calls, PayPal return/webhooks, order status."""

from __future__ import annotations

import html
import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from apps.agent.session import CallSession, LLMUnavailableError, TurnResult
from apps.api.db.models import MenuItemModel, ModifierModel, OrderModel
from apps.api.db.repository import OrderRepository
from apps.api.security import (
    check_call_token,
    check_order_token,
    get_container,
    issue_call_token,
    issue_order_token,
    rate_limit,
    require_service,
)
from apps.api.sse import event_stream
from apps.core.container import Container
from apps.core.settings import PayPalMode
from apps.core.sms import DemoInboxSms
from packages.paypal_gateway import MockPayPalGateway, WebhookHeaders

logger = logging.getLogger("counter.api")
router = APIRouter()


@router.get("/healthz")
async def healthz(c: Container = Depends(get_container)) -> dict[str, Any]:
    return {"ok": True, "paypal_mode": c.settings.paypal_mode.value, "llm": bool(c.llm)}


# ---------------------------------------------------------------------- menu


@router.get("/api/v1/menu", dependencies=[Depends(rate_limit("menu", 120))])
async def get_menu(shop_id: str | None = None, c: Container = Depends(get_container)) -> list[dict[str, Any]]:
    shop_id = shop_id or c.settings.default_shop_id
    async with c.session_maker() as session:
        items = (await session.execute(select(MenuItemModel).where(MenuItemModel.shop_id == shop_id))).scalars().all()
        mods = (await session.execute(select(ModifierModel).where(ModifierModel.item_id.in_([i.id for i in items])))).scalars().all()
    by_item: dict[str, list[dict[str, Any]]] = {}
    for m in mods:
        by_item.setdefault(m.item_id, []).append({"id": m.id, "name": m.name, "price_delta_cents": m.price_delta_cents})
    return [
        {"id": i.id, "name": i.name, "category": i.category, "price_cents": i.price_cents, "available": i.available,
         "dietary_tags": json.loads(i.dietary_tags_json or "[]"), "modifiers": by_item.get(i.id, [])}
        for i in items
    ]


# ---------------------------------------------------------------------- web calls


class StartCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caller_phone: str = Field(min_length=7, max_length=20)
    shop_id: str | None = None


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=1000)
    interrupted: bool = False
    stt_ms: int | None = Field(default=None, ge=0, le=60_000)
    tts_ms: int | None = Field(default=None, ge=0, le=60_000)
    caller_phone: str | None = Field(default=None, max_length=20)


def _turn_payload(c: Container, call_id: str, result: TurnResult) -> dict[str, Any]:
    order = result.order
    if order:
        order = {**order, "status_url": f"/order-status/{order['id']}?t={issue_order_token(c.settings, order['id'])}"}
    return {
        "call_id": call_id,
        "speech": result.speech,
        "order": order,
        "ended": result.ended,
        "handoff": result.handoff_reason,
        "awaiting": result.awaiting,
        "latency_ms": result.latency_ms,
        "llm": {"model": result.metrics.model, "llm_ms": result.metrics.llm_ms, "ttft_ms": result.metrics.ttft_ms,
                "tool_calls": result.metrics.tool_calls},
    }


@router.post("/api/v1/calls", dependencies=[Depends(rate_limit("call-start", 10))])
async def start_call(req: StartCallRequest, c: Container = Depends(get_container)) -> dict[str, Any]:
    if c.llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No LLM is configured (LLM_PROVIDER / LLM_MODEL)")
    try:
        session, greeting = await CallSession.start(c, caller_phone=req.caller_phone, channel="web", shop_id=req.shop_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    return {"call_id": session.call_id, "call_token": issue_call_token(c.settings, session.call_id),
            "speech": greeting, "demo_sms_inbox": isinstance(c.sms, DemoInboxSms),
            "returning_customer": session.state.has_saved_paypal}


async def _web_session(c: Container, call_id: str, token: str | None) -> CallSession:
    if not check_call_token(c.settings, call_id, token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid call token")
    session = await CallSession.resume(c, call_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    return session


@router.post("/api/v1/calls/{call_id}/turns", dependencies=[Depends(rate_limit("call-turn", 60))])
async def call_turn(call_id: str, req: TurnRequest, x_call_token: str | None = Header(None),
                    c: Container = Depends(get_container)) -> dict[str, Any]:
    session = await _web_session(c, call_id, x_call_token)
    try:
        result = await session.handle_turn(req.text, interrupted=req.interrupted, stt_ms=req.stt_ms,
                                           tts_ms=req.tts_ms, caller_phone=req.caller_phone)
    except LLMUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    return _turn_payload(c, call_id, result)


@router.post("/api/v1/calls/{call_id}/silence", dependencies=[Depends(rate_limit("call-turn", 60))])
async def call_silence(call_id: str, x_call_token: str | None = Header(None),
                       c: Container = Depends(get_container)) -> dict[str, Any]:
    session = await _web_session(c, call_id, x_call_token)
    return _turn_payload(c, call_id, await session.handle_silence())


@router.get("/api/v1/calls/{call_id}/demo-sms")
async def demo_sms(call_id: str, x_call_token: str | None = Header(None),
                   c: Container = Depends(get_container)) -> dict[str, Any]:
    if not isinstance(c.sms, DemoInboxSms):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Demo SMS inbox is disabled")
    session = await _web_session(c, call_id, x_call_token)
    return {"messages": c.sms.inbox(session.state.caller_hash)}


# ---------------------------------------------------------------------- service-token agent API


@router.post("/api/v1/agent/calls", dependencies=[Depends(require_service)])
async def agent_start_call(req: StartCallRequest, c: Container = Depends(get_container)) -> dict[str, Any]:
    session, greeting = await CallSession.start(c, caller_phone=req.caller_phone, channel="phone", shop_id=req.shop_id)
    return {"call_id": session.call_id, "speech": greeting}


@router.post("/api/v1/agent/calls/{call_id}/turns", dependencies=[Depends(require_service)])
async def agent_turn(call_id: str, req: TurnRequest, c: Container = Depends(get_container)) -> dict[str, Any]:
    session = await CallSession.resume(c, call_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    try:
        result = await session.handle_turn(req.text, interrupted=req.interrupted, stt_ms=req.stt_ms,
                                           tts_ms=req.tts_ms, caller_phone=req.caller_phone)
    except LLMUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    return _turn_payload(c, call_id, result)


# ---------------------------------------------------------------------- PayPal return / cancel


@router.get("/api/v1/paypal/return")
async def paypal_return(order_id: str, c: Container = Depends(get_container)) -> RedirectResponse:
    """Buyer came back from PayPal: capture server-side (idempotent), then show the status page."""
    result = await c.payments.capture_approved_order(order_id, trigger="return_url")
    if not result.order_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    token = issue_order_token(c.settings, order_id)
    return RedirectResponse(f"{c.settings.web_base_url}/order-status/{order_id}?t={token}", status_code=303)


@router.get("/api/v1/paypal/cancel")
async def paypal_cancel(order_id: str, c: Container = Depends(get_container)) -> RedirectResponse:
    token = issue_order_token(c.settings, order_id)
    return RedirectResponse(f"{c.settings.web_base_url}/order-status/{order_id}?t={token}&cancelled=1", status_code=303)


# ---------------------------------------------------------------------- mock PayPal (PAYPAL_MODE=mock only)


def _mock_gateway(c: Container) -> MockPayPalGateway:
    if c.settings.paypal_mode != PayPalMode.MOCK or not isinstance(c.paypal, MockPayPalGateway):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return c.paypal


@router.get("/api/v1/mock-paypal/approve/{paypal_order_id}", response_class=HTMLResponse)
async def mock_approve_page(paypal_order_id: str, c: Container = Depends(get_container)) -> str:
    gw = _mock_gateway(c)
    order = gw.orders.get(paypal_order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    amount = order["purchase_units"][0]["amount"]
    pid = html.escape(paypal_order_id)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Mock PayPal approval</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:system-ui;max-width:28rem;margin:3rem auto;padding:0 1rem">
<p style="background:#fff3cd;padding:.75rem;border-radius:.5rem"><strong>Test mode.</strong>
This is COUNTER's offline PayPal stand-in (PAYPAL_MODE=mock). No money moves. With PAYPAL_MODE=sandbox
the buyer approves on PayPal's real sandbox checkout instead.</p>
<h1>Approve {html.escape(amount['value'])} {html.escape(amount['currency_code'])}</h1>
<form method="post" action="/api/v1/mock-paypal/approve/{pid}"><button type="submit">Approve payment</button></form>
<form method="post" action="/api/v1/mock-paypal/cancel/{pid}"><button type="submit">Cancel</button></form>
</body></html>"""


@router.post("/api/v1/mock-paypal/approve/{paypal_order_id}")
async def mock_approve(paypal_order_id: str, c: Container = Depends(get_container)) -> RedirectResponse:
    gw = _mock_gateway(c)
    if paypal_order_id not in gw.orders:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    gw.approve(paypal_order_id)
    return RedirectResponse(f"{gw.orders[paypal_order_id]['return_url']}&token={paypal_order_id}", status_code=303)


@router.post("/api/v1/mock-paypal/cancel/{paypal_order_id}")
async def mock_cancel(paypal_order_id: str, c: Container = Depends(get_container)) -> RedirectResponse:
    gw = _mock_gateway(c)
    if paypal_order_id not in gw.orders:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return RedirectResponse(gw.orders[paypal_order_id]["cancel_url"], status_code=303)


# ---------------------------------------------------------------------- customer order status


def _public_order(order: OrderModel) -> dict[str, Any]:
    return {
        "id": order.id,
        "state": order.state,
        "currency": order.currency,
        "subtotal_cents": order.subtotal_cents,
        "tax_cents": order.tax_cents,
        "total_cents": order.total_cents,
        "pickup_at": order.pickup_at.isoformat() + "Z" if order.pickup_at else None,
        "approval_url": order.approval_url if order.state == "LINK_SENT" else None,
        "updated_at": order.updated_at.isoformat() + "Z",
        "lines": [{"item_id": line.item_id, "name": line.name, "qty": line.qty, "line_total_cents": line.line_total_cents,
                   "modifiers": [m["name"] for m in json.loads(line.modifiers_json or "[]")]} for line in order.lines],
    }


@router.get("/api/v1/orders/{order_id}/status", dependencies=[Depends(rate_limit("status", 120))])
async def order_status(order_id: str, t: str = Query(...), c: Container = Depends(get_container)) -> dict[str, Any]:
    if not check_order_token(c.settings, order_id, t):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This link is invalid or has expired")
    async with c.session_maker() as session:
        order = await OrderRepository(session).get_order(order_id)
        if order is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
        from apps.api.db.models import Shop

        shop = await session.get(Shop, order.shop_id)
        return {**_public_order(order), "shop_name": shop.name if shop else ""}


@router.get("/api/v1/orders/{order_id}/stream")
async def order_stream(order_id: str, t: str = Query(...), last_event_id: str | None = Header(None),
                       c: Container = Depends(get_container)) -> StreamingResponse:
    if not check_order_token(c.settings, order_id, t):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This link is invalid or has expired")
    return StreamingResponse(
        event_stream(c, last_event_id=last_event_id, accept=lambda e: e["order_id"] == order_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------- PayPal webhooks


def _parse_paypal_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)
    except ValueError:
        return None


@router.post("/api/v1/paypal/webhooks")
async def paypal_webhook(request: Request, c: Container = Depends(get_container)) -> Response:
    """Verify, store (deduplicated by event id), acknowledge. Processing happens in the worker."""
    headers = WebhookHeaders.from_mapping(request.headers)
    if headers is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing PayPal transmission headers")
    raw = await request.body()
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON") from None
    event_id, event_type = event.get("id"), event.get("event_type")
    if not isinstance(event_id, str) or not event_id or not isinstance(event_type, str):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Event id and type are required")
    if not await c.paypal.verify_webhook(headers, raw):
        logger.warning("Rejected webhook %s: signature verification failed", event_id)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Signature verification failed")

    async with c.session_maker() as session, session.begin():
        is_new = await OrderRepository(session).insert_webhook_event(
            paypal_event_id=event_id, event_type=event_type, raw_payload=raw.decode("utf-8"),
            paypal_create_time=_parse_paypal_time(event.get("create_time")),
        )
    return Response(status_code=status.HTTP_200_OK if is_new else status.HTTP_200_OK,
                    content=json.dumps({"received": True, "duplicate": not is_new}),
                    media_type="application/json")
