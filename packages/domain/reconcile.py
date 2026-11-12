"""
Reconciliation of what PayPal says it captured against what we quoted.

A capture only counts as paid when status is COMPLETED *and* the amount, currency and
custom_id all match the order. Anything else is pending, denied, or a mismatch for review.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from packages.domain.money import MoneyError, value_to_cents


class CaptureOutcome(str, Enum):
    COMPLETED = "completed"
    PENDING = "pending"
    DENIED = "denied"
    MISMATCH = "mismatch"


@dataclass(frozen=True)
class ReconcileResult:
    outcome: CaptureOutcome
    captured_cents: Optional[int]
    reasons: tuple[str, ...] = ()


_DENIED = frozenset({"DECLINED", "DENIED", "FAILED", "VOIDED"})


def reconcile_capture(
    *,
    expected_order_id: str,
    expected_total_cents: int,
    expected_currency: str,
    status: str,
    amount_value: str,
    currency: str,
    custom_id: Optional[str],
) -> ReconcileResult:
    reasons: list[str] = []
    try:
        captured = value_to_cents(amount_value)
    except MoneyError:
        return ReconcileResult(CaptureOutcome.MISMATCH, None, (f"unparseable amount {amount_value!r}",))

    if captured != expected_total_cents:
        reasons.append(f"amount {captured} != quoted {expected_total_cents}")
    if currency != expected_currency:
        reasons.append(f"currency {currency} != {expected_currency}")
    if custom_id != expected_order_id:
        reasons.append(f"custom_id {custom_id!r} != order {expected_order_id!r}")
    if reasons:
        return ReconcileResult(CaptureOutcome.MISMATCH, captured, tuple(reasons))

    status = status.upper()
    if status == "COMPLETED":
        return ReconcileResult(CaptureOutcome.COMPLETED, captured)
    if status == "PENDING":
        return ReconcileResult(CaptureOutcome.PENDING, captured)
    if status in _DENIED:
        return ReconcileResult(CaptureOutcome.DENIED, captured, (f"status {status}",))
    return ReconcileResult(CaptureOutcome.MISMATCH, captured, (f"unexpected status {status}",))
