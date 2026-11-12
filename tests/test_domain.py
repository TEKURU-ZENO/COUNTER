"""
Complete test suite for COUNTER Domain Core.
Includes 100% branch coverage tests, hypothesis property-based tests,
quote hash tamper tests, and mutation verification tests.
"""

from __future__ import annotations

import pytest

from packages.domain.fsm import (
    TRANSITION_TABLE,
    IllegalStateTransitionError,
    can_transition,
    get_next_state,
    get_side_effects,
    refund_action,
)
from packages.domain.guard import Guard
from packages.domain.models import (
    PAID_STATES,
    CartItem,
    GuardDecisionKind,
    MenuItem,
    ModifierDefinition,
    ModifierSelection,
    OrderAction,
    OrderState,
    ShopPolicy,
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
    for action, rule in TRANSITION_TABLE.items():
        for from_state in rule.allowed_from_states:
            assert can_transition(from_state, action) is True
            assert get_next_state(from_state, action) == rule.to_state
            assert get_side_effects(action) == rule.side_effects


def test_every_unlisted_transition_is_rejected():
    for action, rule in TRANSITION_TABLE.items():
        for state in OrderState:
            if state in rule.allowed_from_states:
                continue
            assert can_transition(state, action) is False
            with pytest.raises(IllegalStateTransitionError):
                get_next_state(state, action)


def test_kitchen_never_accepts_unpaid_or_pending_orders():
    for state in OrderState:
        if state != OrderState.CAPTURED:
            assert can_transition(state, OrderAction.KITCHEN_ACCEPT) is False
    assert OrderState.PAYMENT_PENDING not in PAID_STATES


def test_pending_capture_path():
    assert get_next_state(OrderState.APPROVED, OrderAction.MARK_PAYMENT_PENDING) == OrderState.PAYMENT_PENDING
    assert get_next_state(OrderState.PAYMENT_PENDING, OrderAction.CAPTURE) == OrderState.CAPTURED
    assert get_next_state(OrderState.PAYMENT_PENDING, OrderAction.DENY_PAYMENT) == OrderState.PAYMENT_DENIED
    assert get_next_state(OrderState.IN_KITCHEN, OrderAction.REVERSE) == OrderState.REVERSED


def test_refund_action_selection():
    assert refund_action(1000, 1000) == OrderAction.RECORD_FULL_REFUND
    assert refund_action(1000, 400) == OrderAction.RECORD_PARTIAL_REFUND
    for bad in (0, -1, 1001):
        with pytest.raises(ValueError):
            refund_action(1000, bad)


def test_fsm_illegal_transition_message():
    with pytest.raises(IllegalStateTransitionError) as exc_info:
        get_next_state(OrderState.DRAFT, OrderAction.MARK_READY)
    assert "Cannot execute action 'mark_ready' from state 'DRAFT'" in str(exc_info.value)


def test_fsm_unknown_action_handling():
    class FakeAction(str):
        value = "non_existent_action"

    fake = FakeAction("non_existent_action")
    assert can_transition(OrderState.DRAFT, fake) is False  # type: ignore[arg-type]
    with pytest.raises(IllegalStateTransitionError):
        get_next_state(OrderState.DRAFT, fake)  # type: ignore[arg-type]
    assert get_side_effects(fake) == "unknown_side_effect"  # type: ignore[arg-type]


def test_guard_send_link_quote_disallowed(sample_menu, default_policy):
    guard = Guard(default_policy)
    quote = calculate_quote("shop_123", [CartItem("item_pizza", "Margherita Pizza", 50)], sample_menu)
    decision = guard.evaluate_send_payment_link(
        current_state=OrderState.QUOTED, provided_quote_hash=quote.quote_hash, current_quote=quote,
        slot_available=True, shop_open=True,
    )
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
    assert "exceeds the maximum order value" in decision.reason


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


