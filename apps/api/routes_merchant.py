"""Merchant routes (owner session required): orders, kitchen, refunds, approvals, copilot, menu."""

from __future__ import annotations

import json
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from apps.api.db.models import (
    ApprovalModel,
    CallTurnModel,
    MenuDraftModel,
    OrderEventModel,
    OrderModel,
    PaymentModel,
    RefundModel,
    Shop,
)
from apps.api.security import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    get_container,
    issue_session,
    rate_limit,
    require_merchant,
    verify_password,
)
from apps.api.sse import event_stream
from apps.core.container import Container
from packages.domain.models import OrderAction

router = APIRouter(prefix="/api/v1/merchant")


# ---------------------------------------------------------------------- auth


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=255)
    password: str = Field(max_length=255)


@router.post("/auth/login", dependencies=[Depends(rate_limit("login", 10))])
async def login(req: LoginRequest, response: Response, c: Container = Depends(get_container)) -> dict[str, str]:
    s = c.settings
    ok = (
        s.owner_email is not None
        and s.owner_password_hash is not None
        and req.email.strip().lower() == s.owner_email.lower()
        and verify_password(req.password, s.owner_password_hash)
    )
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    response.set_cookie(
        SESSION_COOKIE, issue_session(s, s.owner_email or ""), max_age=SESSION_MAX_AGE, httponly=True,
        samesite="lax", secure=s.is_deployed, path="/",
    )
    return {"email": s.owner_email or ""}


