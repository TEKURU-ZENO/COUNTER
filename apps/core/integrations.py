"""
Outbound integrations fed from the outbox.

Zapier: selected order events are POSTed to a Zapier "Catch Hook" URL so a restaurant can route
them anywhere Zapier reaches (SMS the owner on NEEDS_REVIEW, append paid orders to a sheet...).
Each request carries an HMAC-SHA256 signature of the body in X-Counter-Signature so the Zap can
filter out forgeries. Payloads carry order ids, states and amounts only: no caller identifiers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from apps.core.container import Container

logger = logging.getLogger("counter.integrations")

ZAPIER_EVENT_TYPES = frozenset(
    {
        "ORDER_CAPTURE",
        "ORDER_CHARGE_SAVED_PAYMENT",
        "ORDER_FLAG_REVIEW",
        "ORDER_REVERSE",
        "ORDER_DENY_PAYMENT",
        "REFUND_APPROVAL_REQUESTED",
        "RECONCILIATION_ALERT",
        "ORDER_RECORD_FULL_REFUND",
        "ORDER_RECORD_PARTIAL_REFUND",
    }
)
_SAFE_PAYLOAD_KEYS = ("state", "from", "to", "total_cents", "amount_cents", "refunded_total_cents", "reason")


def zapier_sink(container: Container) -> Callable[[dict[str, Any]], Awaitable[None]]:
    settings = container.settings
    url = settings.zapier_webhook_url
    secret = settings.zapier_signing_secret.get_secret_value() if settings.zapier_signing_secret else ""

    async def _send(event: dict[str, Any]) -> None:
        if not url or event["type"] not in ZAPIER_EVENT_TYPES:
            return
        payload = event.get("payload", {})
        body = json.dumps(
            {
                "event_id": event["event_id"],
                "type": event["type"],
                "order_id": event["order_id"],
                "created_at": event["created_at"],
                **{k: payload[k] for k in _SAFE_PAYLOAD_KEYS if k in payload},
            },
            separators=(",", ":"),
        ).encode()
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["X-Counter-Signature"] = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        resp = await container.http.post(url, content=body, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"Zapier hook returned HTTP {resp.status_code}")

    return _send


def outbox_sinks(container: Container) -> list[Callable[[dict[str, Any]], Awaitable[None]]]:
    sinks = []
    if container.settings.zapier_webhook_url:
        sinks.append(zapier_sink(container))
    return sinks
