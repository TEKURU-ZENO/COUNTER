"""Shared types for the PayPal gateway: the interface both the HTTP client and the mock implement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, Sequence

from packages.domain.money import PayPalLine


class PayPalError(Exception):
    """A PayPal API call failed."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}

    @property
    def issue(self) -> Optional[str]:
        """First issue code from PayPal's error body, e.g. ORDER_ALREADY_CAPTURED."""
        for detail in self.details.get("details", []) or []:
            if isinstance(detail, dict) and detail.get("issue"):
                return str(detail["issue"])
        return None


class PayPalRetryableError(PayPalError):
    """Transient 5xx / 429 / transport failure."""


@dataclass(frozen=True)
class WebhookHeaders:
    transmission_id: str
    transmission_time: str
    cert_url: str
    auth_algo: str
    transmission_sig: str

    REQUIRED = (
        "paypal-transmission-id",
        "paypal-transmission-time",
        "paypal-cert-url",
        "paypal-auth-algo",
        "paypal-transmission-sig",
    )

    @classmethod
    def from_mapping(cls, headers: Any) -> Optional["WebhookHeaders"]:
        """Return None if any transmission header is missing. Never substitutes defaults."""
        values = []
        for name in cls.REQUIRED:
            value = headers.get(name)
            if not value:
                return None
            values.append(value)
        return cls(*values)


@dataclass(frozen=True)
class CaptureFacts:
    """What PayPal says about one capture, reduced to the fields we reconcile on."""

    capture_id: str
    status: str
    amount_value: str
    currency: str
    custom_id: Optional[str]
    status_reason: Optional[str] = None
    vault_id: Optional[str] = None
    fee_value: Optional[str] = None


def extract_capture(order_response: dict[str, Any]) -> Optional[CaptureFacts]:
    """Pull the first capture out of an Orders v2 capture/create response."""
    try:
        unit = order_response["purchase_units"][0]
        capture = unit["payments"]["captures"][0]
    except (KeyError, IndexError, TypeError):
        return None
    amount = capture.get("amount") or {}
    vault_id = (
        (order_response.get("payment_source") or {})
        .get("paypal", {})
        .get("attributes", {})
        .get("vault", {})
        .get("id")
    )
    fee = ((capture.get("seller_receivable_breakdown") or {}).get("paypal_fee") or {}).get("value")
    return CaptureFacts(
        capture_id=str(capture.get("id", "")),
        status=str(capture.get("status", "")),
        amount_value=str(amount.get("value", "")),
        currency=str(amount.get("currency_code", "")),
        custom_id=capture.get("custom_id") or unit.get("custom_id") or unit.get("reference_id"),
        status_reason=(capture.get("status_details") or {}).get("reason"),
        vault_id=vault_id,
        fee_value=fee,
    )


def approval_link(order_response: dict[str, Any]) -> Optional[str]:
    """With payment_source set, PayPal returns rel=payer-action; older flows return rel=approve."""
    for link in order_response.get("links", []) or []:
        if link.get("rel") in ("payer-action", "approve"):
            return str(link.get("href"))
    return None


class PayPalGateway(Protocol):
    mode: str

    async def create_order(
        self,
        *,
        order_id: str,
        currency: str,
        lines: Sequence[PayPalLine],
        subtotal_cents: int,
        tax_cents: int,
        total_cents: int,
        return_url: str,
        cancel_url: str,
        vault_on_success: bool,
        request_id: str,
    ) -> dict[str, Any]: ...

    async def charge_vault(
        self,
        *,
        vault_id: str,
        order_id: str,
        currency: str,
        lines: Sequence[PayPalLine],
        subtotal_cents: int,
        tax_cents: int,
        total_cents: int,
        request_id: str,
    ) -> dict[str, Any]: ...

    async def get_order(self, paypal_order_id: str) -> dict[str, Any]: ...

    async def capture_order(self, paypal_order_id: str, *, request_id: str) -> dict[str, Any]: ...

    async def refund_capture(
        self,
        capture_id: str,
        *,
        amount_cents: int,
        currency: str,
        request_id: str,
        note_to_payer: str,
    ) -> dict[str, Any]: ...

    async def create_invoice(
        self,
        *,
        order_id: str,
        recipient_email: str,
        headcount: int,
        deposit_cents: int,
        total_cents: int,
        event_date: str,
        currency: str,
        request_id: str,
    ) -> dict[str, Any]: ...

    async def send_invoice(self, invoice_id: str) -> dict[str, Any]: ...

    async def search_transactions(self, start_date: str, end_date: str) -> dict[str, Any]: ...

    async def verify_webhook(self, headers: WebhookHeaders, raw_body: bytes) -> bool: ...

    async def aclose(self) -> None: ...
