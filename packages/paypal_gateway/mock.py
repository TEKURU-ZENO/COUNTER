"""
In-memory PayPal gateway for tests and offline development.

Only constructed when PAYPAL_MODE=mock is set explicitly. It models the behaviour the rest of
the system depends on, so that tests exercise the same branches the sandbox would:
- orders need buyer approval before capture (approve() stands in for the buyer);
- captures echo the order amount and custom_id, and can be forced to PENDING/DECLINED or to
  a tampered amount to test reconciliation;
- PayPal-Request-Id replays return the original response;
- refunds above the remaining captured amount fail like PayPal does (422);
- webhooks are signed with an HMAC of the body, and unsigned or mis-signed events fail.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import itertools
import json
from typing import Any, Optional, Sequence

from packages.domain.money import PayPalLine, build_purchase_unit, cents_to_value, value_to_cents
from packages.paypal_gateway.types import PayPalError, WebhookHeaders

_counter = itertools.count(1)


def _next_id(prefix: str) -> str:
    return f"{prefix}{next(_counter):010d}"


def mock_signature(secret: str, headers_without_sig: dict[str, str], raw_body: bytes) -> str:
    message = "|".join(
        [
            headers_without_sig["paypal-transmission-id"],
            headers_without_sig["paypal-transmission-time"],
            hashlib.sha256(raw_body).hexdigest(),
        ]
    ).encode()
    return base64.b64encode(hmac.new(secret.encode(), message, hashlib.sha256).digest()).decode()


def sign_mock_webhook(secret: str, raw_body: bytes, transmission_id: Optional[str] = None) -> dict[str, str]:
    """Headers a test or the dev simulator can send to the webhook endpoint in mock mode."""
    headers = {
        "paypal-transmission-id": transmission_id or _next_id("TX-"),
        "paypal-transmission-time": "2026-01-01T00:00:00Z",
        "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-MOCK",
        "paypal-auth-algo": "SHA256withRSA",
    }
    headers["paypal-transmission-sig"] = mock_signature(secret, headers, raw_body)
    return headers


class MockPayPalGateway:
    mode = "mock"

    def __init__(self, *, webhook_secret: str, approval_base_url: str = "http://localhost:8000"):
        self._webhook_secret = webhook_secret
        self._approval_base_url = approval_base_url.rstrip("/")
        self.orders: dict[str, dict[str, Any]] = {}
        self.captures: dict[str, dict[str, Any]] = {}
        self.refunds: dict[str, dict[str, Any]] = {}
        self.vault_tokens: set[str] = set()
        self.invoices: dict[str, dict[str, Any]] = {}
        self._replay: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, Optional[str]]] = []
        # Fault injection for tests
        self.next_capture_status: Optional[str] = None
        self.next_capture_amount_value: Optional[str] = None
        self.fail_next: Optional[PayPalError] = None

    async def aclose(self) -> None:
        return None

    def _check_fault(self) -> None:
        if self.fail_next is not None:
            err, self.fail_next = self.fail_next, None
            raise err

    def _replayed(self, request_id: str) -> Optional[dict[str, Any]]:
        return self._replay.get(request_id)

    # ------------------------------------------------------------------ buyer simulation

    def approve(self, paypal_order_id: str) -> None:
        order = self.orders[paypal_order_id]
        if order["status"] not in ("PAYER_ACTION_REQUIRED", "CREATED"):
            raise PayPalError("Order not awaiting approval", 422)
        order["status"] = "APPROVED"

    def _make_capture(self, order: dict[str, Any]) -> dict[str, Any]:
        unit = order["purchase_units"][0]
        status = self.next_capture_status or "COMPLETED"
        value = self.next_capture_amount_value or unit["amount"]["value"]
        self.next_capture_status = None
        self.next_capture_amount_value = None
        capture = {
            "id": _next_id("CAP-"),
            "status": status,
            "amount": {"currency_code": unit["amount"]["currency_code"], "value": value},
            "custom_id": unit["custom_id"],
            "seller_receivable_breakdown": {
                "paypal_fee": {
                    "currency_code": unit["amount"]["currency_code"],
                    "value": cents_to_value(value_to_cents(value) * 349 // 10000 + 49),
                }
            },
        }
        if status == "PENDING":
            capture["status_details"] = {"reason": "PENDING_REVIEW"}
        self.captures[capture["id"]] = {**capture, "refunded_cents": 0}
        return capture

    # ------------------------------------------------------------------ Orders v2

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
    ) -> dict[str, Any]:
        self.calls.append(("create_order", request_id))
        self._check_fault()
        if (hit := self._replayed(request_id)) is not None:
            return hit
        unit = build_purchase_unit(
            reference_id=order_id,
            currency=currency,
            lines=lines,
            subtotal_cents=subtotal_cents,
            tax_cents=tax_cents,
            total_cents=total_cents,
        )
        pp_id = _next_id("MOCK-ORDER-")
        order = {
            "id": pp_id,
            "status": "PAYER_ACTION_REQUIRED",
            "purchase_units": [unit],
            "vault_on_success": vault_on_success,
            "return_url": return_url,
            "cancel_url": cancel_url,
            "links": [
                {
                    "rel": "payer-action",
                    "href": f"{self._approval_base_url}/api/v1/mock-paypal/approve/{pp_id}",
                    "method": "GET",
                }
            ],
        }
        self.orders[pp_id] = order
        response = {"id": pp_id, "status": order["status"], "links": order["links"]}
        self._replay[request_id] = response
        return response

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
    ) -> dict[str, Any]:
        self.calls.append(("charge_vault", request_id))
        self._check_fault()
        if (hit := self._replayed(request_id)) is not None:
            return hit
        if vault_id not in self.vault_tokens:
            raise PayPalError("Vault token not found", 422, {"details": [{"issue": "INVALID_RESOURCE_ID"}]})
        unit = build_purchase_unit(
            reference_id=order_id,
            currency=currency,
            lines=lines,
            subtotal_cents=subtotal_cents,
            tax_cents=tax_cents,
            total_cents=total_cents,
        )
        pp_id = _next_id("MOCK-ORDER-")
        order = {"id": pp_id, "status": "APPROVED", "purchase_units": [unit]}
        self.orders[pp_id] = order
        capture = self._make_capture(order)
        order["status"] = "COMPLETED" if capture["status"] == "COMPLETED" else "APPROVED"
        response = {
            "id": pp_id,
            "status": order["status"],
            "purchase_units": [{**unit, "payments": {"captures": [capture]}}],
        }
        self._replay[request_id] = response
        return response

    async def get_order(self, paypal_order_id: str) -> dict[str, Any]:
        order = self.orders.get(paypal_order_id)
        if order is None:
            raise PayPalError("Order not found", 404, {"name": "RESOURCE_NOT_FOUND"})
        return {"id": order["id"], "status": order["status"], "purchase_units": order["purchase_units"]}

    async def capture_order(self, paypal_order_id: str, *, request_id: str) -> dict[str, Any]:
        self.calls.append(("capture_order", request_id))
        self._check_fault()
        if (hit := self._replayed(request_id)) is not None:
            return hit
        order = self.orders.get(paypal_order_id)
        if order is None:
            raise PayPalError("Order not found", 404, {"name": "RESOURCE_NOT_FOUND"})
        if order["status"] == "COMPLETED":
            raise PayPalError(
                "Order already captured", 422, {"details": [{"issue": "ORDER_ALREADY_CAPTURED"}]}
            )
        if order["status"] != "APPROVED":
            raise PayPalError(
                "Payer has not approved", 422, {"details": [{"issue": "ORDER_NOT_APPROVED"}]}
            )
        capture = self._make_capture(order)
        order["status"] = "COMPLETED"
        order["capture"] = capture
        response: dict[str, Any] = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [{**order["purchase_units"][0], "payments": {"captures": [capture]}}],
        }
        if order.get("vault_on_success") and capture["status"] == "COMPLETED":
            token = _next_id("MOCK-VAULT-")
            self.vault_tokens.add(token)
            response["payment_source"] = {
                "paypal": {"attributes": {"vault": {"id": token, "status": "VAULTED"}}}
            }
        self._replay[request_id] = response
        return response

    # ------------------------------------------------------------------ Payments v2

    async def refund_capture(
        self,
        capture_id: str,
        *,
        amount_cents: int,
        currency: str,
        request_id: str,
        note_to_payer: str,
    ) -> dict[str, Any]:
        self.calls.append(("refund_capture", request_id))
        self._check_fault()
        if (hit := self._replayed(request_id)) is not None:
            return hit
        capture = self.captures.get(capture_id)
        if capture is None:
            raise PayPalError("Capture not found", 404, {"name": "RESOURCE_NOT_FOUND"})
        captured = value_to_cents(capture["amount"]["value"])
        if amount_cents <= 0 or capture["refunded_cents"] + amount_cents > captured:
            raise PayPalError(
                "Refund exceeds captured amount", 422, {"details": [{"issue": "REFUND_AMOUNT_EXCEEDED"}]}
            )
        capture["refunded_cents"] += amount_cents
        refund = {
            "id": _next_id("REF-"),
            "status": "COMPLETED",
            "amount": {"currency_code": currency, "value": cents_to_value(amount_cents)},
            "links": [{"rel": "up", "href": f"/v2/payments/captures/{capture_id}"}],
        }
        self.refunds[refund["id"]] = refund
        self._replay[request_id] = refund
        return refund

    # ------------------------------------------------------------------ Invoicing / reporting

    async def create_invoice(self, **kwargs: Any) -> dict[str, Any]:
        inv_id = _next_id("INV-")
        self.invoices[inv_id] = {"id": inv_id, "status": "DRAFT", **kwargs}
        return {"id": inv_id, "status": "DRAFT"}

    async def send_invoice(self, invoice_id: str) -> dict[str, Any]:
        self.invoices[invoice_id]["status"] = "SENT"
        return {}

    async def search_transactions(self, start_date: str, end_date: str) -> dict[str, Any]:
        details = [
            {
                "transaction_info": {
                    "transaction_id": cap_id,
                    "transaction_status": "S",
                    "transaction_amount": cap["amount"],
                    "custom_field": cap.get("custom_id"),
                }
            }
            for cap_id, cap in self.captures.items()
            if cap["status"] == "COMPLETED"
        ]
        return {"transaction_details": details, "total_items": len(details)}

    # ------------------------------------------------------------------ Webhooks

    async def verify_webhook(self, headers: WebhookHeaders, raw_body: bytes) -> bool:
        try:
            json.loads(raw_body)
        except ValueError:
            return False
        expected = mock_signature(
            self._webhook_secret,
            {
                "paypal-transmission-id": headers.transmission_id,
                "paypal-transmission-time": headers.transmission_time,
            },
            raw_body,
        )
        return hmac.compare_digest(expected, headers.transmission_sig)
