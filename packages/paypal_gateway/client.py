"""
Typed, resilient PayPal REST API Client for COUNTER.
Covers Orders v2, Payments v2, Invoicing v2, Catalog Products v2, Reporting,
and Webhook verification with strict idempotency and token caching.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional
import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger("counter.paypal_gateway")


class PayPalError(Exception):
    """Base exception for PayPal REST API failures."""

    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


class PayPalRetryableError(PayPalError):
    """Raised for transient 5xx server errors or 429 Rate Limits."""
    pass


class PayPalGatewayClient:
    """
    Asynchronous client for PayPal REST APIs.
    - Uses OAuth2 client_credentials token caching.
    - Enforces idempotency via 'PayPal-Request-Id'.
    - Implements exponential backoff on retryable status codes.
    - Fallback simulator mode for offline local test runs.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        webhook_id: str = "counter_mock_webhook_id",
        mode: str = "sandbox",
        partner_attribution_id: str = "Counter_Takeaway_AI",
        mock_mode: bool = False,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.webhook_id = webhook_id
        self.mode = mode.lower()
        self.partner_attribution_id = partner_attribution_id
        self.mock_mode = mock_mode or client_id.startswith("your_") or "mock" in client_id

        self.base_url = (
            "https://api-m.paypal.com" if self.mode == "live" else "https://api-m.sandbox.paypal.com"
        )
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

        # In-memory mock store for offline testing
        self._mock_orders: Dict[str, Dict[str, Any]] = {}
        self._mock_captures: Dict[str, Dict[str, Any]] = {}
        self._mock_refunds: Dict[str, Dict[str, Any]] = {}
        self._mock_invoices: Dict[str, Dict[str, Any]] = {}
        self._mock_products: Dict[str, Dict[str, Any]] = {}

    async def _get_access_token(self, http_client: httpx.AsyncClient) -> str:
        """Fetch or return cached OAuth2 bearer token."""
        if self.mock_mode:
            return "mock_access_token_12345"

        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        async with self._token_lock:
            if self._access_token and now < self._token_expires_at - 60:
                return self._access_token

            auth_str = f"{self.client_id}:{self.client_secret}"
            b64_auth = base64.b64encode(auth_str.encode()).decode()

            headers = {
                "Authorization": f"Basic {b64_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            data = {"grant_type": "client_credentials"}

            try:
                resp = await http_client.post(
                    f"{self.base_url}/v1/oauth2/token",
                    headers=headers,
                    data=data,
                    timeout=10.0,
                )
            except Exception as e:
                raise PayPalRetryableError(f"Network error contacting PayPal token endpoint: {e}")

            if resp.status_code != 200:
                raise PayPalError(f"OAuth token request failed: {resp.text}", resp.status_code)

            payload = resp.json()
            self._access_token = payload["access_token"]
            expires_in = payload.get("expires_in", 32400)
            self._token_expires_at = now + expires_in
            return self._access_token

    def _build_headers(
        self, token: str, idempotency_key: Optional[str] = None
    ) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Partner-Attribution-Id": self.partner_attribution_id,
        }
        if idempotency_key:
            headers["PayPal-Request-Id"] = idempotency_key
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute request with retries on 5xx and 429."""
        if self.mock_mode:
            return await self._mock_dispatch(method, path, data, idempotency_key)

        async with httpx.AsyncClient() as client:
            token = await self._get_access_token(client)
            headers = self._build_headers(token, idempotency_key)
            url = f"{self.base_url}{path}"

            @retry(
                retry=retry_if_exception_type(PayPalRetryableError),
                stop=stop_after_attempt(4),
                wait=wait_exponential_jitter(initial=0.5, max=4.0),
                reraise=True,
            )
            async def _execute():
                try:
                    resp = await client.request(
                        method,
                        url,
                        headers=headers,
                        json=data if data else None,
                        timeout=15.0,
                    )
                except httpx.RequestError as exc:
                    raise PayPalRetryableError(f"Transport error connecting to PayPal: {exc}")

                if resp.status_code in (429, 500, 502, 503, 504):
                    raise PayPalRetryableError(
                        f"PayPal transient error {resp.status_code}: {resp.text}",
                        resp.status_code,
                    )

                if resp.status_code not in (200, 201, 202, 204):
                    raise PayPalError(
                        f"PayPal API error {resp.status_code}: {resp.text}",
                        resp.status_code,
                        resp.json() if resp.headers.get("content-type") == "application/json" else {},
                    )

                if resp.status_code == 204:
                    return {}
                return resp.json()

            return await _execute()

    # --------------------------------------------------------------------------
    # Orders v2
    # --------------------------------------------------------------------------

    async def create_order(
        self,
        order_id: str,
        total_cents: int,
        subtotal_cents: int,
        tax_cents: int,
        currency: str = "USD",
        items: Optional[List[Dict[str, Any]]] = None,
        return_url: str = "http://localhost:3000/order-status",
        cancel_url: str = "http://localhost:3000/cancel",
        vault_buyer: bool = True,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a PayPal Order v2 with itemized breakdown, Venmo enabled,
        and buyer wallet vaulting on success (payment_source.paypal.attributes.vault).
        """
        if not idempotency_key:
            idempotency_key = f"order:{order_id}:create"

        def _fmt(cents: int) -> str:
            return f"{cents / 100:.2f}"

        item_list = []
        if items:
            for it in items:
                item_list.append({
                    "name": it.get("name", "Takeaway Item")[:127],
                    "quantity": str(it.get("qty", 1)),
                    "unit_amount": {
                        "currency_code": currency,
                        "value": _fmt(it.get("unit_total_cents", 0)),
                    },
                })

        payload: Dict[str, Any] = {
            "intent": "CAPTURE",
            "custom_id": order_id,
            "purchase_units": [
                {
                    "reference_id": order_id,
                    "custom_id": order_id,
                    "amount": {
                        "currency_code": currency,
                        "value": _fmt(total_cents),
                        "breakdown": {
                            "item_total": {"currency_code": currency, "value": _fmt(subtotal_cents)},
                            "tax_total": {"currency_code": currency, "value": _fmt(tax_cents)},
                        },
                    },
                    "items": item_list if item_list else None,
                }
            ],
            "application_context": {
                "return_url": f"{return_url}?counter_order_id={order_id}",
                "cancel_url": f"{cancel_url}?counter_order_id={order_id}",
                "user_action": "PAY_NOW",
                "shipping_preference": "NO_SHIPPING",
            },
        }

        # Enable wallet vaulting and Venmo approval for US buyers
        if vault_buyer:
            payload["payment_source"] = {
                "paypal": {
                    "attributes": {
                        "vault": {
                            "store_in_vault": "ON_SUCCESS",
                            "usage_type": "MERCHANT",
                            "customer_type": "CONSUMER",
                        }
                    }
                },
                "venmo": {
                    "experience_context": {
                        "return_url": f"{return_url}?counter_order_id={order_id}",
                        "cancel_url": f"{cancel_url}?counter_order_id={order_id}",
                    }
                },
            }

        # Remove None values
        if not payload["purchase_units"][0]["items"]:
            del payload["purchase_units"][0]["items"]

        return await self._request("POST", "/v2/checkout/orders", payload, idempotency_key)

    async def charge_vault_id(
        self,
        vault_id: str,
        order_id: str,
        total_cents: int,
        subtotal_cents: int,
        tax_cents: int,
        currency: str = "USD",
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Charge a repeat caller's saved PayPal wallet directly using their Vault ID.
        Executes without mid-call web redirects, enabling instant voice-confirmed payment.
        """
        if not idempotency_key:
            idempotency_key = f"order:{order_id}:vault_charge"

        def _fmt(cents: int) -> str:
            return f"{cents / 100:.2f}"

        payload = {
            "intent": "CAPTURE",
            "custom_id": order_id,
            "payment_source": {
                "paypal": {
                    "vault_id": vault_id
                }
            },
            "purchase_units": [
                {
                    "reference_id": order_id,
                    "custom_id": order_id,
                    "amount": {
                        "currency_code": currency,
                        "value": _fmt(total_cents),
                        "breakdown": {
                            "item_total": {"currency_code": currency, "value": _fmt(subtotal_cents)},
                            "tax_total": {"currency_code": currency, "value": _fmt(tax_cents)},
                        },
                    },
                }
            ],
        }
        return await self._request("POST", "/v2/checkout/orders", payload, idempotency_key)

        return await self._request("POST", "/v2/checkout/orders", payload, idempotency_key)

    async def get_order(self, paypal_order_id: str) -> Dict[str, Any]:
        """Fetch current order details from PayPal Orders v2."""
        return await self._request("GET", f"/v2/checkout/orders/{paypal_order_id}")

    async def capture_order(
        self,
        paypal_order_id: str,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Capture payment for an approved PayPal order."""
        if not idempotency_key:
            idempotency_key = f"order:{paypal_order_id}:capture"
        return await self._request(
            "POST",
            f"/v2/checkout/orders/{paypal_order_id}/capture",
            idempotency_key=idempotency_key,
        )

    # --------------------------------------------------------------------------
    # Payments v2 (Captures & Refunds)
    # --------------------------------------------------------------------------

    async def get_capture(self, capture_id: str) -> Dict[str, Any]:
        """Fetch captured payment details."""
        return await self._request("GET", f"/v2/payments/captures/{capture_id}")

    async def refund_capture(
        self,
        capture_id: str,
        amount_cents: Optional[int] = None,
        currency: str = "USD",
        note_to_payer: str = "Counter Takeaway Refund",
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Issue full or partial refund on a capture with idempotency key."""
        payload: Dict[str, Any] = {"note_to_payer": note_to_payer}
        if amount_cents is not None:
            payload["amount"] = {
                "currency_code": currency,
                "value": f"{amount_cents / 100:.2f}",
            }

        return await self._request(
            "POST",
            f"/v2/payments/captures/{capture_id}/refund",
            payload,
            idempotency_key=idempotency_key,
        )

    # --------------------------------------------------------------------------
    # Invoicing v2 (Catering flow)
    # --------------------------------------------------------------------------

    async def create_invoice(
        self,
        order_id: str,
        recipient_email: str,
        headcount: int,
        deposit_cents: int,
        total_cents: int,
        event_date: str,
        currency: str = "USD",
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create draft invoice with deposit / partial payment enabled for catering."""
        payload = {
            "detail": {
                "currency_code": currency,
                "note": f"Catering booking for {headcount} guests on {event_date}",
                "payment_term": {"term_type": "DUE_ON_RECEIPT"},
            },
            "primary_recipients": [{"billing_info": {"email_address": recipient_email}}],
            "items": [
                {
                    "name": f"Catering Package ({headcount} guests)",
                    "quantity": "1",
                    "unit_amount": {"currency_code": currency, "value": f"{total_cents / 100:.2f}"},
                }
            ],
            "configuration": {
                "partial_payment": {
                    "allow_partial_payment": True,
                    "minimum_amount_due": {
                        "currency_code": currency,
                        "value": f"{deposit_cents / 100:.2f}",
                    },
                }
            },
        }
        return await self._request("POST", "/v2/invoicing/invoices", payload, idempotency_key)

    async def send_invoice(self, invoice_id: str) -> Dict[str, Any]:
        """Send invoice to customer email."""
        return await self._request("POST", f"/v2/invoicing/invoices/{invoice_id}/send")

    # --------------------------------------------------------------------------
    # Catalog Products
    # --------------------------------------------------------------------------

    async def create_product(
        self,
        name: str,
        description: str,
        category: str = "FOOD_BEVERAGE",
        product_type: str = "PHYSICAL",
    ) -> Dict[str, Any]:
        """Sync a takeaway menu item into PayPal Catalog."""
        payload = {
            "name": name[:127],
            "description": description[:256],
            "type": product_type,
            "category": category,
        }
        return await self._request("POST", "/v1/catalogs/products", payload)

    async def list_products(self) -> Dict[str, Any]:
        """List products in PayPal catalog."""
        return await self._request("GET", "/v1/catalogs/products?page_size=20")

    # --------------------------------------------------------------------------
    # Reporting / Reconciliation
    # --------------------------------------------------------------------------

    async def search_transactions(
        self,
        start_date: str,
        end_date: str,
        transaction_status: str = "S",
    ) -> Dict[str, Any]:
        """Search PayPal transactions for end-of-day reconciliation."""
        path = f"/v1/reporting/transactions?start_date={start_date}&end_date={end_date}&transaction_status={transaction_status}"
        return await self._request("GET", path)

    # --------------------------------------------------------------------------
    # Webhook Signature Verification
    # --------------------------------------------------------------------------

    async def verify_webhook_signature(
        self,
        transmission_id: str,
        transmission_time: str,
        cert_url: str,
        auth_algo: str,
        transmission_sig: str,
        webhook_id: str,
        webhook_body: Dict[str, Any],
    ) -> bool:
        """
        Verify incoming webhook signature with PayPal API.
        Guarantees payloads cannot be forged or tampered with.
        """
        if self.mock_mode:
            # In mock mode, check that transmission_sig is not 'invalid_signature'
            return transmission_sig != "invalid_signature"

        payload = {
            "transmission_id": transmission_id,
            "transmission_time": transmission_time,
            "cert_url": cert_url,
            "auth_algo": auth_algo,
            "transmission_sig": transmission_sig,
            "webhook_id": webhook_id,
            "webhook_event": webhook_body,
        }
        try:
            res = await self._request("POST", "/v1/notifications/verify-webhook-signature", payload)
            return res.get("verification_status") == "SUCCESS"
        except Exception as e:
            logger.error(f"Error verifying webhook signature: {e}")
            return False

    # --------------------------------------------------------------------------
    # Deterministic Mock Dispatcher (Local & Chaos Test Suite)
    # --------------------------------------------------------------------------

    async def _mock_dispatch(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]],
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        """Local in-memory mock handler for zero-dependency tests."""
        await asyncio.sleep(0.01)  # tiny simulation delay

        # Create Order
        if method == "POST" and path == "/v2/checkout/orders":
            custom_id = data.get("custom_id", "mock_order_123") if data else "mock_order_123"
            paypal_order_id = f"PP_ORDER_{custom_id}_{int(time.time() * 1000)}"

            # Direct Vault Charge flow
            vault_id = data.get("payment_source", {}).get("paypal", {}).get("vault_id") if data else None
            if vault_id:
                capture_id = f"CAP_VAULT_{paypal_order_id[-8:]}"
                capture_data = {
                    "id": capture_id,
                    "status": "COMPLETED",
                    "amount": data["purchase_units"][0]["amount"] if data else {},
                }
                order_data = {
                    "id": paypal_order_id,
                    "status": "COMPLETED",
                    "custom_id": custom_id,
                    "vault_id": vault_id,
                    "purchase_units": [{"payments": {"captures": [capture_data]}}],
                }
                self._mock_orders[paypal_order_id] = order_data
                self._mock_captures[capture_id] = capture_data
                return order_data

            order_data = {
                "id": paypal_order_id,
                "status": "CREATED",
                "custom_id": custom_id,
                "amount": data["purchase_units"][0]["amount"] if data else {},
                "vault_token": f"VAULT_MANDATE_{custom_id[-6:]}",
                "links": [
                    {
                        "href": f"https://www.sandbox.paypal.com/checkoutnow?token={paypal_order_id}",
                        "rel": "approve",
                        "method": "GET",
                    }
                ],
            }
            self._mock_orders[paypal_order_id] = order_data
            return order_data

        # Get Order
        if method == "GET" and path.startswith("/v2/checkout/orders/"):
            paypal_order_id = path.split("/")[-1]
            if paypal_order_id in self._mock_orders:
                return self._mock_orders[paypal_order_id]
            return {"id": paypal_order_id, "status": "APPROVED"}

        # Capture Order
        if method == "POST" and path.endswith("/capture"):
            parts = path.split("/")
            paypal_order_id = parts[3]
            capture_id = f"CAP_{paypal_order_id[-8:]}"
            capture_data = {
                "id": capture_id,
                "status": "COMPLETED",
                "amount": {"currency_code": "USD", "value": "25.00"},
                "seller_receivable_breakdown": {
                    "gross_amount": {"value": "25.00"},
                    "paypal_fee": {"value": "1.05"},
                    "net_amount": {"value": "23.95"},
                },
            }
            self._mock_captures[capture_id] = capture_data
            if paypal_order_id in self._mock_orders:
                self._mock_orders[paypal_order_id]["status"] = "COMPLETED"

            return {
                "id": paypal_order_id,
                "status": "COMPLETED",
                "purchase_units": [
                    {
                        "payments": {
                            "captures": [capture_data]
                        }
                    }
                ],
            }

        # Refund Capture
        if method == "POST" and "/refund" in path:
            capture_id = path.split("/")[-2]
            refund_id = f"REF_{capture_id[-8:]}_{int(time.time())}"
            refund_data = {
                "id": refund_id,
                "status": "COMPLETED",
                "amount": data.get("amount") if data else {},
            }
            self._mock_refunds[refund_id] = refund_data
            return refund_data

        # Invoices
        if method == "POST" and path == "/v2/invoicing/invoices":
            inv_id = f"INV_{int(time.time() * 1000)}"
            self._mock_invoices[inv_id] = {"id": inv_id, "status": "DRAFT", "data": data}
            return {"id": inv_id, "status": "DRAFT"}

        if method == "POST" and "/send" in path:
            inv_id = path.split("/")[-2]
            return {"id": inv_id, "status": "SENT"}

        # Products
        if method == "POST" and path == "/v1/catalogs/products":
            prod_id = f"PROD_{int(time.time() * 1000)}"
            return {"id": prod_id, "name": data.get("name", "Product") if data else "Product"}

        # Default fallback
        return {"status": "SUCCESS"}
