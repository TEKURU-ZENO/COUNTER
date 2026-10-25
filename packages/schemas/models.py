"""
Shared Pydantic v2 schemas for COUNTER.
Single source of truth for API contracts, tool arguments, database serialization,
and frontend TypeScript codegen.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from packages.domain.models import (
    ActorType,
    GuardDecisionKind,
    OrderAction,
    OrderState,
)


class ModifierSelectionSchema(BaseModel):
    modifier_id: str
    name: str
    price_delta_cents: int = Field(ge=0)


class CartItemSchema(BaseModel):
    item_id: str
    name: str
    qty: int = Field(gt=0)
    modifiers: List[ModifierSelectionSchema] = Field(default_factory=list)


class MenuItemSchema(BaseModel):
    id: str
    shop_id: str
    name: str
    category: str
    price_cents: int = Field(ge=0)
    prep_min: int = Field(default=15, ge=1)
    available: bool = True
    dietary_tags: List[str] = Field(default_factory=list)
    paypal_product_id: Optional[str] = None


class ShopPolicySchema(BaseModel):
    cancel_full_refund_until_state: OrderState = OrderState.CAPTURED
    partial_refund_pct: int = Field(default=50, ge=0, le=100)
    link_ttl_min: int = Field(default=15, ge=1)
    max_order_value_cents: int = Field(default=50000, ge=100)
    refund_approval_threshold_cents: int = Field(default=2500, ge=100)
    catering_threshold_cents: int = Field(default=20000, ge=100)


class QuoteLineSchema(BaseModel):
    item_id: str
    name: str
    qty: int
    unit_base_cents: int
    modifiers_delta_cents: int
    unit_total_cents: int
    line_total_cents: int
    modifiers: List[ModifierSelectionSchema] = Field(default_factory=list)


class QuoteResponseSchema(BaseModel):
    shop_id: str
    lines: List[QuoteLineSchema]
    subtotal_cents: int
    tax_rate_bps: int
    tax_cents: int
    total_cents: int
    quote_hash: str
    spoken_summary: str


class OrderLineSchema(BaseModel):
    id: Optional[str] = None
    item_id: str
    name: str
    qty: int
    modifiers: List[ModifierSelectionSchema] = Field(default_factory=list)
    line_total_cents: int


class OrderResponseSchema(BaseModel):
    id: str
    shop_id: str
    caller_hash: str
    state: OrderState
    version: int
    subtotal_cents: int
    tax_cents: int
    total_cents: int
    quote_hash: Optional[str] = None
    pickup_at: Optional[datetime] = None
    paypal_order_id: Optional[str] = None
    created_at: datetime
    lines: List[OrderLineSchema] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class OrderEventSchema(BaseModel):
    id: str
    order_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    actor: ActorType
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ToolInvocationSchema(BaseModel):
    call_id: str
    seq: int
    tool: str
    args: dict[str, Any]
    guard_decision: GuardDecisionKind
    reason: str
    result: Optional[dict[str, Any]] = None
    latency_ms: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PaymentRecordSchema(BaseModel):
    order_id: str
    paypal_order_id: str
    capture_id: Optional[str] = None
    status: str
    amount_cents: int
    fee_cents: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RefundRecordSchema(BaseModel):
    id: str
    order_id: str
    capture_id: str
    paypal_refund_id: Optional[str] = None
    amount_cents: int
    reason: str
    status: str
    approved_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ApprovalRequestSchema(BaseModel):
    id: str
    order_id: str
    kind: str
    requested_amount_cents: int
    reason: str
    status: str  # PENDING, APPROVED, REJECTED
    requested_at: datetime
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None


class SendPaymentLinkRequest(BaseModel):
    order_id: str
    quote_hash: str
    pickup_slot_id: Optional[str] = None


class SendPaymentLinkResponse(BaseModel):
    order_id: str
    paypal_order_id: str
    approval_url: str
    qr_code_data: str
    expires_at: datetime


class WebhookPayloadSchema(BaseModel):
    id: str
    event_type: str
    resource_type: str
    resource: dict[str, Any]
    create_time: str
