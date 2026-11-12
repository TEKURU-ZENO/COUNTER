"""
Guard: the deterministic policy check every money-moving action passes before it runs.

The guard takes *records*, not booleans. For a vault charge it is handed the stored consent
event and the stored step-up verification and checks them itself (right order, right quote,
right amount, recent enough), so no caller of the function can assert "consent happened".
"""

from __future__ import annotations

from datetime import datetime

from packages.domain.fsm import POLICY_REFUND_STATES, can_transition, get_next_state
from packages.domain.models import (
    ConsentDecision,
    ConsentRecord,
    GuardDecision,
    GuardDecisionKind,
    OrderAction,
    OrderState,
    Quote,
    ShopPolicy,
    StepUpRecord,
    VaultMandate,
)


def _deny(reason: str, **metadata: object) -> GuardDecision:
    return GuardDecision(kind=GuardDecisionKind.DENY, reason=reason, allowed=False, metadata=dict(metadata))


class Guard:
    def __init__(self, policy: ShopPolicy | None = None):
        self.policy = policy or ShopPolicy()

    # ------------------------------------------------------------------ quotes

    def evaluate_quote(self, quote: Quote) -> GuardDecision:
        if quote.total_cents <= 0:
            return _deny("Cart is empty")
        if quote.total_cents > self.policy.max_order_value_cents:
            return GuardDecision(
                kind=GuardDecisionKind.DENY,
                reason=(
                    f"Order total {quote.total_cents} exceeds the maximum order value "
                    f"{self.policy.max_order_value_cents}"
                ),
                allowed=False,
                computed_amount_cents=quote.total_cents,
                metadata={"threshold_cents": self.policy.max_order_value_cents},
            )
        if quote.total_cents >= self.policy.catering_threshold_cents:
            return GuardDecision(
                kind=GuardDecisionKind.NEEDS_FLOW,
                reason=(
                    f"Order total {quote.total_cents} meets the catering threshold "
                    f"{self.policy.catering_threshold_cents}; a deposit invoice is required"
                ),
                allowed=False,
                computed_amount_cents=quote.total_cents,
                target_state=OrderState.CONFIRMED_CATERING,
                metadata={"flow": "catering"},
            )
        return GuardDecision(
            kind=GuardDecisionKind.ALLOW,
            reason="Quote within policy",
            allowed=True,
            computed_amount_cents=quote.total_cents,
        )

    # ------------------------------------------------------------------ payment link

    def evaluate_send_payment_link(
        self,
        *,
        current_state: OrderState,
        provided_quote_hash: str,
        current_quote: Quote,
        slot_available: bool,
        shop_open: bool,
    ) -> GuardDecision:
        if not shop_open:
            return _deny("Shop is closed")
        if not slot_available:
            return _deny("No pickup slot capacity")
        if not can_transition(current_state, OrderAction.SEND_LINK):
            return _deny(f"Cannot send a payment link from state {current_state.value}")
        if provided_quote_hash != current_quote.quote_hash:
            return _deny("Quote fingerprint mismatch: the cart changed after the read-back")
        quote_eval = self.evaluate_quote(current_quote)
        if not quote_eval.allowed:
            return quote_eval
        return GuardDecision(
            kind=GuardDecisionKind.ALLOW,
            reason="Quote verified",
            allowed=True,
            computed_amount_cents=current_quote.total_cents,
            target_state=OrderState.LINK_SENT,
        )

    # ------------------------------------------------------------------ saved wallet

    def evaluate_charge_saved_payment(
        self,
        *,
        current_state: OrderState,
        provided_quote_hash: str,
        current_quote: Quote,
        mandate: VaultMandate | None,
        consent: ConsentRecord | None,
        step_up: StepUpRecord | None,
        vault_charges_today: int,
        call_id: str,
        order_id: str,
        now: datetime,
        slot_available: bool,
        shop_open: bool,
    ) -> GuardDecision:
        if not shop_open:
            return _deny("Shop is closed")
        if not slot_available:
            return _deny("No pickup slot capacity")
        if not can_transition(current_state, OrderAction.CHARGE_SAVED_PAYMENT):
            return _deny(f"Cannot charge a saved wallet from state {current_state.value}")
        if mandate is None or mandate.status != "ACTIVE":
            return _deny("No active saved-PayPal mandate for this caller")
        if mandate.shop_id != current_quote.shop_id:
            return _deny("Mandate belongs to another shop")
        if current_quote.total_cents > mandate.max_charge_cents:
            return _deny(
                "Order total exceeds the mandate cap",
                cap_cents=mandate.max_charge_cents,
                total_cents=current_quote.total_cents,
            )
        if provided_quote_hash != current_quote.quote_hash:
            return _deny("Quote fingerprint mismatch: the cart changed after the read-back")
        quote_eval = self.evaluate_quote(current_quote)
        if not quote_eval.allowed:
            return quote_eval

        if vault_charges_today >= self.policy.max_vault_charges_per_day:
            return _deny("Daily saved-wallet charge limit reached for this mandate")

        if step_up is None or step_up.call_id != call_id or step_up.mandate_id != mandate.id:
            return _deny("Possession check (SMS code) not completed in this call")

        if consent is None:
            return _deny("No recorded spoken consent")
        if consent.order_id != order_id or consent.call_id != call_id:
            return _deny("Consent was recorded for a different order or call")
        if consent.decision != ConsentDecision.YES:
            return _deny(f"Recorded consent is '{consent.decision.value}', not 'yes'")
        if consent.quote_hash != current_quote.quote_hash:
            return _deny("Consent was given for a different quote")
        if consent.amount_cents != current_quote.total_cents:
            return _deny("Consent was given for a different amount")
        age = (now - consent.recorded_at).total_seconds()
        if age < 0 or age > self.policy.consent_max_age_seconds:
            return _deny("Recorded consent is stale")

        return GuardDecision(
            kind=GuardDecisionKind.ALLOW,
            reason=f"Saved-wallet charge of {current_quote.total_cents} authorised under mandate {mandate.id}",
            allowed=True,
            computed_amount_cents=current_quote.total_cents,
            target_state=OrderState.CAPTURED,
            metadata={"vault_id": mandate.vault_id, "mandate_id": mandate.id},
        )

    # ------------------------------------------------------------------ refunds

    def evaluate_refund(
        self,
        *,
        current_state: OrderState,
        captured_amount_cents: int,
        already_refunded_cents: int,
        caller_authenticated: bool,
    ) -> GuardDecision:
        """Caller-initiated cancellation: amount comes from policy, never from the caller."""
        if not caller_authenticated:
            return _deny("Caller is not the person who placed the order")
        if current_state not in POLICY_REFUND_STATES:
            return _deny(f"Refunds by phone are only possible before pickup, not in {current_state.value}")

        remaining = captured_amount_cents - already_refunded_cents
        if current_state == OrderState.CAPTURED:
            amount = remaining
            desc = "Full refund before kitchen preparation"
        else:
            amount = min(remaining, captured_amount_cents * self.policy.partial_refund_pct // 100)
            desc = f"Partial refund of {self.policy.partial_refund_pct}% (preparation started)"
        return self._refund_decision(amount, remaining, desc, captured_amount_cents)

    def evaluate_owner_refund(
        self,
        *,
        current_state: OrderState,
        requested_cents: int,
        captured_amount_cents: int,
        already_refunded_cents: int,
    ) -> GuardDecision:
        """Owner-initiated refund of an arbitrary amount, bounded by what is left to refund."""
        if not (
            can_transition(current_state, OrderAction.RECORD_PARTIAL_REFUND)
            or can_transition(current_state, OrderAction.RECORD_FULL_REFUND)
        ):
            return _deny(f"Order in state {current_state.value} cannot be refunded")
        remaining = captured_amount_cents - already_refunded_cents
        if requested_cents <= 0:
            return _deny("Refund amount must be positive")
        if requested_cents > remaining:
            return _deny(
                f"Refund {requested_cents} exceeds the remaining refundable {remaining}",
                remaining_cents=remaining,
            )
        return GuardDecision(
            kind=GuardDecisionKind.ALLOW,
            reason="Owner refund within the captured amount",
            allowed=True,
            computed_amount_cents=requested_cents,
            metadata={"captured_cents": captured_amount_cents, "remaining_cents": remaining},
        )

    def _refund_decision(self, amount: int, remaining: int, desc: str, captured: int) -> GuardDecision:
        if amount <= 0:
            return _deny("Nothing left to refund", remaining_cents=remaining)
        meta = {"captured_cents": captured, "remaining_cents": remaining}
        if amount > self.policy.refund_approval_threshold_cents:
            return GuardDecision(
                kind=GuardDecisionKind.NEEDS_APPROVAL,
                reason=f"{desc} of {amount} exceeds the auto-refund threshold; owner approval required",
                allowed=False,
                computed_amount_cents=amount,
                metadata=meta,
            )
        return GuardDecision(
            kind=GuardDecisionKind.ALLOW,
            reason=f"{desc} of {amount}",
            allowed=True,
            computed_amount_cents=amount,
            metadata=meta,
        )

    # ------------------------------------------------------------------ generic

    def evaluate_transition(self, current_state: OrderState, action: OrderAction) -> GuardDecision:
        if not can_transition(current_state, action):
            return _deny(f"Transition '{action.value}' not allowed from '{current_state.value}'")
        target = get_next_state(current_state, action)
        return GuardDecision(
            kind=GuardDecisionKind.ALLOW,
            reason=f"{current_state.value} -> {target.value}",
            allowed=True,
            target_state=target,
        )
