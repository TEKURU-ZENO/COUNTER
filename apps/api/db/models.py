"""
SQLAlchemy 2.0 Async Data Models for COUNTER.
Implements the exact data model defined in specification section 3.
Includes optimistic locking versioning and transactional outbox.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from packages.domain.models import ActorType, OrderState


class Base(DeclarativeBase):
    pass


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York")
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    hours_json: Mapped[str] = mapped_column(Text, default="{}")
    prep_capacity: Mapped[int] = mapped_column(Integer, default=10)
    policy_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    demo_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    cancel_full_refund_until_state: Mapped[str] = mapped_column(
        String(32), default=OrderState.CAPTURED.value
    )
    partial_refund_pct: Mapped[int] = mapped_column(Integer, default=50)
    link_ttl_min: Mapped[int] = mapped_column(Integer, default=15)
    max_order_value_cents: Mapped[int] = mapped_column(Integer, default=50000)
    refund_approval_threshold_cents: Mapped[int] = mapped_column(Integer, default=2500)
    catering_threshold_cents: Mapped[int] = mapped_column(Integer, default=20000)


class MenuItemModel(Base):
    __tablename__ = "menu_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(128), default="General")
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    prep_min: Mapped[int] = mapped_column(Integer, default=15)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    dietary_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    paypal_product_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


class ModifierModel(Base):
    __tablename__ = "modifiers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    price_delta_cents: Mapped[int] = mapped_column(Integer, default=0)
    group_name: Mapped[str] = mapped_column(String(128), default="Options")
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    max_select: Mapped[int] = mapped_column(Integer, default=1)


class PickupSlotModel(Base):
    __tablename__ = "pickup_slots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    shop_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=8)
    booked: Mapped[int] = mapped_column(Integer, default=0)


class OrderModel(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    shop_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    caller_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), default=OrderState.DRAFT.value, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # Optimistic Lock
    subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    quote_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    pickup_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    paypal_order_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    lines: Mapped[list[OrderLineModel]] = relationship("OrderLineModel", back_populates="order")
    events: Mapped[list[OrderEventModel]] = relationship("OrderEventModel", back_populates="order")


class OrderLineModel(Base):
    __tablename__ = "order_lines"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(64), ForeignKey("orders.id"), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, default=1)
    modifiers_json: Mapped[str] = mapped_column(Text, default="[]")
    line_total_cents: Mapped[int] = mapped_column(Integer, default=0)

    order: Mapped[OrderModel] = relationship("OrderModel", back_populates="lines")


class OrderEventModel(Base):
    __tablename__ = "order_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(64), ForeignKey("orders.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    actor: Mapped[str] = mapped_column(String(32), default=ActorType.SYSTEM.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    order: Mapped[OrderModel] = relationship("OrderModel", back_populates="events")


class OutboxModel(Base):
    __tablename__ = "outbox"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PaymentModel(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    paypal_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    capture_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED")
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RefundModel(Base):
    __tablename__ = "refunds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    capture_id: Mapped[str] = mapped_column(String(128), nullable=False)
    paypal_refund_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), default="Customer Request")
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED")
    approved_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WebhookEventModel(Base):
    __tablename__ = "webhook_events"

    # Strict UNIQUE constraint for exact-once delivery
    paypal_event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)


class InvoiceModel(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    paypal_invoice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    deposit_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="SENT")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CallModel(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    shop_id: Mapped[str] = mapped_column(String(64), nullable=False)
    caller_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), default="web")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class CallTurnModel(Base):
    __tablename__ = "call_turns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    call_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(32), nullable=False)  # "agent" or "caller"
    text: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


class ToolInvocationModel(Base):
    __tablename__ = "tool_invocations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    call_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    args_json: Mapped[str] = mapped_column(Text, default="{}")
    guard_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ApprovalModel(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="REFUND")
    requested_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="PENDING")  # PENDING, APPROVED, REJECTED
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    decided_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
