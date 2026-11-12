"""Guard, consent, reconciliation and money-formatting tests (pure domain, no I/O)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from packages.domain.consent import classify_consent, consent_prompt, quote_short_code
from packages.domain.guard import Guard
from packages.domain.models import (
    CartItem,
    ConsentDecision,
    ConsentRecord,
    GuardDecisionKind,
    MenuItem,
    ModifierSelection,
    OrderState,
    ShopPolicy,
    StepUpRecord,
    VaultMandate,
)
from packages.domain.money import (
    AmountMismatchError,
    MoneyError,
    PayPalLine,
    build_purchase_unit,
    cents_to_value,
    value_to_cents,
)
from packages.domain.pricing import calculate_quote
from packages.domain.reconcile import CaptureOutcome, reconcile_capture

NOW = datetime(2026, 11, 12, 18, 0, 0)
MENU = {
    "pizza": MenuItem(id="pizza", shop_id="shop_1", name="Margherita", category="Pizza", price_cents=1450),
}


def _quote(qty: int = 1):
    return calculate_quote("shop_1", [CartItem("pizza", "Margherita", qty)], MENU)


def _charge_kwargs(quote, **overrides):
    mandate = VaultMandate(id="m1", vault_id="v1", customer_hash="h", shop_id="shop_1", max_charge_cents=5000)
    base = dict(
        current_state=OrderState.QUOTED,
        provided_quote_hash=quote.quote_hash,
        current_quote=quote,
        mandate=mandate,
        consent=ConsentRecord("o1", "c1", quote.quote_hash, quote.total_cents, ConsentDecision.YES, NOW),
        step_up=StepUpRecord("c1", "m1", NOW - timedelta(minutes=1)),
        vault_charges_today=0,
        call_id="c1",
        order_id="o1",
        now=NOW + timedelta(seconds=10),
        slot_available=True,
        shop_open=True,
    )
    base.update(overrides)
    return base


# ------------------------------------------------------------------ vault charge guard


def test_vault_charge_allowed_only_with_all_records():
    q = _quote()
    decision = Guard().evaluate_charge_saved_payment(**_charge_kwargs(q))
    assert decision.allowed and decision.kind == GuardDecisionKind.ALLOW
    assert decision.computed_amount_cents == q.total_cents


@pytest.mark.parametrize(
    "override_factory,reason_fragment",
    [
        (lambda q: {"consent": None}, "No recorded spoken consent"),
        (lambda q: {"consent": ConsentRecord("o1", "c1", q.quote_hash, q.total_cents, ConsentDecision.NO, NOW)}, "'no'"),
        (lambda q: {"consent": ConsentRecord("o1", "c1", q.quote_hash, q.total_cents, ConsentDecision.UNCLEAR, NOW)}, "'unclear'"),
        (lambda q: {"consent": ConsentRecord("o1", "c1", "other", q.total_cents, ConsentDecision.YES, NOW)}, "different quote"),
        (lambda q: {"consent": ConsentRecord("o1", "c1", q.quote_hash, q.total_cents + 1, ConsentDecision.YES, NOW)}, "different amount"),
        (lambda q: {"consent": ConsentRecord("o2", "c1", q.quote_hash, q.total_cents, ConsentDecision.YES, NOW)}, "different order"),
        (lambda q: {"consent": ConsentRecord("o1", "c9", q.quote_hash, q.total_cents, ConsentDecision.YES, NOW)}, "different order or call"),
        (lambda q: {"now": NOW + timedelta(minutes=10)}, "stale"),
        (lambda q: {"step_up": None}, "Possession check"),
        (lambda q: {"step_up": StepUpRecord("other_call", "m1", NOW)}, "Possession check"),
        (lambda q: {"vault_charges_today": 3}, "Daily"),
        (lambda q: {"mandate": None}, "No active"),
        (lambda q: {"provided_quote_hash": "tampered"}, "fingerprint mismatch"),
        (lambda q: {"current_state": OrderState.LINK_SENT}, "Cannot charge"),
        (lambda q: {"shop_open": False}, "closed"),
        (lambda q: {"slot_available": False}, "capacity"),
    ],
)
def test_vault_charge_denials(override_factory, reason_fragment):
    q = _quote()
    decision = Guard().evaluate_charge_saved_payment(**_charge_kwargs(q, **override_factory(q)))
    assert not decision.allowed
    assert reason_fragment in decision.reason


def test_vault_charge_denied_over_cap_and_other_shop():
    q = _quote(qty=4)  # 6278 > 5000 cap
    assert "cap" in Guard().evaluate_charge_saved_payment(**_charge_kwargs(q)).reason
    q1 = _quote()
    other = VaultMandate(id="m1", vault_id="v1", customer_hash="h", shop_id="shop_2")
    assert "another shop" in Guard().evaluate_charge_saved_payment(**_charge_kwargs(q1, mandate=other)).reason


def test_guard_signature_has_no_consent_boolean():
    import inspect

    params = inspect.signature(Guard.evaluate_charge_saved_payment).parameters
    assert "spoken_consent_recorded" not in params
    assert all(p.default is inspect.Parameter.empty for name, p in params.items() if name != "self")


# ------------------------------------------------------------------ refunds


def test_refund_policy_amounts_and_threshold():
    g = Guard(ShopPolicy(partial_refund_pct=50, refund_approval_threshold_cents=2500))
    full = g.evaluate_refund(current_state=OrderState.CAPTURED, captured_amount_cents=2000,
                             already_refunded_cents=0, caller_authenticated=True)
    assert full.allowed and full.computed_amount_cents == 2000
    partial = g.evaluate_refund(current_state=OrderState.IN_KITCHEN, captured_amount_cents=2000,
                                already_refunded_cents=0, caller_authenticated=True)
    assert partial.computed_amount_cents == 1000
    big = g.evaluate_refund(current_state=OrderState.CAPTURED, captured_amount_cents=4000,
                            already_refunded_cents=0, caller_authenticated=True)
    assert big.kind == GuardDecisionKind.NEEDS_APPROVAL
    assert not g.evaluate_refund(current_state=OrderState.CAPTURED, captured_amount_cents=2000,
                                 already_refunded_cents=0, caller_authenticated=False).allowed
    assert not g.evaluate_refund(current_state=OrderState.READY, captured_amount_cents=2000,
                                 already_refunded_cents=0, caller_authenticated=True).allowed


def test_refund_accounts_for_prior_refunds():
    g = Guard()
    d = g.evaluate_refund(current_state=OrderState.CAPTURED, captured_amount_cents=2000,
                          already_refunded_cents=2000, caller_authenticated=True)
    assert not d.allowed and "Nothing left" in d.reason
    owner = g.evaluate_owner_refund(current_state=OrderState.REFUNDED_PARTIAL, requested_cents=600,
                                    captured_amount_cents=2000, already_refunded_cents=1500)
    assert not owner.allowed and "exceeds the remaining" in owner.reason
    ok = g.evaluate_owner_refund(current_state=OrderState.REFUNDED_PARTIAL, requested_cents=500,
                                 captured_amount_cents=2000, already_refunded_cents=1500)
    assert ok.allowed


@given(
    captured=st.integers(min_value=1, max_value=200_000),
    already=st.integers(min_value=0, max_value=200_000),
    pct=st.integers(min_value=1, max_value=100),
    state=st.sampled_from([OrderState.CAPTURED, OrderState.IN_KITCHEN]),
)
def test_property_refund_never_exceeds_remaining(captured, already, pct, state):
    already = min(already, captured)
    d = Guard(ShopPolicy(partial_refund_pct=pct)).evaluate_refund(
        current_state=state, captured_amount_cents=captured,
        already_refunded_cents=already, caller_authenticated=True,
    )
    if d.computed_amount_cents is not None:
        assert 0 < d.computed_amount_cents <= captured - already


@given(captured=st.integers(1, 100_000), already=st.integers(0, 100_000), requested=st.integers(-10, 200_000))
def test_property_owner_refund_bounded(captured, already, requested):
    already = min(already, captured)
    d = Guard().evaluate_owner_refund(current_state=OrderState.CAPTURED, requested_cents=requested,
                                      captured_amount_cents=captured, already_refunded_cents=already)
    if d.allowed:
        assert 0 < requested <= captured - already


# ------------------------------------------------------------------ consent


@pytest.mark.parametrize("reply", ["yes", "Yes please.", "yeah go ahead", "sure", "okay, charge it", "correct"])
def test_consent_yes(reply):
    assert classify_consent(reply) == ConsentDecision.YES


@pytest.mark.parametrize(
    "reply",
    ["no", "No thanks", "yes no wait", "don't", "not yet", "cancel that", "hold on", "yes, but not that card"],
)
def test_consent_no(reply):
    assert classify_consent(reply) == ConsentDecision.NO


@pytest.mark.parametrize(
    "reply", ["", "maybe", "how much was it?", "yes?", "um yes", "pizza", "I guess so",
              "yes I would like to also add a garlic bread and a tiramisu to that please"],
)
def test_consent_unclear(reply):
    assert classify_consent(reply) == ConsentDecision.UNCLEAR


@given(st.text(max_size=80))
def test_property_negated_reply_is_never_yes(text):
    assert classify_consent(f"{text} no {text}") != ConsentDecision.YES


def test_consent_prompt_contains_amount_and_short_code():
    q = _quote()
    prompt = consent_prompt(q.total_cents, q.quote_hash)
    code = quote_short_code(q.quote_hash)
    assert "$15.70" in prompt
    assert " ".join(code) in prompt
    assert len(code) == 4 and code.isdigit()


# ------------------------------------------------------------------ reconciliation


def test_reconcile_outcomes():
    base = dict(expected_order_id="o1", expected_total_cents=1570, expected_currency="USD",
                amount_value="15.70", currency="USD", custom_id="o1")
    assert reconcile_capture(status="COMPLETED", **base).outcome == CaptureOutcome.COMPLETED
    assert reconcile_capture(status="PENDING", **base).outcome == CaptureOutcome.PENDING
    assert reconcile_capture(status="DECLINED", **base).outcome == CaptureOutcome.DENIED
    assert reconcile_capture(status="COMPLETED", **{**base, "amount_value": "15.69"}).outcome == CaptureOutcome.MISMATCH
    assert reconcile_capture(status="COMPLETED", **{**base, "currency": "EUR"}).outcome == CaptureOutcome.MISMATCH
    assert reconcile_capture(status="COMPLETED", **{**base, "custom_id": "o2"}).outcome == CaptureOutcome.MISMATCH
    assert reconcile_capture(status="COMPLETED", **{**base, "amount_value": "abc"}).outcome == CaptureOutcome.MISMATCH


# ------------------------------------------------------------------ money


def test_money_conversion_examples():
    assert cents_to_value(0) == "0.00"
    assert cents_to_value(5) == "0.05"
    assert cents_to_value(1450) == "14.50"
    assert value_to_cents("14.5") == 1450
    assert value_to_cents("14") == 1400
    for bad in ("14.505", "-1.00", "1e3", " ", "01.00", "14,50"):
        with pytest.raises(MoneyError):
            value_to_cents(bad)
    with pytest.raises(MoneyError):
        cents_to_value(-1)
    with pytest.raises(MoneyError):
        cents_to_value(14.5)  # type: ignore[arg-type]


@given(st.integers(min_value=0, max_value=10**12))
def test_property_money_round_trip(cents):
    assert value_to_cents(cents_to_value(cents)) == cents


@given(
    prices=st.lists(st.integers(min_value=1, max_value=50_000), min_size=1, max_size=8),
    qtys=st.lists(st.integers(min_value=1, max_value=20), min_size=8, max_size=8),
    mods=st.lists(st.integers(min_value=0, max_value=2_000), min_size=8, max_size=8),
    tax_bps=st.integers(min_value=0, max_value=2_500),
)
def test_property_purchase_unit_breakdown_is_exact(prices, qtys, mods, tax_bps):
    """For any cart the pricing engine produces, the PayPal breakdown adds up exactly."""
    menu = {f"i{n}": MenuItem(id=f"i{n}", shop_id="s", name=f"Item {n}", category="c", price_cents=p)
            for n, p in enumerate(prices)}
    cart = [
        CartItem(f"i{n}", f"Item {n}", qtys[n], (ModifierSelection("m", "Mod", mods[n]),) if mods[n] else ())
        for n in range(len(prices))
    ]
    quote = calculate_quote("s", cart, menu, tax_rate_bps=tax_bps)
    lines = [PayPalLine(line.name, line.qty, line.unit_total_cents) for line in quote.lines]
    unit = build_purchase_unit(reference_id="o", currency="USD", lines=lines,
                               subtotal_cents=quote.subtotal_cents, tax_cents=quote.tax_cents,
                               total_cents=quote.total_cents)
    amount = unit["amount"]
    item_total = value_to_cents(amount["breakdown"]["item_total"]["value"])
    tax_total = value_to_cents(amount["breakdown"]["tax_total"]["value"])
    assert item_total + tax_total == value_to_cents(amount["value"]) == quote.total_cents
    assert sum(value_to_cents(i["unit_amount"]["value"]) * int(i["quantity"]) for i in unit["items"]) == item_total
    assert all(m["currency_code"] == "USD" for m in (amount, amount["breakdown"]["item_total"]))


def test_purchase_unit_rejects_mismatch_and_bad_currency():
    with pytest.raises(AmountMismatchError):
        build_purchase_unit(reference_id="o", currency="USD", lines=[], subtotal_cents=100, tax_cents=8, total_cents=109)
    with pytest.raises(AmountMismatchError):
        build_purchase_unit(reference_id="o", currency="USD", lines=[PayPalLine("x", 2, 50)],
                            subtotal_cents=101, tax_cents=0, total_cents=101)
    for currency in ("JPY", "usd", "US"):
        with pytest.raises(MoneyError):
            build_purchase_unit(reference_id="o", currency=currency, lines=[], subtotal_cents=1, tax_cents=0, total_cents=1)
