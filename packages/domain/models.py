"""
Domain models for COUNTER. Pure Python, no I/O. Money is always integer cents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class OrderState(str, Enum):
    DRAFT = "DRAFT"
    QUOTED = "QUOTED"
    LINK_SENT = "LINK_SENT"
    APPROVED = "APPROVED"
    PAYMENT_PENDING = "PAYMENT_PENDING"  # capture exists but PayPal has not completed it
    CAPTURED = "CAPTURED"
    IN_KITCHEN = "IN_KITCHEN"
    READY = "READY"
    PICKED_UP = "PICKED_UP"
    CANCELLED = "CANCELLED"
    LINK_EXPIRED = "LINK_EXPIRED"
    PAYMENT_DENIED = "PAYMENT_DENIED"
    REVERSED = "REVERSED"  # chargeback / reversal after capture
    NEEDS_REVIEW = "NEEDS_REVIEW"  # PayPal's view disagrees with ours; a human must look
    REFUNDED_FULL = "REFUNDED_FULL"
    REFUNDED_PARTIAL = "REFUNDED_PARTIAL"
    NO_SHOW = "NO_SHOW"
    CONFIRMED_CATERING = "CONFIRMED_CATERING"


# States in which the kitchen may hold a ticket. PAYMENT_PENDING is deliberately absent.
PAID_STATES = frozenset(
    {OrderState.CAPTURED, OrderState.IN_KITCHEN, OrderState.READY, OrderState.PICKED_UP, OrderState.NO_SHOW}
)


class OrderAction(str, Enum):
    QUOTE = "quote"
    SEND_LINK = "send_link"
    CHARGE_SAVED_PAYMENT = "charge_saved_payment"
    APPROVE = "approve"
    CAPTURE = "capture"
    MARK_PAYMENT_PENDING = "mark_payment_pending"
    DENY_PAYMENT = "deny_payment"
    REVERSE = "reverse"
    FLAG_REVIEW = "flag_review"
    RESOLVE_REVIEW = "resolve_review"
    KITCHEN_ACCEPT = "kitchen_accept"
    MARK_READY = "mark_ready"
    PICKUP = "pickup"
    CANCEL = "cancel"
    EXPIRE = "expire"
    SWEEP_NO_SHOW = "sweep_no_show"
    RECORD_FULL_REFUND = "record_full_refund"
    RECORD_PARTIAL_REFUND = "record_partial_refund"
    CONFIRM_CATERING = "confirm_catering"


class GuardDecisionKind(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"
    NEEDS_FLOW = "needs_flow"


class ActorType(str, Enum):
    AGENT = "agent"
    OWNER = "owner"
    SYSTEM = "system"
    PAYPAL = "paypal"
    CALLER = "caller"


class ConsentDecision(str, Enum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class ModifierSelection:
    modifier_id: str
    name: str
    price_delta_cents: int


@dataclass(frozen=True)
class CartItem:
    item_id: str
    name: str
    qty: int
    modifiers: tuple[ModifierSelection, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ModifierDefinition:
    id: str
    name: str
    price_delta_cents: int
    group_name: str
    required: bool = False
    max_select: int = 1


@dataclass(frozen=True)
class MenuItem:
    id: str
    shop_id: str
    name: str
    category: str
    price_cents: int
    prep_min: int = 15
    available: bool = True
    dietary_tags: tuple[str, ...] = field(default_factory=tuple)
    paypal_product_id: str | None = None
    modifiers: tuple[ModifierDefinition, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ShopPolicy:
    partial_refund_pct: int = 50
    link_ttl_min: int = 15
    max_order_value_cents: int = 50000
    refund_approval_threshold_cents: int = 2500
    catering_threshold_cents: int = 20000
    consent_max_age_seconds: int = 300
    max_vault_charges_per_day: int = 3


@dataclass(frozen=True)
class VaultMandate:
    """Customer authorisation to charge a saved PayPal wallet at one shop, up to a cap."""

    id: str
    vault_id: str
    customer_hash: str
    shop_id: str
    max_charge_cents: int = 10000
    status: str = "ACTIVE"


@dataclass(frozen=True)
class ConsentRecord:
    """A stored consent event: what was read to the caller and how their reply was classified."""

    order_id: str
    call_id: str
    quote_hash: str
    amount_cents: int
    decision: ConsentDecision
    recorded_at: datetime


@dataclass(frozen=True)
class StepUpRecord:
    """A verified possession check (SMS one-time code) for one call and one mandate."""

    call_id: str
    mandate_id: str
    verified_at: datetime


@dataclass(frozen=True)
class QuoteLine:
    item_id: str
    name: str
    qty: int
    unit_base_cents: int
    modifiers_delta_cents: int
    unit_total_cents: int
    line_total_cents: int
    modifiers: tuple[ModifierSelection, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Quote:
    shop_id: str
    lines: tuple[QuoteLine, ...]
    subtotal_cents: int
    tax_rate_bps: int
    tax_cents: int
    total_cents: int
    quote_hash: str
    spoken_summary: str


@dataclass(frozen=True)
class GuardDecision:
    kind: GuardDecisionKind
    reason: str
    allowed: bool
    computed_amount_cents: int | None = None
    target_state: OrderState | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
