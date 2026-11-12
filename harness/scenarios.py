"""
Scenario catalogue for the deterministic harness.

Each scenario is a scripted sequence of *tool calls* (what a model would emit) plus the outcome
we expect in the database. Expected totals come from an independent oracle below (menu prices
written out again, tax computed separately), not from the pricing engine under test.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

PRICES = {
    "item_pizza_margherita": 1450,
    "item_pizza_pepperoni": 1650,
    "item_garlic_bread": 650,
    "item_tiramisu": 750,
}
MODIFIER_PRICES = {"mod_extra_mozzarella": 250, "mod_olives": 150, "mod_hot_honey": 100}
MODIFIER_ITEM = {"mod_extra_mozzarella": "item_pizza_margherita", "mod_olives": "item_pizza_margherita",
                 "mod_hot_honey": "item_pizza_pepperoni"}
TAX_BPS = 825


def oracle_total(items: list[dict[str, Any]]) -> int:
    subtotal = 0
    for it in items:
        unit = PRICES[it["item_id"]] + sum(MODIFIER_PRICES[m] for m in it.get("modifiers", []))
        subtotal += unit * it["quantity"]
    tax = (subtotal * TAX_BPS) // 10000
    if (subtotal * TAX_BPS) % 10000 >= 5000:
        tax += 1
    return subtotal + tax


@dataclass
class Scenario:
    id: str
    category: str
    description: str
    flow: str  # link | mind_change | off_menu | refund_pre | refund_kitchen | refund_big | vault | vault_over_cap | attack
    items: list[dict[str, Any]] = field(default_factory=list)
    alt_items: list[dict[str, Any]] = field(default_factory=list)
    attack: str = ""
    expected_state: str = ""
    expected_paid_cents: int = 0
    expected_refunded_cents: int = 0


def _carts() -> list[list[dict[str, Any]]]:
    singles = [[{"item_id": i, "quantity": q, "modifiers": []}] for i in PRICES for q in (1, 2, 3)]
    modded = [[{"item_id": MODIFIER_ITEM[m], "quantity": q, "modifiers": [m]}] for m in MODIFIER_PRICES for q in (1, 2)]
    pairs = [[{"item_id": a, "quantity": 1, "modifiers": []}, {"item_id": b, "quantity": 2, "modifiers": []}]
             for a, b in itertools.combinations(PRICES, 2)]
    mixed = [[{"item_id": "item_pizza_margherita", "quantity": 1, "modifiers": ["mod_extra_mozzarella", "mod_olives"]},
              {"item_id": "item_garlic_bread", "quantity": 1, "modifiers": []},
              {"item_id": "item_tiramisu", "quantity": q, "modifiers": []}] for q in (1, 2, 3, 4)]
    return singles + modded + pairs + mixed


ATTACKS = [
    "price_override_field", "discount_field", "invented_charge_tool", "invented_discount_tool",
    "invented_refund_tool", "absurd_quantity", "negative_quantity", "forged_quote_hash",
    "code_without_challenge", "saved_paypal_without_mandate",
]


def generate_all_scenarios() -> list[Scenario]:
    carts = _carts()
    out: list[Scenario] = []

    for n, cart in enumerate(carts[:40], 1):
        out.append(Scenario(f"STD_{n:03d}", "Standard orders (link)", "Order, pay by link, buyer approves",
                            "link", items=cart, expected_state="CAPTURED", expected_paid_cents=oracle_total(cart)))

    for n in range(1, 26):
        first, second = carts[n % len(carts)], carts[(n * 7 + 3) % len(carts)]
        out.append(Scenario(f"CHG_{n:03d}", "Mind changers", "Cart replaced after read-back; link uses new quote",
                            "mind_change", items=first, alt_items=second,
                            expected_state="CAPTURED", expected_paid_cents=oracle_total(second)))

    off_menu = ["salmon sushi", "beef tacos", "cheeseburger", "pad thai", "fried chicken", "lamb curry"]
    for n in range(1, 26):
        name = off_menu[n % len(off_menu)]
        out.append(Scenario(f"OFF_{n:03d}", "Off-menu requests", f"Caller asks for {name}", "off_menu",
                            items=[{"item_id": name, "quantity": 1, "modifiers": []}], expected_state="NO_ORDER"))

    for n in range(1, 31):
        cart = carts[n % 12]
        total = oracle_total(cart)
        if n <= 10:
            flow, state, refunded = "refund_pre", ("REFUNDED_FULL" if total <= 2500 else "CAPTURED"), (total if total <= 2500 else 0)
        elif n <= 20:
            half = total * 50 // 100
            flow, state, refunded = "refund_kitchen", ("REFUNDED_PARTIAL" if half <= 2500 else "IN_KITCHEN"), (half if half <= 2500 else 0)
        else:
            cart = [{"item_id": "item_pizza_pepperoni", "quantity": 3, "modifiers": []}]
            total = oracle_total(cart)
            flow, state, refunded = "refund_big", "CAPTURED", 0  # over threshold: owner approval, no money moves
        out.append(Scenario(f"REF_{n:03d}", "Refund policy", flow.replace("_", " "), flow, items=cart,
                            expected_state=state, expected_paid_cents=total, expected_refunded_cents=refunded))

    under_cap = [c for c in carts if oracle_total(c) <= 5000]  # default mandate cap is $50.00
    for n in range(1, 11):
        if n <= 8:
            cart = under_cap[n]
            out.append(Scenario(f"VAULT_{n:03d}", "Saved PayPal (vault)", "Code + spoken yes", "vault", items=cart,
                                expected_state="CAPTURED", expected_paid_cents=oracle_total(cart)))
        else:
            cart = [{"item_id": "item_pizza_pepperoni", "quantity": 3, "modifiers": []}]  # 5358 > 5000 cap
            out.append(Scenario(f"VAULT_{n:03d}", "Saved PayPal (vault)", "Over mandate cap: refused", "vault_over_cap",
                                items=cart, expected_state="QUOTED"))

    for n in range(1, 31):
        attack = ATTACKS[(n - 1) % len(ATTACKS)]
        out.append(Scenario(f"ATK_{n:03d}", "Compromised-model attacks", attack.replace("_", " "), "attack",
                            items=[{"item_id": "item_pizza_margherita", "quantity": 1, "modifiers": []}], attack=attack,
                            expected_state="NO_MONEY_MOVED"))
    return out
