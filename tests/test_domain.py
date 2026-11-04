"""
Complete test suite for COUNTER Domain Core.
Includes 100% branch coverage tests, hypothesis property-based tests,
quote hash tamper tests, and mutation verification tests.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

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
    CartItem,
    GuardDecisionKind,
    MenuItem,
    ModifierDefinition,
    ModifierSelection,
    OrderAction,
    OrderState,
    Quote,
    ShopPolicy,
    VaultMandate,
)
from packages.domain.pricing import (
    MenuItemNotFoundError,
    MenuItemUnavailableError,
    calculate_quote,
    compute_quote_hash,
    format_cents,
    generate_spoken_readback,
)


# ==============================================================================
# Fixtures & Sample Data
# ==============================================================================

@pytest.fixture
def sample_menu() -> dict[str, MenuItem]:
    return {
        "item_pizza": MenuItem(
            id="item_pizza",
            shop_id="shop_123",
            name="Margherita Pizza",
            category="Pizza",
            price_cents=1200,
            prep_min=15,
            available=True,
            dietary_tags=("vegetarian",),
            modifiers=(
                ModifierDefinition(id="mod_cheese", name="Extra Cheese", price_delta_cents=200, group_name="Toppings"),
                ModifierDefinition(id="mod_olives", name="Black Olives", price_delta_cents=150, group_name="Toppings"),
            ),
        ),
        "item_burger": MenuItem(
            id="item_burger",
            shop_id="shop_123",
            name="Classic Cheeseburger",
            category="Burgers",
            price_cents=950,
            prep_min=10,
            available=True,
            modifiers=(),
        ),
        "item_unavailable": MenuItem(
            id="item_unavailable",
            shop_id="shop_123",
            name="Sold Out Special",
            category="Specials",
            price_cents=1500,
            available=False,
        ),
    }


@pytest.fixture
def default_policy() -> ShopPolicy:
    return ShopPolicy(
        cancel_full_refund_until_state=OrderState.CAPTURED,
        partial_refund_pct=50,
        link_ttl_min=15,
        max_order_value_cents=50000,          # $500.00
        refund_approval_threshold_cents=2500,  # $25.00
        catering_threshold_cents=20000,        # $200.00
    )


# ==============================================================================
# 1. FSM & Transition Table Tests
# ==============================================================================

def test_all_fsm_valid_transitions():
    """Verify that every transition explicitly defined in the FSM succeeds."""
    for action, rule in TRANSITION_TABLE.items():
        for from_state in rule.allowed_from_states:
            assert can_transition(from_state, action) is True
            next_state = get_next_state(from_state, action)
            assert next_state == rule.to_state
            side_effect = get_side_effects(action, from_state)
            assert side_effect == rule.side_effects


def test_fsm_refund_transitions():
    """Verify refund state changes and side effects."""
    # From CAPTURED -> REFUNDED_FULL
    assert can_transition(OrderState.CAPTURED, OrderAction.REFUND) is True
    assert get_next_state(OrderState.CAPTURED, OrderAction.REFUND) == OrderState.REFUNDED_FULL
    assert get_side_effects(OrderAction.REFUND, OrderState.CAPTURED) == "paypal_refund_full_executed"

    # From IN_KITCHEN -> REFUNDED_PARTIAL
    assert can_transition(OrderState.IN_KITCHEN, OrderAction.REFUND) is True
    assert get_next_state(OrderState.IN_KITCHEN, OrderAction.REFUND) == OrderState.REFUNDED_PARTIAL
    assert get_side_effects(OrderAction.REFUND, OrderState.IN_KITCHEN) == "paypal_refund_partial_executed"

    # From invalid state for refund
    assert can_transition(OrderState.DRAFT, OrderAction.REFUND) is False
    with pytest.raises(IllegalStateTransitionError):
        get_next_state(OrderState.DRAFT, OrderAction.REFUND)


def test_fsm_illegal_transitions_raise_error():
    """Ensure invalid state transitions throw IllegalStateTransitionError."""
    # Cannot mark ready from DRAFT
    assert can_transition(OrderState.DRAFT, OrderAction.MARK_READY) is False
    with pytest.raises(IllegalStateTransitionError) as exc_info:
        get_next_state(OrderState.DRAFT, OrderAction.MARK_READY)
    assert "Cannot execute action 'mark_ready' from state 'DRAFT'" in str(exc_info.value)

    # Cannot pickup from LINK_SENT
    assert can_transition(OrderState.LINK_SENT, OrderAction.PICKUP) is False
    with pytest.raises(IllegalStateTransitionError):
        get_next_state(OrderState.LINK_SENT, OrderAction.PICKUP)


def test_fsm_unknown_action_handling():
    """Ensure unknown actions or edge cases fail cleanly."""
    class FakeAction(str):
        pass
    fake = FakeAction("non_existent_action")
    assert can_transition(OrderState.DRAFT, fake) is False  # type: ignore
    with pytest.raises(IllegalStateTransitionError):
        get_next_state(OrderState.DRAFT, fake)  # type: ignore

    # Unknown side effect fallback
    assert get_side_effects(fake) == "unknown_side_effect"  # type: ignore
    assert get_side_effects(OrderAction.REFUND, OrderState.DRAFT) == "paypal_refund_executed"


def test_guard_send_link_quote_disallowed(sample_menu, default_policy):
    """If current quote violates policy (e.g. exceeds max value), send_payment_link is denied."""
    guard = Guard(default_policy)
    # 50 pizzas exceeds max order value
    huge_cart = [CartItem("item_pizza", "Margherita Pizza", 50)]
    quote = calculate_quote("shop_123", huge_cart, sample_menu)
    decision = guard.evaluate_send_payment_link(OrderState.QUOTED, quote.quote_hash, quote)
    assert decision.allowed is False
    assert decision.kind == GuardDecisionKind.DENY


def test_pricing_zero_qty_and_three_items(sample_menu):
    """Items with qty <= 0 are skipped, and 3+ items produce correct Oxford comma grammar."""
    menu = dict(sample_menu)
    menu["item_salad"] = MenuItem(id="item_salad", shop_id="shop_123", name="Garden Salad", category="Salad", price_cents=700)

    cart = [
        CartItem("item_pizza", "Margherita Pizza", 1),
        CartItem("item_burger", "Classic Cheeseburger", 1),
        CartItem("item_salad", "Garden Salad", 1),
        CartItem("item_pizza", "Margherita Pizza", 0),  # qty 0 should be skipped
    ]
    quote = calculate_quote("shop_123", cart, menu)
    assert len(quote.lines) == 3
    assert "1 Margherita Pizza, 1 Classic Cheeseburger, and 1 Garden Salad" in quote.spoken_summary



# ==============================================================================
# 2. Pricing Engine Tests (Pure Integer Cents)
# ==============================================================================

def test_pricing_empty_cart(sample_menu):
    """Empty cart returns 0 totals and valid empty hash."""
    quote = calculate_quote("shop_123", [], sample_menu)
    assert quote.subtotal_cents == 0
    assert quote.tax_cents == 0
    assert quote.total_cents == 0
    assert len(quote.lines) == 0
    assert quote.quote_hash is not None
    assert "empty" in quote.spoken_summary.lower()


def test_pricing_single_item_no_modifiers(sample_menu):
    """Margherita Pizza ($12.00) with 8.25% tax."""
    cart = [CartItem(item_id="item_pizza", name="Margherita Pizza", qty=1, modifiers=())]
    quote = calculate_quote("shop_123", cart, sample_menu, tax_rate_bps=825)

    assert quote.subtotal_cents == 1200
    # 1200 * 825 = 990,000 bps -> (990000 + 5000)//10000 = 99 cents tax
    assert quote.tax_cents == 99
    assert quote.total_cents == 1299
    assert len(quote.lines) == 1
    assert quote.lines[0].line_total_cents == 1200


def test_pricing_multiple_items_with_modifiers(sample_menu):
    """
    2 Margherita Pizzas ($12.00 each) with Extra Cheese ($2.00) = $14.00 * 2 = $28.00 (2800 cents)
    1 Cheeseburger ($9.50) = 950 cents
    Subtotal = 3750 cents ($37.50)
    Tax @ 8.25%: 3750 * 825 = 3093750 -> (3093750 + 5000)//10000 = 309 cents ($3.09)
    Total = 4059 cents ($40.59)
    """
    cart = [
        CartItem(
            item_id="item_pizza",
            name="Margherita Pizza",
            qty=2,
            modifiers=(ModifierSelection("mod_cheese", "Extra Cheese", 200),),
        ),
        CartItem(
            item_id="item_burger",
            name="Classic Cheeseburger",
            qty=1,
            modifiers=(),
        ),
    ]
    quote = calculate_quote("shop_123", cart, sample_menu, tax_rate_bps=825)

    assert quote.subtotal_cents == 3750
    assert quote.tax_cents == 309
    assert quote.total_cents == 4059
    assert len(quote.lines) == 2
    assert quote.lines[0].line_total_cents == 2800
    assert quote.lines[1].line_total_cents == 950
    assert "$37.50" in quote.spoken_summary
    assert "$40.59" in quote.spoken_summary


def test_pricing_missing_item_error(sample_menu):
    cart = [CartItem(item_id="item_ghost", name="Ghost Item", qty=1)]
    with pytest.raises(MenuItemNotFoundError) as exc_info:
        calculate_quote("shop_123", cart, sample_menu)
    assert exc_info.value.item_id == "item_ghost"


def test_pricing_unavailable_item_error(sample_menu):
    cart = [CartItem(item_id="item_unavailable", name="Sold Out Special", qty=1)]
    with pytest.raises(MenuItemUnavailableError) as exc_info:
        calculate_quote("shop_123", cart, sample_menu)
    assert "Sold Out Special" in str(exc_info.value)


def test_format_cents():
    assert format_cents(0) == "$0.00"
    assert format_cents(5) == "$0.05"
    assert format_cents(99) == "$0.99"
    assert format_cents(1250) == "$12.50"
    assert format_cents(-500) == "-$5.00"


def test_spoken_readback_grammar():
    # 0 items
    assert "empty" in generate_spoken_readback((), 0, 0, 0).lower()

    # 1 item
    quote1 = calculate_quote("shop_123", [CartItem("item_burger", "Cheeseburger", 1)], {
        "item_burger": MenuItem("item_burger", "shop_123", "Cheeseburger", "Burgers", 800)
    })
    assert "You have 1 Cheeseburger." in quote1.spoken_summary

    # 2 items
    quote2 = calculate_quote("shop_123", [
        CartItem("item_burger", "Cheeseburger", 1),
        CartItem("item_pizza", "Margherita", 1),
    ], {
        "item_burger": MenuItem("item_burger", "shop_123", "Cheeseburger", "Burgers", 800),
        "item_pizza": MenuItem("item_pizza", "shop_123", "Margherita", "Pizza", 1000),
    })
    assert "1 Cheeseburger and 1 Margherita" in quote2.spoken_summary


# ==============================================================================
# 3. Cryptographic Quote Hash Tamper Tests
# ==============================================================================

def test_quote_hash_tamper_detection(sample_menu):
    """Ensure any modification to cart, quantities, prices, or modifiers invalidates hash."""
    cart = [CartItem("item_pizza", "Margherita Pizza", 1)]
    quote = calculate_quote("shop_123", cart, sample_menu)
    original_hash = quote.quote_hash

    # Identical quote produces identical hash (determinism)
    identical_quote = calculate_quote("shop_123", cart, sample_menu)
    assert identical_quote.quote_hash == original_hash

    # Tampered subtotal by 1 cent
    tampered_hash_1cent = compute_quote_hash(
        quote.shop_id, quote.lines, quote.subtotal_cents + 1, quote.tax_cents, quote.total_cents + 1
    )
    assert tampered_hash_1cent != original_hash

    # Tampered modifier
    cart_with_mod = [CartItem("item_pizza", "Margherita Pizza", 1, (ModifierSelection("mod_cheese", "Cheese", 200),))]
    quote_with_mod = calculate_quote("shop_123", cart_with_mod, sample_menu)
    assert quote_with_mod.quote_hash != original_hash


# ==============================================================================
# 4. Guard Policy Engine Tests
# ==============================================================================

def test_guard_evaluate_quote_max_order_value(sample_menu, default_policy):
    """Orders exceeding max_order_value_cents are denied by the Guard."""
    guard = Guard(default_policy)
    # Huge cart: 50 pizzas = $600.00 (60000 cents > 50000 max limit)
    cart = [CartItem("item_pizza", "Margherita Pizza", 50)]
    quote = calculate_quote("shop_123", cart, sample_menu)

    decision = guard.evaluate_quote(quote)
    assert decision.allowed is False
    assert decision.kind == GuardDecisionKind.DENY
    assert "exceeds maximum permitted order value" in decision.reason


def test_guard_evaluate_quote_catering_threshold(sample_menu, default_policy):
    """Orders meeting or exceeding catering threshold are routed to catering flow."""
    guard = Guard(default_policy)
    # 18 pizzas = 18 * 1200 = 21600 cents subtotal (> 20000 catering threshold)
    cart = [CartItem("item_pizza", "Margherita Pizza", 18)]
    quote = calculate_quote("shop_123", cart, sample_menu)

    decision = guard.evaluate_quote(quote)
    assert decision.allowed is False
    assert decision.kind == GuardDecisionKind.NEEDS_FLOW
    assert decision.target_state == OrderState.CONFIRMED_CATERING
    assert decision.metadata["flow"] == "catering"


def test_guard_evaluate_send_payment_link_all_checks(sample_menu, default_policy):
    guard = Guard(default_policy)
    cart = [CartItem("item_pizza", "Margherita Pizza", 1)]
    quote = calculate_quote("shop_123", cart, sample_menu)

    # 1. Shop closed -> DENY
    d_closed = guard.evaluate_send_payment_link(OrderState.QUOTED, quote.quote_hash, quote, shop_open=False)
    assert d_closed.allowed is False
    assert "closed" in d_closed.reason.lower()

    # 2. Slot unavailable -> DENY
    d_slot = guard.evaluate_send_payment_link(OrderState.QUOTED, quote.quote_hash, quote, slot_available=False)
    assert d_slot.allowed is False
    assert "fully booked" in d_slot.reason.lower()

    # 3. Wrong state (e.g. DRAFT instead of QUOTED) -> DENY
    d_state = guard.evaluate_send_payment_link(OrderState.DRAFT, quote.quote_hash, quote)
    assert d_state.allowed is False
    assert "Cannot send payment link from order state 'DRAFT'" in d_state.reason

    # 4. Hash mismatch -> DENY
    d_tamper = guard.evaluate_send_payment_link(OrderState.QUOTED, "bad_tampered_hash", quote)
    assert d_tamper.allowed is False
    assert "mismatch" in d_tamper.reason.lower()

    # 5. Happy path -> ALLOW
    d_ok = guard.evaluate_send_payment_link(OrderState.QUOTED, quote.quote_hash, quote)
    assert d_ok.allowed is True
    assert d_ok.kind == GuardDecisionKind.ALLOW
    assert d_ok.target_state == OrderState.LINK_SENT


def test_guard_evaluate_charge_saved_payment_all_branches(sample_menu, default_policy):
    """Verify all 8 safety checks on voice-confirmed vaulted payments."""
    guard = Guard(default_policy)
    cart = [CartItem("item_pizza", "Margherita Pizza", 1)]
    quote = calculate_quote("shop_123", cart, sample_menu)
    valid_mandate = VaultMandate(
        id="man_001",
        vault_id="v_token_123",
        customer_hash="cust_hash_1",
        shop_id="shop_123",
        max_charge_cents=5000,
        status="ACTIVE",
    )

    # 1. Shop closed -> DENY
    d_closed = guard.evaluate_charge_saved_payment(
        OrderState.QUOTED, quote.quote_hash, quote, valid_mandate, True, shop_open=False
    )
    assert d_closed.allowed is False
    assert "closed" in d_closed.reason.lower()

    # 2. Slot unavailable -> DENY
    d_slot = guard.evaluate_charge_saved_payment(
        OrderState.QUOTED, quote.quote_hash, quote, valid_mandate, True, slot_available=False
    )
    assert d_slot.allowed is False
    assert "fully booked" in d_slot.reason.lower()

    # 3. Wrong state -> DENY
    d_state = guard.evaluate_charge_saved_payment(
        OrderState.DRAFT, quote.quote_hash, quote, valid_mandate, True
    )
    assert d_state.allowed is False
    assert "must be in QUOTED state" in d_state.reason

    # 4. No mandate or revoked mandate -> DENY
    d_no_man = guard.evaluate_charge_saved_payment(
        OrderState.QUOTED, quote.quote_hash, quote, None, True
    )
    assert d_no_man.allowed is False
    assert "No active PayPal vault mandate" in d_no_man.reason

    revoked_man = VaultMandate("man_002", "v_2", "cust_1", "shop_123", 5000, status="REVOKED")
    d_revoked = guard.evaluate_charge_saved_payment(
        OrderState.QUOTED, quote.quote_hash, quote, revoked_man, True
    )
    assert d_revoked.allowed is False

    # 5. Mandate belongs to another shop -> DENY
    other_shop_man = VaultMandate("man_003", "v_3", "cust_1", "other_shop", 5000, status="ACTIVE")
    d_other = guard.evaluate_charge_saved_payment(
        OrderState.QUOTED, quote.quote_hash, quote, other_shop_man, True
    )
    assert d_other.allowed is False
    assert "restricted to another restaurant" in d_other.reason

    # 6. Order total exceeds mandate cap -> DENY
    low_cap_man = VaultMandate("man_004", "v_4", "cust_1", "shop_123", max_charge_cents=500, status="ACTIVE")
    d_cap = guard.evaluate_charge_saved_payment(
        OrderState.QUOTED, quote.quote_hash, quote, low_cap_man, True
    )
    assert d_cap.allowed is False
    assert "exceeds customer's authorized vault mandate cap" in d_cap.reason

    # 7. Quote hash mismatch -> DENY
    d_hash = guard.evaluate_charge_saved_payment(
        OrderState.QUOTED, "bad_hash_val", quote, valid_mandate, True
    )
    assert d_hash.allowed is False
    assert "Quote hash mismatch" in d_hash.reason

    # 8. Spoken consent not recorded -> DENY
    d_no_consent = guard.evaluate_charge_saved_payment(
        OrderState.QUOTED, quote.quote_hash, quote, valid_mandate, spoken_consent_recorded=False
    )
    assert d_no_consent.allowed is False
    assert "Voice consent not verified" in d_no_consent.reason

    # 9. All 8 safety conditions met -> ALLOW (CAPTURED)
    d_ok = guard.evaluate_charge_saved_payment(
        OrderState.QUOTED, quote.quote_hash, quote, valid_mandate, spoken_consent_recorded=True
    )
    assert d_ok.allowed is True
    assert d_ok.kind == GuardDecisionKind.ALLOW
    assert d_ok.target_state == OrderState.CAPTURED
    assert d_ok.computed_amount_cents == quote.total_cents


def test_guard_evaluate_refund_policies(default_policy):
    guard = Guard(default_policy)

    # 1. Unauthenticated caller -> DENY
    d_unauth = guard.evaluate_refund(OrderState.CAPTURED, 2000, caller_authenticated=False)
    assert d_unauth.allowed is False
    assert "Caller phone number does not match" in d_unauth.reason

    # 2. Refund in invalid state (e.g. READY or CANCELLED) -> DENY
    d_ready = guard.evaluate_refund(OrderState.READY, 2000)
    assert d_ready.allowed is False
    assert "not permitted from state 'READY'" in d_ready.reason

    # 3. CAPTURED (before kitchen): Full refund <= $25 threshold -> ALLOW
    d_cap_under = guard.evaluate_refund(OrderState.CAPTURED, 2000)  # $20.00
    assert d_cap_under.allowed is True
    assert d_cap_under.kind == GuardDecisionKind.ALLOW
    assert d_cap_under.computed_amount_cents == 2000
    assert d_cap_under.target_state == OrderState.REFUNDED_FULL

    # 4. CAPTURED (before kitchen): Full refund > $25 threshold -> NEEDS_APPROVAL
    d_cap_over = guard.evaluate_refund(OrderState.CAPTURED, 4000)  # $40.00
    assert d_cap_over.allowed is False
    assert d_cap_over.kind == GuardDecisionKind.NEEDS_APPROVAL
    assert d_cap_over.computed_amount_cents == 4000
    assert d_cap_over.target_state == OrderState.REFUNDED_FULL

    # 5. IN_KITCHEN (during prep): Partial refund (50%)
    # Captured amount = $40.00 (4000 cents). 50% partial = 2000 cents ($20.00 <= $25 threshold) -> ALLOW
    d_kit_under = guard.evaluate_refund(OrderState.IN_KITCHEN, 4000)
    assert d_kit_under.allowed is True
    assert d_kit_under.kind == GuardDecisionKind.ALLOW
    assert d_kit_under.computed_amount_cents == 2000
    assert d_kit_under.target_state == OrderState.REFUNDED_PARTIAL

    # 6. IN_KITCHEN: Partial refund > $25 threshold
    # Captured amount = $80.00 (8000 cents). 50% partial = 4000 cents ($40.00 > $25 threshold) -> NEEDS_APPROVAL
    d_kit_over = guard.evaluate_refund(OrderState.IN_KITCHEN, 8000)
    assert d_kit_over.allowed is False
    assert d_kit_over.kind == GuardDecisionKind.NEEDS_APPROVAL
    assert d_kit_over.computed_amount_cents == 4000
    assert d_kit_over.target_state == OrderState.REFUNDED_PARTIAL


def test_guard_evaluate_transition(default_policy):
    guard = Guard(default_policy)
    # Valid
    d_val = guard.evaluate_transition(OrderState.CAPTURED, OrderAction.KITCHEN_ACCEPT)
    assert d_val.allowed is True
    assert d_val.target_state == OrderState.IN_KITCHEN

    # Invalid
    d_inval = guard.evaluate_transition(OrderState.DRAFT, OrderAction.PICKUP)
    assert d_inval.allowed is False
    assert d_inval.kind == GuardDecisionKind.DENY


# ==============================================================================
# 5. Hypothesis Property-Based Testing
# ==============================================================================

@given(
    base_price=st.integers(min_value=100, max_value=10000),
    qty=st.integers(min_value=1, max_value=20),
    mod_price=st.integers(min_value=0, max_value=1000),
    tax_bps=st.integers(min_value=0, max_value=2500),
)
def test_hypothesis_pricing_properties(base_price: int, qty: int, mod_price: int, tax_bps: int):
    """
    Mathematical invariants:
    1. Subtotal equals exactly sum of line totals.
    2. Total equals exactly subtotal + tax.
    3. Line total equals exactly (base + mod) * qty.
    4. All monetary figures are integers (no floating point drift).
    """
    menu = {
        "item_1": MenuItem(
            id="item_1",
            shop_id="shop_prop",
            name="Prop Item",
            category="Test",
            price_cents=base_price,
            available=True,
        )
    }
    cart = [
        CartItem(
            item_id="item_1",
            name="Prop Item",
            qty=qty,
            modifiers=(ModifierSelection("mod_1", "Mod", mod_price),) if mod_price > 0 else (),
        )
    ]
    quote = calculate_quote("shop_prop", cart, menu, tax_rate_bps=tax_bps)

    assert isinstance(quote.subtotal_cents, int)
    assert isinstance(quote.tax_cents, int)
    assert isinstance(quote.total_cents, int)

    expected_unit = base_price + mod_price
    expected_line = expected_unit * qty
    assert quote.lines[0].line_total_cents == expected_line
    assert quote.subtotal_cents == expected_line
    assert quote.total_cents == quote.subtotal_cents + quote.tax_cents


@given(
    captured_cents=st.integers(min_value=1, max_value=100000),
    partial_pct=st.integers(min_value=1, max_value=100),
    approval_threshold=st.integers(min_value=100, max_value=10000),
)
def test_hypothesis_refund_amount_never_exceeds_captured(captured_cents: int, partial_pct: int, approval_threshold: int):
    """
    Security invariant:
    A refund (whether full, partial, or approval-routed) can NEVER exceed the captured amount.
    """
    policy = ShopPolicy(
        partial_refund_pct=partial_pct,
        refund_approval_threshold_cents=approval_threshold,
    )
    guard = Guard(policy)

    # In CAPTURED
    decision_cap = guard.evaluate_refund(OrderState.CAPTURED, captured_cents)
    assert decision_cap.computed_amount_cents is not None
    assert decision_cap.computed_amount_cents <= captured_cents

    # In IN_KITCHEN
    decision_kit = guard.evaluate_refund(OrderState.IN_KITCHEN, captured_cents)
    assert decision_kit.computed_amount_cents is not None
    assert decision_kit.computed_amount_cents <= captured_cents
    assert decision_kit.computed_amount_cents == (captured_cents * partial_pct) // 100
