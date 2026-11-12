"""
HTTP client for PayPal REST APIs (Orders v2, Payments v2, Invoicing v2, Reporting, Webhooks).

- One pooled httpx client per process, OAuth token cached until 60 s before expiry.
- Every money-moving POST carries a caller-supplied PayPal-Request-Id; there are no defaults,
  so a retry can never accidentally create a second charge or refund.
- Retries only on 429/5xx/transport errors, with jittered exponential backoff.
- Webhook verification posts the *raw* event body to /v1/notifications/verify-webhook-signature,
  because re-serialising the JSON can change bytes and make PayPal report FAILURE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from packages.domain.money import PayPalLine, build_purchase_unit, money
from packages.paypal_gateway.types import PayPalError, PayPalRetryableError, WebhookHeaders

logger = logging.getLogger("counter.paypal")

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
LIVE_BASE = "https://api-m.paypal.com"


def cert_url_is_paypal(cert_url: str) -> bool:
    """The signing cert must come from PayPal over https; anything else is a forgery attempt."""
    parsed = urlparse(cert_url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "paypal.com" or host.endswith(".paypal.com"))


class PayPalClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        webhook_id: str,
        mode: str,
        partner_attribution_id: str | None = None,
        http: httpx.AsyncClient | None = None,
    ):
        if mode not in ("sandbox", "live"):
            raise ValueError("PayPalClient only talks to sandbox or live; use MockPayPalGateway for mock")
        self.mode = mode
        self.client_id = client_id
        self._client_secret = client_secret
        self.webhook_id = webhook_id
        self.partner_attribution_id = partner_attribution_id
        self.base_url = LIVE_BASE if mode == "live" else SANDBOX_BASE
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ transport

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at - 60:
            return self._token
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at - 60:
                return self._token
            try:
                resp = await self._http.post(
                    f"{self.base_url}/v1/oauth2/token",
                    auth=(self.client_id, self._client_secret),
                    data={"grant_type": "client_credentials"},
                )
            except httpx.RequestError as exc:
                raise PayPalRetryableError(f"Token request transport error: {exc}") from exc
            if resp.status_code != 200:
                raise PayPalError("OAuth token request failed", resp.status_code, _safe_json(resp))
            payload = resp.json()
            self._token = str(payload["access_token"])
            self._token_expires_at = time.monotonic() + float(payload.get("expires_in", 3600))
            return self._token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        request_id: str | None = None,
        prefer_representation: bool = True,
    ) -> dict[str, Any]:
        @retry(
            retry=retry_if_exception_type(PayPalRetryableError),
            stop=stop_after_attempt(4),
            wait=wait_exponential_jitter(initial=0.5, max=4.0),
            reraise=True,
        )
        async def _once() -> dict[str, Any]:
            token = await self._access_token()
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            if prefer_representation:
                headers["Prefer"] = "return=representation"
            if request_id:
                headers["PayPal-Request-Id"] = request_id
            if self.partner_attribution_id:
                headers["PayPal-Partner-Attribution-Id"] = self.partner_attribution_id
            try:
                resp = await self._http.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=json_body if raw_body is None else None,
                    content=raw_body,
                )
            except httpx.RequestError as exc:
                raise PayPalRetryableError(f"Transport error calling PayPal {path}: {exc}") from exc

            if resp.status_code == 401:
                self._token = None  # token revoked or expired early; retry once with a new one
                raise PayPalRetryableError("PayPal returned 401", 401, _safe_json(resp))
            if resp.status_code in (429, 500, 502, 503, 504):
                raise PayPalRetryableError(
                    f"PayPal transient error {resp.status_code}", resp.status_code, _safe_json(resp)
                )
            if resp.status_code >= 400:
                body = _safe_json(resp)
                raise PayPalError(
                    f"PayPal API error {resp.status_code} on {path}: {body.get('name', '')}",
                    resp.status_code,
                    body,
                )
            if resp.status_code == 204 or not resp.content:
                return {}
            return dict(resp.json())

        return await _once()

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
        paypal_source: dict[str, Any] = {
            "experience_context": {
                "return_url": return_url,
                "cancel_url": cancel_url,
                "user_action": "PAY_NOW",
                "shipping_preference": "NO_SHIPPING",
            }
        }
        if vault_on_success:
            paypal_source["attributes"] = {
                "vault": {
                    "store_in_vault": "ON_SUCCESS",
                    "usage_type": "MERCHANT",
                    "customer_type": "CONSUMER",
                }
            }
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                build_purchase_unit(
                    reference_id=order_id,
                    currency=currency,
                    lines=lines,
                    subtotal_cents=subtotal_cents,
                    tax_cents=tax_cents,
                    total_cents=total_cents,
                )
            ],
            "payment_source": {"paypal": paypal_source},
        }
        return await self._request("POST", "/v2/checkout/orders", json_body=payload, request_id=request_id)

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
        """Merchant-initiated charge of a saved PayPal wallet. PayPal captures in the same call."""
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                build_purchase_unit(
                    reference_id=order_id,
                    currency=currency,
                    lines=lines,
                    subtotal_cents=subtotal_cents,
                    tax_cents=tax_cents,
                    total_cents=total_cents,
                )
            ],
            "payment_source": {"paypal": {"vault_id": vault_id}},
        }
        return await self._request("POST", "/v2/checkout/orders", json_body=payload, request_id=request_id)

    async def get_order(self, paypal_order_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v2/checkout/orders/{paypal_order_id}")

    async def capture_order(self, paypal_order_id: str, *, request_id: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/v2/checkout/orders/{paypal_order_id}/capture", json_body={}, request_id=request_id
        )

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
        payload = {"amount": money(amount_cents, currency), "note_to_payer": note_to_payer[:255]}
        return await self._request(
            "POST", f"/v2/payments/captures/{capture_id}/refund", json_body=payload, request_id=request_id
        )

    # ------------------------------------------------------------------ Invoicing v2 (catering)

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
    ) -> dict[str, Any]:
        payload = {
            "detail": {
                "invoice_number": order_id,
                "currency_code": currency,
                "note": f"Catering for {headcount} guests on {event_date}",
                "payment_term": {"term_type": "DUE_ON_RECEIPT"},
            },
            "primary_recipients": [{"billing_info": {"email_address": recipient_email}}],
            "items": [
                {
                    "name": f"Catering package ({headcount} guests)",
                    "quantity": "1",
                    "unit_amount": money(total_cents, currency),
                }
            ],
            "configuration": {
                "allow_tip": False,
                "partial_payment": {
                    "allow_partial_payment": True,
                    "minimum_amount_due": money(deposit_cents, currency),
                },
            },
        }
        return await self._request("POST", "/v2/invoicing/invoices", json_body=payload, request_id=request_id)

    async def send_invoice(self, invoice_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v2/invoicing/invoices/{invoice_id}/send",
            json_body={"send_to_recipient": True},
            prefer_representation=False,
        )

    # ------------------------------------------------------------------ Reporting

    async def search_transactions(self, start_date: str, end_date: str) -> dict[str, Any]:
        path = (
            f"/v1/reporting/transactions?start_date={start_date}&end_date={end_date}"
            "&fields=transaction_info&page_size=500"
        )
        return await self._request("GET", path, prefer_representation=False)

    # ------------------------------------------------------------------ Webhooks

    async def verify_webhook(self, headers: WebhookHeaders, raw_body: bytes) -> bool:
        if not cert_url_is_paypal(headers.cert_url):
            logger.warning("Rejecting webhook with non-PayPal cert_url host")
            return False
        try:
            json.loads(raw_body)
        except ValueError:
            return False
        envelope = (
            "{"
            f'"transmission_id":{json.dumps(headers.transmission_id)},'
            f'"transmission_time":{json.dumps(headers.transmission_time)},'
            f'"cert_url":{json.dumps(headers.cert_url)},'
            f'"auth_algo":{json.dumps(headers.auth_algo)},'
            f'"transmission_sig":{json.dumps(headers.transmission_sig)},'
            f'"webhook_id":{json.dumps(self.webhook_id)},'
            f'"webhook_event":{raw_body.decode("utf-8")}'
            "}"
        ).encode()
        try:
            res = await self._request(
                "POST",
                "/v1/notifications/verify-webhook-signature",
                raw_body=envelope,
                prefer_representation=False,
            )
        except PayPalError as exc:
            logger.error("Webhook verification call failed: %s", exc)
            return False
        return res.get("verification_status") == "SUCCESS"


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
        return body if isinstance(body, dict) else {"body": body}
    except ValueError:
        return {"body": resp.text[:500]}
