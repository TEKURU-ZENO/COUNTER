"""
SQLAlchemy 2.0 models for COUNTER.

All timestamps are naive UTC (utc_now()). Money is integer cents with an explicit currency.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from packages.domain.models import ActorType, OrderState


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    hours_json: Mapped[str] = mapped_column(Text, default="{}")
    prep_capacity: Mapped[int] = mapped_column(Integer, default=10)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    demo_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


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


class MenuDraftModel(Base):
    """Output of menu extraction awaiting owner confirmation. Never served to callers."""

    __tablename__ = "menu_drafts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    shop_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT")  # DRAFT, PUBLISHED, DISCARDED
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    model: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    published_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


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
    call_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    caller_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), default=OrderState.DRAFT.value, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    quote_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    pickup_slot_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pickup_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    paypal_order_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    approval_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    link_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    vault_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    lines: Mapped[list["OrderLineModel"]] = relationship(
        "OrderLineModel", back_populates="order", lazy="selectin", cascade="all, delete-orphan"
    )


class OrderLineModel(Base):
    __tablename__ = "order_lines"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(64), ForeignKey("orders.id"), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, default=1)
    unit_total_cents: Mapped[int] = mapped_column(Integer, default=0)
    modifiers_json: Mapped[str] = mapped_column(Text, default="[]")
    line_total_cents: Mapped[int] = mapped_column(Integer, default=0)

    order: Mapped[OrderModel] = relationship("OrderModel", back_populates="lines")


class OrderEventModel(Base):
    __tablename__ = "order_events"
    __table_args__ = (UniqueConstraint("order_id", "seq", name="uq_order_event_seq"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(64), ForeignKey("orders.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    actor: Mapped[str] = mapped_column(String(32), default=ActorType.SYSTEM.value)
    actor_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class OutboxModel(Base):
    """Transactional outbox. The integer id is the global stream position used as the SSE id."""

    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_status_id", "status", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING, PUBLISHED, DEAD
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PaymentModel(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    paypal_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    capture_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fee_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="link")  # link, vault
    mandate_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class RefundModel(Base):
    __tablename__ = "refunds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    capture_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    paypal_refund_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, unique=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # COMPLETED, PENDING, FAILED
    source: Mapped[str] = mapped_column(String(32), default="agent")  # agent, owner, paypal
    approved_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class WebhookEventModel(Base):
    """Every verified PayPal event, deduplicated by PayPal's event id (primary key)."""

    __tablename__ = "webhook_events"

    paypal_event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    paypal_create_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    status: Mapped[str] = mapped_column(String(16), default="RECEIVED", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class VaultMandateModel(Base):
    __tablename__ = "vault_mandates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    vault_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    customer_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    shop_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    max_charge_cents: Mapped[int] = mapped_column(Integer, default=5000)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    revoke_token: Mapped[str] = mapped_column(String(128), default=generate_uuid, index=True)
    source_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ConsentEventModel(Base):
    """What the agent read, what the caller said, and how it was classified."""

    __tablename__ = "consent_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    call_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    quote_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class StepUpChallengeModel(Base):
    """SMS one-time code sent to the number on file before the first vault charge in a call."""

    __tablename__ = "step_up_challenges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    call_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mandate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class InvoiceModel(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    paypal_invoice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    deposit_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="SENT")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class CallModel(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    shop_id: Mapped[str] = mapped_column(String(64), nullable=False)
    caller_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    caller_last4: Mapped[str] = mapped_column(String(4), default="")
    channel: Mapped[str] = mapped_column(String(16), default="web")  # web, phone, eval
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class CallTurnModel(Base):
    __tablename__ = "call_turns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    call_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(32), nullable=False)  # caller, agent
    text: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    # Measured timings (null when not measured; never filled with estimates)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # server time for the turn
    llm_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    llm_ttft_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stt_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # reported by the speech client
    tts_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # reported by the speech client
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tool_calls_json: Mapped[str] = mapped_column(Text, default="[]")
    validation_errors: Mapped[int] = mapped_column(Integer, default=0)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class ApprovalModel(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="REFUND")
    requested_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="PENDING")  # PENDING, APPROVED, REJECTED
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    decided_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
