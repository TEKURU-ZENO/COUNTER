"""
Guard Policy Engine for COUNTER.
Pure Python, zero I/O, deterministic safety boundary.
Every tool call and state-changing action must receive Guard evaluation
before any state mutation or external payment gateway execution.
"""

from __future__ import annotations

from typing import Optional
from packages.domain.fsm import can_transition, get_next_state
from packages.domain.models import (
    GuardDecision,
    GuardDecisionKind,
    OrderAction,
    OrderState,
    Quote,
    ShopPolicy,
)


class Guard:
    """
    Deterministic authority enforcing security invariants, quote integrity,
    order state validity, refund thresholds, and catering transitions.
    """

    def __init__(self, policy: Optional[ShopPolicy] = None):
        self.policy = policy or ShopPolicy()

    def evaluate_quote(self, quote: Quote) -> GuardDecision:
        """
        Validate a generated quote against shop policies.
        If the total exceeds the catering threshold, routes to catering flow.
        If exceeding max order value, denies.
        """
        if quote.total_cents > self.policy.max_order_value_cents:
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason=f"Order total of {quote.total_cents} cents exceeds maximum permitted order value of {self.policy.max_order_value_cents} cents",
                allowed=False,
                computed_amount_cents=quote.total_cents,
                metadata={"threshold_cents": self.policy.max_order_value_cents},
            )

        if quote.total_cents >= self.policy.catering_threshold_cents:
            return GuardDecision(
                kind=GuardDecisionKind.NEEDS_FLOW,
                reason=f"Order total of {quote.total_cents} cents meets or exceeds catering threshold of {self.policy.catering_threshold_cents} cents. Deposit invoice required.",
                allowed=False,
                computed_amount_cents=quote.total_cents,
                target_state=OrderState.CONFIRMED_CATERING,
                metadata={"flow": "catering", "threshold_cents": self.policy.catering_threshold_cents},
            )

        return GuardDecision(
            kind=GuardDecisionKind.ALLOW,
            reason="Quote within standard operational parameters",
            allowed=True,
            computed_amount_cents=quote.total_cents,
        )

    def evaluate_send_payment_link(
        self,
        current_state: OrderState,
        provided_quote_hash: str,
        current_quote: Quote,
        slot_available: bool = True,
        shop_open: bool = True,
    ) -> GuardDecision:
        """
        Verify preconditions for generating and dispatching a PayPal payment link:
        1. Current order state permits sending link (must be QUOTED).
        2. Shop is open and selected pickup slot has available capacity.
        3. Provided quote hash strictly matches current verified quote hash.
        """
        if not shop_open:
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason="Shop is currently closed for incoming orders",
                allowed=False,
            )

        if not slot_available:
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason="Requested pickup slot is fully booked at kitchen capacity",
                allowed=False,
            )

        if not can_transition(current_state, OrderAction.SEND_LINK):
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason=f"Cannot send payment link from order state '{current_state.value}' (must be in QUOTED state)",
                allowed=False,
            )

        if provided_quote_hash != current_quote.quote_hash:
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason="Quote hash mismatch: cart or pricing was modified after spoken readback. Re-quote required.",
                allowed=False,
                metadata={
                    "provided_hash": provided_quote_hash,
                    "expected_hash": current_quote.quote_hash,
                },
            )

        # Enforce policy limits on quote amount
        quote_eval = self.evaluate_quote(current_quote)
        if not quote_eval.allowed:
            return quote_eval

        return GuardDecision(
            kind=GuardDecisionKind.ALLOW,
            reason="Quote hash verified and operational parameters satisfied",
            allowed=True,
            computed_amount_cents=current_quote.total_cents,
            target_state=OrderState.LINK_SENT,
        )

    def evaluate_charge_saved_payment(
        self,
        current_state: OrderState,
        provided_quote_hash: str,
        current_quote: Quote,
        mandate: Optional[VaultMandate],
        spoken_consent_recorded: bool,
        shop_open: bool = True,
        slot_available: bool = True,
    ) -> GuardDecision:
        """
        Evaluate eligibility for voice-confirmed charging of a vaulted PayPal wallet.
        Enforces strict authorization ceiling, matching shop, valid quote hash,
        and immutable audit record of spoken consent.
        """
        if not shop_open:
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason="Shop is currently closed for incoming orders",
                allowed=False,
            )

        if not slot_available:
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason="Requested pickup slot is fully booked at kitchen capacity",
                allowed=False,
            )

        if not can_transition(current_state, OrderAction.CHARGE_SAVED_PAYMENT):
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason=f"Cannot charge saved payment from order state '{current_state.value}' (must be in QUOTED state)",
                allowed=False,
            )

        if not mandate or mandate.status != "ACTIVE":
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason="No active PayPal vault mandate found for this customer. Fallback to payment link required.",
                allowed=False,
            )

        if mandate.shop_id != current_quote.shop_id:
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason="Vault mandate is restricted to another restaurant and cannot be charged here",
                allowed=False,
            )

        if current_quote.total_cents > mandate.max_charge_cents:
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason=f"Order total of {current_quote.total_cents} cents exceeds customer's authorized vault mandate cap of {mandate.max_charge_cents} cents. Fallback to payment link required.",
                allowed=False,
                metadata={"mandate_cap_cents": mandate.max_charge_cents, "order_total_cents": current_quote.total_cents},
            )

        if provided_quote_hash != current_quote.quote_hash:
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason="Quote hash mismatch: cart was modified after spoken readback. Re-quote required.",
                allowed=False,
            )

        if not spoken_consent_recorded:
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason="Voice consent not verified. Caller must verbally confirm charge before charging saved PayPal.",
                allowed=False,
            )

        return GuardDecision(
            kind=GuardDecisionKind.ALLOW,
            reason=f"Voice-confirmed vault charge of {current_quote.total_cents} cents authorized under mandate {mandate.id}",
            allowed=True,
            computed_amount_cents=current_quote.total_cents,
            target_state=OrderState.CAPTURED,
            metadata={"vault_id": mandate.vault_id, "mandate_id": mandate.id},
        )

    def evaluate_refund(
        self,
        current_state: OrderState,
        captured_amount_cents: int,
        caller_authenticated: bool = True,
    ) -> GuardDecision:
        """
        Determine refund eligibility and amount according to state-dependent shop policy:
        - CAPTURED (before kitchen): Eligible for 100% full refund.
        - IN_KITCHEN (during prep): Eligible for partial refund (partial_refund_pct).
        - Above refund_approval_threshold_cents: Requires owner approval.
        - Other states: Denied.
        """
        if not caller_authenticated:
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason="Caller phone number does not match original ordering caller",
                allowed=False,
            )

        if not can_transition(current_state, OrderAction.REFUND):
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason=f"Refunds are not permitted from state '{current_state.value}'. Only CAPTURED and IN_KITCHEN orders can be refunded.",
                allowed=False,
            )

        if current_state == OrderState.CAPTURED:
            # 100% full refund
            refund_amount_cents = captured_amount_cents
            target = OrderState.REFUNDED_FULL
            refund_desc = "Full refund (prior to kitchen preparation)"
        else:
            # Policy-based partial refund (e.g. 50%) for IN_KITCHEN
            refund_amount_cents = (captured_amount_cents * self.policy.partial_refund_pct) // 100
            target = OrderState.REFUNDED_PARTIAL
            refund_desc = f"Partial refund of {self.policy.partial_refund_pct}% (food preparation already underway)"

        # Check approval threshold
        if refund_amount_cents > self.policy.refund_approval_threshold_cents:
            return GuardDecision(
                kind=GuardDecisionKind.NEEDS_APPROVAL,
                reason=f"{refund_desc} of {refund_amount_cents} cents exceeds auto-refund threshold of {self.policy.refund_approval_threshold_cents} cents. Routed to owner approval queue.",
                allowed=False,
                computed_amount_cents=refund_amount_cents,
                target_state=target,
                metadata={"captured_cents": captured_amount_cents, "threshold_cents": self.policy.refund_approval_threshold_cents},
            )

        return GuardDecision(
            kind=GuardDecisionKind.ALLOW,
            reason=f"{refund_desc} of {refund_amount_cents} cents within policy limits",
            allowed=True,
            computed_amount_cents=refund_amount_cents,
            target_state=target,
            metadata={"captured_cents": captured_amount_cents},
        )

    def evaluate_transition(
        self,
        current_state: OrderState,
        action: OrderAction,
    ) -> GuardDecision:
        """General state transition check against the deterministic FSM."""
        if not can_transition(current_state, action):
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason=f"Transition '{action.value}' not allowed from state '{current_state.value}'",
                allowed=False,
            )

        target_state = get_next_state(current_state, action)
        return GuardDecision(
            kind=GuardDecisionKind.ALLOW,
            reason=f"Valid transition from {current_state.value} to {target_state.value}",
            allowed=True,
            target_state=target_state,
        )
