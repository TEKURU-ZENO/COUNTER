"""
Money conversion between integer cents and PayPal's decimal strings.

Integer arithmetic only. PayPal rejects an order with AMOUNT_MISMATCH unless
sum(items) == item_total and item_total + tax_total == amount.value exactly, so the
purchase-unit builder checks those identities before anything leaves the process.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# Currencies PayPal treats as zero-decimal. We only support two-decimal currencies; failing
# loudly is better than sending 1450 JPY as "14.50".
ZERO_DECIMAL_CURRENCIES = frozenset({"JPY", "HUF", "TWD"})
_VALUE_RE = re.compile(r"^(0|[1-9][0-9]*)(?:\.([0-9]{1,2}))?$")


class MoneyError(ValueError):
    pass


class AmountMismatchError(MoneyError):
    """The breakdown does not add up; PayPal would reject it with AMOUNT_MISMATCH."""


def check_currency(currency: str) -> str:
    if len(currency) != 3 or not currency.isalpha() or not currency.isupper():
        raise MoneyError(f"Invalid ISO-4217 currency code: {currency!r}")
    if currency in ZERO_DECIMAL_CURRENCIES:
        raise MoneyError(f"Zero-decimal currency {currency} is not supported")
    return currency


def cents_to_value(cents: int) -> str:
    """1450 -> '14.50'. Negative amounts are never sent to PayPal."""
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise MoneyError(f"Amount must be integer cents, got {type(cents).__name__}")
    if cents < 0:
        raise MoneyError("Amount must not be negative")
    return f"{cents // 100}.{cents % 100:02d}"


def value_to_cents(value: str) -> int:
    """'14.5' -> 1450, '14.50' -> 1450. Rejects anything that is not a plain decimal."""
    if not isinstance(value, str):
        raise MoneyError(f"PayPal amount must be a string, got {type(value).__name__}")
    match = _VALUE_RE.match(value.strip())
    if not match:
        raise MoneyError(f"Unparseable PayPal amount: {value!r}")
    whole, frac = match.group(1), match.group(2) or ""
    return int(whole) * 100 + int(frac.ljust(2, "0"))


def money(cents: int, currency: str) -> dict[str, str]:
    return {"currency_code": check_currency(currency), "value": cents_to_value(cents)}


@dataclass(frozen=True)
class PayPalLine:
    name: str
    qty: int
    unit_cents: int


def build_purchase_unit(
    *,
    reference_id: str,
    currency: str,
    lines: Sequence[PayPalLine],
    subtotal_cents: int,
    tax_cents: int,
    total_cents: int,
) -> dict[str, Any]:
    """Build an Orders v2 purchase unit, refusing any breakdown that does not add up."""
    check_currency(currency)
    if subtotal_cents + tax_cents != total_cents:
        raise AmountMismatchError(
            f"item_total {subtotal_cents} + tax_total {tax_cents} != amount {total_cents}"
        )
    items_sum = sum(line.unit_cents * line.qty for line in lines)
    if lines and items_sum != subtotal_cents:
        raise AmountMismatchError(f"sum(items) {items_sum} != item_total {subtotal_cents}")

    unit: dict[str, Any] = {
        "reference_id": reference_id,
        "custom_id": reference_id,
        "amount": {
            **money(total_cents, currency),
            "breakdown": {
                "item_total": money(subtotal_cents, currency),
                "tax_total": money(tax_cents, currency),
            },
        },
    }
    if lines:
        unit["items"] = [
            {
                "name": line.name[:127],
                "quantity": str(line.qty),
                "unit_amount": money(line.unit_cents, currency),
                "category": "PHYSICAL_GOODS",
            }
            for line in lines
        ]
    return unit
