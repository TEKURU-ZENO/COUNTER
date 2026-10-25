"""
Finite State Machine for COUNTER Order Engine.
Pure Python, zero I/O, deterministic transition validation.
Every transition is defined in a plain data transition table.
"""

from __future__ import annotations

from typing import NamedTuple, Optional
from packages.domain.models import OrderAction, OrderState


class IllegalStateTransitionError(ValueError):
    """Raised when an action is executed from an invalid order state."""

    def __init__(self, current_state: OrderState, action: OrderAction, reason: str = ""):
        self.current_state = current_state
        self.action = action
        self.reason = reason
        action_name = getattr(action, "value", str(action))
        current_name = getattr(current_state, "value", str(current_state))
        msg = f"Cannot execute action '{action_name}' from state '{current_name}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class TransitionRule(NamedTuple):
    allowed_from_states: frozenset[OrderState]
    to_state: OrderState
    side_effects: str


# Plain data transition table: {action: (allowed_from_states, to_state, side_effects)}
TRANSITION_TABLE: dict[OrderAction, TransitionRule] = {
    OrderAction.QUOTE: TransitionRule(
        allowed_from_states=frozenset({OrderState.DRAFT, OrderState.QUOTED}),
        to_state=OrderState.QUOTED,
        side_effects="quote_generated",
    ),
    OrderAction.SEND_LINK: TransitionRule(
        allowed_from_states=frozenset({OrderState.QUOTED}),
        to_state=OrderState.LINK_SENT,
        side_effects="paypal_order_created",
    ),
    OrderAction.APPROVE: TransitionRule(
        allowed_from_states=frozenset({OrderState.LINK_SENT}),
        to_state=OrderState.APPROVED,
        side_effects="paypal_buyer_approved",
    ),
    OrderAction.CAPTURE: TransitionRule(
        # Allows direct capture from APPROVED or from LINK_SENT when webhook capture arrives
        allowed_from_states=frozenset({OrderState.APPROVED, OrderState.LINK_SENT}),
        to_state=OrderState.CAPTURED,
        side_effects="paypal_payment_captured",
    ),
    OrderAction.KITCHEN_ACCEPT: TransitionRule(
        allowed_from_states=frozenset({OrderState.CAPTURED}),
        to_state=OrderState.IN_KITCHEN,
        side_effects="kitchen_ticket_accepted",
    ),
    OrderAction.MARK_READY: TransitionRule(
        allowed_from_states=frozenset({OrderState.IN_KITCHEN}),
        to_state=OrderState.READY,
        side_effects="customer_sms_ready_dispatched",
    ),
    OrderAction.PICKUP: TransitionRule(
        allowed_from_states=frozenset({OrderState.READY}),
        to_state=OrderState.PICKED_UP,
        side_effects="order_completed",
    ),
    OrderAction.CANCEL: TransitionRule(
        allowed_from_states=frozenset({OrderState.DRAFT, OrderState.QUOTED, OrderState.LINK_SENT}),
        to_state=OrderState.CANCELLED,
        side_effects="slot_released",
    ),
    OrderAction.EXPIRE: TransitionRule(
        allowed_from_states=frozenset({OrderState.LINK_SENT}),
        to_state=OrderState.LINK_EXPIRED,
        side_effects="link_invalidated",
    ),
    OrderAction.SWEEP_NO_SHOW: TransitionRule(
        allowed_from_states=frozenset({OrderState.READY}),
        to_state=OrderState.NO_SHOW,
        side_effects="no_show_recorded",
    ),
    OrderAction.CONFIRM_CATERING: TransitionRule(
        allowed_from_states=frozenset({OrderState.QUOTED}),
        to_state=OrderState.CONFIRMED_CATERING,
        side_effects="catering_deposit_invoiced",
    ),
}

# Refund transitions depend on the current state (CAPTURED -> REFUNDED_FULL; IN_KITCHEN -> REFUNDED_PARTIAL)
REFUND_TRANSITIONS: dict[OrderState, OrderState] = {
    OrderState.CAPTURED: OrderState.REFUNDED_FULL,
    OrderState.IN_KITCHEN: OrderState.REFUNDED_PARTIAL,
}


def can_transition(current_state: OrderState, action: OrderAction) -> bool:
    """Check if the state transition is valid without raising an exception."""
    if action == OrderAction.REFUND:
        return current_state in REFUND_TRANSITIONS

    rule = TRANSITION_TABLE.get(action)
    if not rule:
        return False
    return current_state in rule.allowed_from_states


def get_next_state(current_state: OrderState, action: OrderAction) -> OrderState:
    """
    Compute the deterministic next state for the given action.
    Raises IllegalStateTransitionError if the action is not permitted from the current state.
    """
    if action == OrderAction.REFUND:
        if current_state not in REFUND_TRANSITIONS:
            raise IllegalStateTransitionError(
                current_state,
                action,
                f"Refund only permitted in CAPTURED (full) or IN_KITCHEN (partial). State is {current_state.value}",
            )
        return REFUND_TRANSITIONS[current_state]

    rule = TRANSITION_TABLE.get(action)
    if not rule:
        action_name = getattr(action, "value", str(action))
        raise IllegalStateTransitionError(current_state, action, f"Unknown action {action_name}")

    if current_state not in rule.allowed_from_states:
        allowed = ", ".join(s.value for s in rule.allowed_from_states)
        raise IllegalStateTransitionError(
            current_state,
            action,
            f"Action '{action.value}' is only allowed from states [{allowed}]",
        )

    return rule.to_state


def get_side_effects(action: OrderAction, current_state: Optional[OrderState] = None) -> str:
    """Return descriptive side effects for audit logging and outbox events."""
    if action == OrderAction.REFUND:
        if current_state == OrderState.CAPTURED:
            return "paypal_refund_full_executed"
        elif current_state == OrderState.IN_KITCHEN:
            return "paypal_refund_partial_executed"
        return "paypal_refund_executed"

    rule = TRANSITION_TABLE.get(action)
    return rule.side_effects if rule else "unknown_side_effect"
