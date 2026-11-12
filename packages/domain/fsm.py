"""
Order state machine. Every legal transition is one row in TRANSITION_TABLE.

Payment-status rules worth calling out:
- A capture that PayPal reports as PENDING goes to PAYMENT_PENDING, never CAPTURED, so no
  kitchen ticket can exist for it (KITCHEN_ACCEPT is only legal from CAPTURED).
- Any disagreement between our ledger and PayPal goes to NEEDS_REVIEW.
- Refund states are reached by recording a refund that PayPal confirmed, full or partial.
"""

from __future__ import annotations

from typing import NamedTuple

from packages.domain.models import OrderAction, OrderState

S = OrderState


class IllegalStateTransitionError(ValueError):
    def __init__(self, current_state: OrderState, action: OrderAction, reason: str = ""):
        self.current_state = current_state
        self.action = action
        self.reason = reason
        msg = f"Cannot execute action '{action.value}' from state '{current_state.value}'"
        super().__init__(f"{msg}: {reason}" if reason else msg)


class TransitionRule(NamedTuple):
    allowed_from_states: frozenset[OrderState]
    to_state: OrderState
    side_effects: str


_REFUNDABLE = frozenset({S.CAPTURED, S.IN_KITCHEN, S.READY, S.PICKED_UP, S.NO_SHOW, S.NEEDS_REVIEW})

TRANSITION_TABLE: dict[OrderAction, TransitionRule] = {
    OrderAction.QUOTE: TransitionRule(frozenset({S.DRAFT, S.QUOTED}), S.QUOTED, "quote_generated"),
    OrderAction.SEND_LINK: TransitionRule(frozenset({S.QUOTED}), S.LINK_SENT, "paypal_order_created"),
    OrderAction.CHARGE_SAVED_PAYMENT: TransitionRule(
        frozenset({S.QUOTED}), S.CAPTURED, "paypal_vault_charge_completed"
    ),
    OrderAction.APPROVE: TransitionRule(frozenset({S.LINK_SENT}), S.APPROVED, "paypal_buyer_approved"),
    OrderAction.CAPTURE: TransitionRule(
        frozenset({S.LINK_SENT, S.APPROVED, S.PAYMENT_PENDING}), S.CAPTURED, "paypal_capture_completed"
    ),
    OrderAction.MARK_PAYMENT_PENDING: TransitionRule(
        frozenset({S.QUOTED, S.LINK_SENT, S.APPROVED}), S.PAYMENT_PENDING, "paypal_capture_pending"
    ),
    OrderAction.DENY_PAYMENT: TransitionRule(
        frozenset({S.QUOTED, S.LINK_SENT, S.APPROVED, S.PAYMENT_PENDING}),
        S.PAYMENT_DENIED,
        "paypal_capture_denied",
    ),
    OrderAction.REVERSE: TransitionRule(
        frozenset({S.CAPTURED, S.IN_KITCHEN, S.READY, S.PICKED_UP, S.NO_SHOW, S.NEEDS_REVIEW}),
        S.REVERSED,
        "paypal_capture_reversed",
    ),
    OrderAction.FLAG_REVIEW: TransitionRule(
        frozenset({S.QUOTED, S.LINK_SENT, S.APPROVED, S.PAYMENT_PENDING, S.CAPTURED, S.IN_KITCHEN, S.READY}),
        S.NEEDS_REVIEW,
        "reconciliation_mismatch",
    ),
    OrderAction.RESOLVE_REVIEW: TransitionRule(frozenset({S.NEEDS_REVIEW}), S.CAPTURED, "owner_resolved_review"),
    OrderAction.KITCHEN_ACCEPT: TransitionRule(frozenset({S.CAPTURED}), S.IN_KITCHEN, "kitchen_ticket_accepted"),
    OrderAction.MARK_READY: TransitionRule(frozenset({S.IN_KITCHEN}), S.READY, "customer_ready_notified"),
    OrderAction.PICKUP: TransitionRule(frozenset({S.READY}), S.PICKED_UP, "order_completed"),
    OrderAction.CANCEL: TransitionRule(
        frozenset({S.DRAFT, S.QUOTED, S.LINK_SENT}), S.CANCELLED, "slot_released"
    ),
    OrderAction.EXPIRE: TransitionRule(frozenset({S.LINK_SENT}), S.LINK_EXPIRED, "link_invalidated"),
    OrderAction.SWEEP_NO_SHOW: TransitionRule(frozenset({S.READY}), S.NO_SHOW, "no_show_recorded"),
    OrderAction.RECORD_FULL_REFUND: TransitionRule(
        _REFUNDABLE | {S.REFUNDED_PARTIAL}, S.REFUNDED_FULL, "refund_recorded_full"
    ),
    OrderAction.RECORD_PARTIAL_REFUND: TransitionRule(
        _REFUNDABLE | {S.REFUNDED_PARTIAL}, S.REFUNDED_PARTIAL, "refund_recorded_partial"
    ),
    OrderAction.CONFIRM_CATERING: TransitionRule(
        frozenset({S.QUOTED}), S.CONFIRMED_CATERING, "catering_deposit_invoiced"
    ),
}

# Order states from which a caller-initiated refund is policy-eligible (amount set by the guard).
POLICY_REFUND_STATES = frozenset({S.CAPTURED, S.IN_KITCHEN})


def can_transition(current_state: OrderState, action: OrderAction) -> bool:
    rule = TRANSITION_TABLE.get(action)
    return rule is not None and current_state in rule.allowed_from_states


def get_next_state(current_state: OrderState, action: OrderAction) -> OrderState:
    rule = TRANSITION_TABLE.get(action)
    if rule is None:
        raise IllegalStateTransitionError(current_state, action, f"Unknown action {action.value}")
    if current_state not in rule.allowed_from_states:
        allowed = ", ".join(sorted(s.value for s in rule.allowed_from_states))
        raise IllegalStateTransitionError(current_state, action, f"allowed only from [{allowed}]")
    return rule.to_state


def get_side_effects(action: OrderAction) -> str:
    rule = TRANSITION_TABLE.get(action)
    return rule.side_effects if rule else "unknown_side_effect"


def refund_action(captured_cents: int, refunded_total_cents: int) -> OrderAction:
    """Which refund transition applies once refunded_total_cents (cumulative) is confirmed."""
    if refunded_total_cents <= 0 or refunded_total_cents > captured_cents:
        raise ValueError("Refund total must be positive and not exceed the captured amount")
    if refunded_total_cents == captured_cents:
        return OrderAction.RECORD_FULL_REFUND
    return OrderAction.RECORD_PARTIAL_REFUND
