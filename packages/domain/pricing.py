"""
Pure pricing engine for COUNTER.
Zero I/O, integer cents everywhere. No floating point numbers anywhere near money.
Provides deterministic quote calculation, a quote fingerprint (SHA-256 of the canonical cart),
and the spoken read-back.
"""

from __future__ import annotations

import hashlib
import json
from typing import Mapping, Sequence

from packages.domain.models import (
    CartItem,
    MenuItem,
    ModifierSelection,
    Quote,
    QuoteLine,
)


class PricingError(ValueError):
    """Base error for pricing and quote calculations."""
    pass


class MenuItemNotFoundError(PricingError):
    def __init__(self, item_id: str):
        super().__init__(f"Menu item '{item_id}' not found in catalog")
        self.item_id = item_id


class MenuItemUnavailableError(PricingError):
    def __init__(self, item_name: str):
        super().__init__(f"Menu item '{item_name}' is currently unavailable")
        self.item_name = item_name


def format_cents(cents: int) -> str:
    """Format integer cents into a standard currency string (e.g. 1450 -> '$14.50')."""
    sign = "-" if cents < 0 else ""
    magnitude = abs(cents)
    return f"{sign}${magnitude // 100}.{magnitude % 100:02d}"


def compute_quote_hash(
    shop_id: str,
    lines: Sequence[QuoteLine],
    subtotal_cents: int,
    tax_cents: int,
    total_cents: int,
    secret_salt: str = "counter_quote_salt",
) -> str:
    """
    Fingerprint of the quote: SHA-256 over a canonical JSON encoding of the cart and totals.

    It detects that the cart changed between the spoken read-back and the payment step. It is
    an integrity check inside our own process, not a signature: it proves nothing to a third party.
    """
    canonical_lines = []
    # Sort lines deterministically by item_id and modifiers
    for line in sorted(lines, key=lambda l: (l.item_id, l.name)):
        sorted_mods = sorted(
            [(m.modifier_id, m.name, m.price_delta_cents) for m in line.modifiers]
        )
        canonical_lines.append({
            "item_id": line.item_id,
            "name": line.name,
            "qty": line.qty,
            "unit_total_cents": line.unit_total_cents,
            "line_total_cents": line.line_total_cents,
            "modifiers": sorted_mods,
        })

    payload = {
        "shop_id": shop_id,
        "lines": canonical_lines,
        "subtotal_cents": subtotal_cents,
        "tax_cents": tax_cents,
        "total_cents": total_cents,
        "salt": secret_salt,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generate_spoken_readback(lines: Sequence[QuoteLine], subtotal_cents: int, tax_cents: int, total_cents: int) -> str:
    """
    Generates a natural, professional verbal order read-back template.
    The agent uses this exact structure when speaking to the customer.
    """
    if not lines:
        return "Your cart is currently empty."

    items_spoken = []
    for line in lines:
        mod_text = ""
        if line.modifiers:
            mod_names = ", ".join(m.name for m in line.modifiers)
            mod_text = f" with {mod_names}"
        qty_str = f"{line.qty} {line.name}" if line.qty == 1 else f"{line.qty} {line.name}s"
        items_spoken.append(f"{qty_str}{mod_text}")

    if len(items_spoken) == 1:
        items_str = items_spoken[0]
    elif len(items_spoken) == 2:
        items_str = f"{items_spoken[0]} and {items_spoken[1]}"
    else:
        items_str = ", ".join(items_spoken[:-1]) + f", and {items_spoken[-1]}"

    return (
        f"You have {items_str}. "
        f"The subtotal is {format_cents(subtotal_cents)}, plus {format_cents(tax_cents)} tax, "
        f"bringing your total to {format_cents(total_cents)}."
    )


def calculate_quote(
    shop_id: str,
    cart: Sequence[CartItem],
    menu: Mapping[str, MenuItem],
    tax_rate_bps: int = 825,  # 8.25% standard default sales tax
    secret_salt: str = "counter_quote_salt",
) -> Quote:
    """
    Pure deterministic pricing function.
    Calculates line totals, subtotal, tax (half-up integer rounding in bps), and total in integer cents.
    Generates the quote fingerprint and spoken read-back.
    """
    if not cart:
        empty_hash = compute_quote_hash(shop_id, (), 0, 0, 0, secret_salt)
        return Quote(
            shop_id=shop_id,
            lines=(),
            subtotal_cents=0,
            tax_rate_bps=tax_rate_bps,
            tax_cents=0,
            total_cents=0,
            quote_hash=empty_hash,
            spoken_summary="Your cart is currently empty.",
        )

    computed_lines: list[QuoteLine] = []
    running_subtotal_cents = 0

    for item in cart:
        if item.qty <= 0:
            continue

        menu_item = menu.get(item.item_id)
        if not menu_item:
            raise MenuItemNotFoundError(item.item_id)

        if not menu_item.available:
            raise MenuItemUnavailableError(menu_item.name)

        modifiers_delta = sum(m.price_delta_cents for m in item.modifiers)
        unit_total = menu_item.price_cents + modifiers_delta
        line_total = unit_total * item.qty

        running_subtotal_cents += line_total

        computed_lines.append(
            QuoteLine(
                item_id=menu_item.id,
                name=menu_item.name,
                qty=item.qty,
                unit_base_cents=menu_item.price_cents,
                modifiers_delta_cents=modifiers_delta,
                unit_total_cents=unit_total,
                line_total_cents=line_total,
                modifiers=tuple(item.modifiers),
            )
        )

    # Integer tax calculation with half-up rounding (add 5000 bps before floor divide by 10000 bps)
    # Example: 1000 cents * 825 bps = 825000. (825000 + 5000) // 10000 = 83 cents.
    tax_cents = (running_subtotal_cents * tax_rate_bps + 5000) // 10000
    total_cents = running_subtotal_cents + tax_cents

    lines_tuple = tuple(computed_lines)
    quote_hash = compute_quote_hash(
        shop_id=shop_id,
        lines=lines_tuple,
        subtotal_cents=running_subtotal_cents,
        tax_cents=tax_cents,
        total_cents=total_cents,
        secret_salt=secret_salt,
    )
    spoken = generate_spoken_readback(
        lines=lines_tuple,
        subtotal_cents=running_subtotal_cents,
        tax_cents=tax_cents,
        total_cents=total_cents,
    )

    return Quote(
        shop_id=shop_id,
        lines=lines_tuple,
        subtotal_cents=running_subtotal_cents,
        tax_rate_bps=tax_rate_bps,
        tax_cents=tax_cents,
        total_cents=total_cents,
        quote_hash=quote_hash,
        spoken_summary=spoken,
    )
