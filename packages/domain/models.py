"""
Domain models for COUNTER.
Pure Python, zero I/O, deterministic types and schemas.
All monetary values are strict integer cents. Floats are never permitted near money.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class OrderState(str, Enum):
    DRAFT = "DRAFT"
    QUOTED = "QUOTED"
    LINK_SENT = "LINK_SENT"
    APPROVED = "APPROVED"
    CAPTURED = "CAPTURED"
    IN_KITCHEN = "IN_KITCHEN"
    READY = "READY"
    PICKED_UP = "PICKED_UP"
    CANCELLED = "CANCELLED"
    LINK_EXPIRED = "LINK_EXPIRED"
    REFUNDED_FULL = "REFUNDED_FULL"
    REFUNDED_PARTIAL = "REFUNDED_PARTIAL"
    NO_SHOW = "NO_SHOW"
    CONFIRMED_CATERING = "CONFIRMED_CATERING"


class OrderAction(str, Enum):
    QUOTE = "quote"
    SEND_LINK = "send_link"
    APPROVE = "approve"
    CAPTURE = "capture"
    KITCHEN_ACCEPT = "kitchen_accept"
    MARK_READY = "mark_ready"
    PICKUP = "pickup"
    CANCEL = "cancel"
    EXPIRE = "expire"
    SWEEP_NO_SHOW = "sweep_no_show"
    REFUND = "refund"
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
    paypal_product_id: Optional[str] = None
    modifiers: tuple[ModifierDefinition, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ShopPolicy:
    cancel_full_refund_until_state: OrderState = OrderState.CAPTURED
    partial_refund_pct: int = 50  # 50% partial refund when cancelled in kitchen
    link_ttl_min: int = 15
    max_order_value_cents: int = 50000  # $500.00
    refund_approval_threshold_cents: int = 2500  # $25.00
    catering_threshold_cents: int = 20000  # $200.00


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
    tax_rate_bps: int  # basis points (e.g. 825 = 8.25%)
    tax_cents: int
    total_cents: int
    quote_hash: str
    spoken_summary: str


@dataclass(frozen=True)
class GuardDecision:
    kind: GuardDecisionKind
    reason: str
    allowed: bool
    computed_amount_cents: Optional[int] = None
    target_state: Optional[OrderState] = None
    metadata: dict[str, Any] = field(default_factory=dict)
