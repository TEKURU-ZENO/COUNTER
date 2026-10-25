"""
Counter Domain Package.
Contains pure, zero-I/O models, state machines, pricing engines, and guard policies.
"""

from packages.domain.fsm import (
    IllegalStateTransitionError,
    TRANSITION_TABLE,
    REFUND_TRANSITIONS,
    can_transition,
    get_next_state,
    get_side_effects,
)
from packages.domain.guard import Guard
from packages.domain.models import (
    ActorType,
    CartItem,
    GuardDecision,
    GuardDecisionKind,
    MenuItem,
    ModifierDefinition,
    ModifierSelection,
    OrderAction,
    OrderState,
    Quote,
    QuoteLine,
    ShopPolicy,
)
from packages.domain.pricing import (
    MenuItemNotFoundError,
    MenuItemUnavailableError,
    PricingError,
    calculate_quote,
    compute_quote_hash,
    format_cents,
    generate_spoken_readback,
)

__all__ = [
    "ActorType",
    "CartItem",
    "Guard",
    "GuardDecision",
    "GuardDecisionKind",
    "IllegalStateTransitionError",
    "MenuItem",
    "MenuItemNotFoundError",
    "MenuItemUnavailableError",
    "ModifierDefinition",
    "ModifierSelection",
    "OrderAction",
    "OrderState",
    "PricingError",
    "Quote",
    "QuoteLine",
    "REFUND_TRANSITIONS",
    "ShopPolicy",
    "TRANSITION_TABLE",
    "calculate_quote",
    "can_transition",
    "compute_quote_hash",
    "format_cents",
    "generate_spoken_readback",
    "get_next_state",
    "get_side_effects",
]
