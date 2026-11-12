"""PayPal gateway: a real HTTP client and an explicit in-memory mock behind one interface."""

from packages.paypal_gateway.client import PayPalClient, cert_url_is_paypal
from packages.paypal_gateway.mock import MockPayPalGateway, sign_mock_webhook
from packages.paypal_gateway.types import (
    CaptureFacts,
    PayPalError,
    PayPalGateway,
    PayPalRetryableError,
    WebhookHeaders,
    approval_link,
    extract_capture,
)

__all__ = [
    "CaptureFacts",
    "MockPayPalGateway",
    "PayPalClient",
    "PayPalError",
    "PayPalGateway",
    "PayPalRetryableError",
    "WebhookHeaders",
    "approval_link",
    "cert_url_is_paypal",
    "extract_capture",
    "sign_mock_webhook",
]
