"""
Outbound SMS: Twilio in deployed setups, an in-memory demo inbox otherwise.

The demo inbox exists so the web demo (which has no real handset) can show the one-time code.
Settings refuse to enable it in production or with live PayPal.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Protocol

import httpx

logger = logging.getLogger("counter.sms")


class SmsSender(Protocol):
    async def send(self, to_phone: str, body: str, *, inbox_key: str) -> None: ...


class TwilioSms:
    def __init__(self, account_sid: str, auth_token: str, from_number: str, http: httpx.AsyncClient):
        self._sid = account_sid
        self._token = auth_token
        self._from = from_number
        self._http = http

    async def send(self, to_phone: str, body: str, *, inbox_key: str) -> None:
        resp = await self._http.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json",
            auth=(self._sid, self._token),
            data={"To": to_phone, "From": self._from, "Body": body},
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"Twilio SMS failed with HTTP {resp.status_code}")


class DemoInboxSms:
    """Keeps the last few messages per caller in memory. Never sends anything."""

    def __init__(self, max_per_key: int = 10):
        self._messages: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=max_per_key))

    async def send(self, to_phone: str, body: str, *, inbox_key: str) -> None:
        self._messages[inbox_key].append(body)
        logger.info("Demo SMS stored for inbox %s…", inbox_key[:8])

    def inbox(self, inbox_key: str) -> list[str]:
        return list(self._messages.get(inbox_key, []))


class NullSms:
    async def send(self, to_phone: str, body: str, *, inbox_key: str) -> None:
        raise RuntimeError("SMS is not configured; set Twilio credentials or DEMO_SMS_INBOX=true")