@router.post("/auth/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
async def me(owner: str = Depends(require_merchant)) -> dict[str, str]:
    return {"email": owner}


# ---------------------------------------------------------------------- orders


def _order_row(o: OrderModel) -> dict[str, Any]:
    return {
        "id": o.id, "state": o.state, "version": o.version, "currency": o.currency,
        "subtotal_cents": o.subtotal_cents, "tax_cents": o.tax_cents, "total_cents": o.total_cents,
        "pickup_at": o.pickup_at.isoformat() + "Z" if o.pickup_at else None,
        "paypal_order_id": o.paypal_order_id, "review_reason": o.review_reason, "call_id": o.call_id,
        "created_at": o.created_at.isoformat() + "Z", "updated_at": o.updated_at.isoformat() + "Z",
        "lines": [{"name": line.name, "qty": line.qty, "line_total_cents": line.line_total_cents,
                   "modifiers": [m["name"] for m in json.loads(line.modifiers_json or "[]")]} for line in o.lines],
    }


@router.get("/orders")
async def list_orders(state: Optional[str] = None, limit: int = 200, _: str = Depends(require_merchant),
                      c: Container = Depends(get_container)) -> list[dict[str, Any]]:
    stmt = select(OrderModel).where(OrderModel.shop_id == c.settings.default_shop_id)
    if state:
        stmt = stmt.where(OrderModel.state.in_(state.split(",")))
    stmt = stmt.order_by(OrderModel.created_at.desc()).limit(min(limit, 500))
    async with c.session_maker() as session:
        return [_order_row(o) for o in (await session.execute(stmt)).scalars().all()]


@router.get("/orders/{order_id}")
async def get_order(order_id: str, _: str = Depends(require_merchant), c: Container = Depends(get_container)) -> dict[str, Any]:
    async with c.session_maker() as session:
        order = await session.get(OrderModel, order_id)
        if order is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        events = (await session.execute(select(OrderEventModel).where(OrderEventModel.order_id == order_id)
                                        .order_by(OrderEventModel.seq))).scalars().all()
        payments = (await session.execute(select(PaymentModel).where(PaymentModel.order_id == order_id))).scalars().all()
        refunds = (await session.execute(select(RefundModel).where(RefundModel.order_id == order_id))).scalars().all()
    return {
        **_order_row(order),
        "events": [{"seq": e.seq, "type": e.type, "actor": e.actor, "actor_id": e.actor_id,
                    "payload": json.loads(e.payload_json), "at": e.created_at.isoformat() + "Z"} for e in events],
        "payments": [{"capture_id": p.capture_id, "status": p.status, "amount_cents": p.amount_cents,
                      "currency": p.currency, "fee_cents": p.fee_cents, "source": p.source} for p in payments],
        "refunds": [{"paypal_refund_id": r.paypal_refund_id, "status": r.status, "amount_cents": r.amount_cents,
                     "source": r.source, "approved_by": r.approved_by, "at": r.created_at.isoformat() + "Z"}
                    for r in refunds],
    }


class OrderActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["kitchen_accept", "mark_ready", "pickup", "resolve_review"]


@router.post("/orders/{order_id}/actions")
async def order_action(order_id: str, req: OrderActionRequest, owner: str = Depends(require_merchant),
                       c: Container = Depends(get_container)) -> dict[str, Any]:
    result = await c.payments.merchant_transition(order_id, OrderAction(req.action), owner=owner)
    if not result.ok:
        raise HTTPException(status.HTTP_409_CONFLICT, result.reason)
    return {"order_id": order_id, "state": result.state}


class RefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_cents: int = Field(gt=0)
    client_request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@router.post("/orders/{order_id}/refunds")
async def refund(order_id: str, req: RefundRequest, owner: str = Depends(require_merchant),
                 c: Container = Depends(get_container)) -> dict[str, Any]:
    result = await c.payments.owner_refund(order_id, req.amount_cents, owner=owner,
                                           client_request_id=req.client_request_id)
    if not result.ok and result.reason not in ("PENDING",):
        raise HTTPException(status.HTTP_409_CONFLICT, result.reason)
    return {"order_id": order_id, "state": result.state, "status": result.reason, **result.data}


# ---------------------------------------------------------------------- approvals


@router.get("/approvals")
async def approvals(_: str = Depends(require_merchant), c: Container = Depends(get_container)) -> list[dict[str, Any]]:
    async with c.session_maker() as session:
        rows = (await session.execute(select(ApprovalModel).order_by(ApprovalModel.requested_at.desc()).limit(100))).scalars().all()
    return [{"id": a.id, "order_id": a.order_id, "kind": a.kind, "amount_cents": a.requested_amount_cents,
             "reason": a.reason, "status": a.status, "requested_at": a.requested_at.isoformat() + "Z",
             "decided_by": a.decided_by, "decided_at": a.decided_at.isoformat() + "Z" if a.decided_at else None}
            for a in rows]


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool


@router.post("/approvals/{approval_id}/decision")
async def decide(approval_id: str, req: DecisionRequest, owner: str = Depends(require_merchant),
                 c: Container = Depends(get_container)) -> dict[str, Any]:
    result = await c.payments.decide_approval(approval_id, approve=req.approve, decided_by=owner)
    if not result.ok and result.reason == "Approval not pending":
        raise HTTPException(status.HTTP_409_CONFLICT, result.reason)
    return {"ok": result.ok, "status": result.reason, "order_id": result.order_id, "state": result.state}


# ---------------------------------------------------------------------- shop, calls, stream


class ShopOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_open: bool


@router.put("/shop/open")
async def set_open(req: ShopOpenRequest, _: str = Depends(require_merchant), c: Container = Depends(get_container)) -> dict[str, Any]:
    async with c.session_maker() as session, session.begin():
        shop = await session.get(Shop, c.settings.default_shop_id)
        if shop is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        shop.is_open = req.is_open
    return {"is_open": req.is_open}


@router.get("/calls/{call_id}/turns")
async def call_turns(call_id: str, _: str = Depends(require_merchant), c: Container = Depends(get_container)) -> list[dict[str, Any]]:
    async with c.session_maker() as session:
        rows = (await session.execute(select(CallTurnModel).where(CallTurnModel.call_id == call_id)
                                      .order_by(CallTurnModel.seq))).scalars().all()
    return [{"seq": t.seq, "speaker": t.speaker, "text": t.text, "model": t.model, "llm_ms": t.llm_ms,
             "llm_ttft_ms": t.llm_ttft_ms, "stt_ms": t.stt_ms, "tts_ms": t.tts_ms,
             "tool_calls": json.loads(t.tool_calls_json or "[]")} for t in rows]


@router.get("/stream")
async def merchant_stream(last_event_id: Optional[str] = Header(None), _: str = Depends(require_merchant),
                          c: Container = Depends(get_container)) -> StreamingResponse:
    return StreamingResponse(event_stream(c, last_event_id=last_event_id, accept=lambda e: True),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------- copilot


class CopilotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=500)


@router.post("/copilot", dependencies=[Depends(rate_limit("copilot", 20))])
async def copilot(req: CopilotRequest, _: str = Depends(require_merchant), c: Container = Depends(get_container)) -> dict[str, Any]:
    from apps.api.copilot import CopilotUnavailable, answer

    try:
        return await answer(c, req.question)
    except CopilotUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


# ---------------------------------------------------------------------- menu onboarding


@router.post("/menu/extract", dependencies=[Depends(rate_limit("menu-extract", 5))])
async def menu_extract(file: UploadFile = File(...), _: str = Depends(require_merchant),
                       c: Container = Depends(get_container)) -> dict[str, Any]:
    from apps.api.menu_extract import ExtractionUnavailable, extract_menu

    data = await file.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Image must be under 8 MB")
    media_type = file.content_type or ""
    if media_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Upload a JPEG, PNG, WebP or GIF photo")
    try:
        return await extract_menu(c, data, media_type)
    except ExtractionUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


class PublishItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=60)
    price_cents: int = Field(ge=50, le=20_000)


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]
    items: list[PublishItem] = Field(min_length=1, max_length=200)


@router.post("/menu/drafts/{draft_id}/publish")
async def publish_menu(draft_id: str, req: PublishRequest, owner: str = Depends(require_merchant),
                       c: Container = Depends(get_container)) -> dict[str, Any]:
    from apps.api.menu_extract import publish_draft

    async with c.session_maker() as session:
        draft = await session.get(MenuDraftModel, draft_id)
        if draft is None or draft.status != "DRAFT":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft not found or already published")
    count = await publish_draft(c, draft_id, [i.model_dump() for i in req.items], owner)
    return {"published": count}
