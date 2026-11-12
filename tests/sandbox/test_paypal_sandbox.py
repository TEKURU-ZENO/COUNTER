"""
Real PayPal sandbox integration suite. Opt-in, run on demand:

    PAYPAL_MODE=sandbox PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... PAYPAL_WEBHOOK_ID=... \
    PAYPAL_SANDBOX_BUYER_EMAIL=... PAYPAL_SANDBOX_BUYER_PASSWORD=... \
    pytest -m sandbox tests/sandbox -s

create -> buyer approves on PayPal's real sandbox checkout (Playwright) -> capture -> partial
refund -> vault token from the capture -> merchant-initiated charge of the vault token.

The browser is a hosted Kernel browser when KERNEL_API_KEY is set (pip install kernel), otherwise
local Chromium (playwright install chromium). Vaulting requires "Save payment methods" to be
enabled on the sandbox REST app. Each run appends a row to harness/reports/sandbox.md.
"""

from __future__ import annotations

import os
import platform
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from packages.domain.money import PayPalLine, value_to_cents
from packages.domain.reconcile import CaptureOutcome, reconcile_capture
from packages.paypal_gateway import PayPalClient, WebhookHeaders, approval_link, extract_capture

pytestmark = pytest.mark.sandbox
REQUIRED = ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID",
            "PAYPAL_SANDBOX_BUYER_EMAIL", "PAYPAL_SANDBOX_BUYER_PASSWORD"]
missing = [k for k in REQUIRED if not os.getenv(k)]
if os.getenv("PAYPAL_MODE") != "sandbox" or missing:
    pytest.skip(f"sandbox suite needs PAYPAL_MODE=sandbox and {', '.join(missing) or 'credentials'}",
                allow_module_level=True)

REPORT = Path(__file__).resolve().parents[2] / "harness" / "reports" / "sandbox.md"
RETURN_URL = "https://example.com/counter-sandbox-return"
CANCEL_URL = "https://example.com/counter-sandbox-cancel"
LINES = [PayPalLine("Margherita Wood-Fired Pizza", 1, 1450)]
SUBTOTAL, TAX, TOTAL = 1450, 120, 1570


@pytest_asyncio.fixture
async def paypal() -> AsyncIterator[PayPalClient]:
    client = PayPalClient(client_id=os.environ["PAYPAL_CLIENT_ID"], client_secret=os.environ["PAYPAL_CLIENT_SECRET"],
                          webhook_id=os.environ["PAYPAL_WEBHOOK_ID"], mode="sandbox")
    yield client
    await client.aclose()


async def approve_in_browser(url: str) -> str:
    from playwright.async_api import async_playwright

    kernel_browser = None
    async with async_playwright() as pw:
        if os.getenv("KERNEL_API_KEY"):
            from kernel import Kernel

            kernel = Kernel()
            kernel_browser = kernel.browsers.create()
            browser = await pw.chromium.connect_over_cdp(kernel_browser.cdp_ws_url)
            where = "kernel"
        else:
            browser = await pw.chromium.launch()
            where = "local-chromium"
        try:
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            await page.goto(url)
            await page.fill("#email", os.environ["PAYPAL_SANDBOX_BUYER_EMAIL"])
            if await page.locator("#btnNext").is_visible():
                await page.click("#btnNext")
            await page.fill("#password", os.environ["PAYPAL_SANDBOX_BUYER_PASSWORD"])
            await page.click("#btnLogin")
            submit = page.locator("#payment-submit-btn, button:has-text('Complete Purchase'), "
                                  "button:has-text('Agree & Continue'), button:has-text('Continue')").first
            await submit.click(timeout=60_000)
            await page.wait_for_url(f"{RETURN_URL}*", timeout=60_000)
        finally:
            await browser.close()
            if kernel_browser is not None:
                kernel.browsers.delete_by_id(kernel_browser.session_id)
    return where


def record(step: str, ok: bool, detail: str, started: float) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    if not REPORT.exists():
        REPORT.write_text(
            "# Runs against the real PayPal sandbox\n\n"
            "Rows are appended by `pytest -m sandbox tests/sandbox`. Unlike the deterministic harness, these calls "
            "go to api-m.sandbox.paypal.com.\n\n"
            "| When (UTC) | Host | Step | Result | Detail | Seconds |\n| --- | --- | --- | --- | --- | ---: |\n",
            encoding="utf-8")
    with REPORT.open("a", encoding="utf-8") as f:
        f.write(f"| {datetime.now(UTC):%Y-%m-%d %H:%M} | {platform.system()} | {step} | "
                f"{'pass' if ok else 'FAIL'} | {detail} | {time.perf_counter() - started:.1f} |\n")


async def test_create_approve_capture_refund_vault_charge(paypal: PayPalClient) -> None:
    order_id = f"sbx_{uuid.uuid4().hex[:10]}"
    t0 = time.perf_counter()
    created = await paypal.create_order(order_id=order_id, currency="USD", lines=LINES, subtotal_cents=SUBTOTAL,
                                        tax_cents=TAX, total_cents=TOTAL, return_url=RETURN_URL, cancel_url=CANCEL_URL,
                                        vault_on_success=True, request_id=f"order:{order_id}:create")
    link = approval_link(created)
    assert created["status"] == "PAYER_ACTION_REQUIRED" and link
    record("create order", True, created["id"], t0)

    t0 = time.perf_counter()
    where = await approve_in_browser(link)
    record("buyer approval", True, f"browser={where}", t0)

    t0 = time.perf_counter()
    captured = await paypal.capture_order(created["id"], request_id=f"order:{order_id}:capture")
    again = await paypal.capture_order(created["id"], request_id=f"order:{order_id}:capture")  # idempotent replay
    facts = extract_capture(captured)
    assert facts is not None
    assert extract_capture(again).capture_id == facts.capture_id  # type: ignore[union-attr]
    result = reconcile_capture(expected_order_id=order_id, expected_total_cents=TOTAL, expected_currency="USD",
                               status=facts.status, amount_value=facts.amount_value, currency=facts.currency,
                               custom_id=facts.custom_id)
    assert result.outcome == CaptureOutcome.COMPLETED, result
    record("capture + replay", True, f"{facts.capture_id} {facts.status}", t0)

    t0 = time.perf_counter()
    refund = await paypal.refund_capture(facts.capture_id, amount_cents=100, currency="USD",
                                         request_id=f"order:{order_id}:refund:1", note_to_payer="sandbox test")
    assert refund["status"] in ("COMPLETED", "PENDING") and value_to_cents(refund["amount"]["value"]) == 100
    record("partial refund", True, f"{refund['id']} {refund['status']}", t0)

    assert facts.vault_id, "no vault id returned: enable vaulting on the sandbox app"
    t0 = time.perf_counter()
    repeat_id = f"sbx_{uuid.uuid4().hex[:10]}"
    charged = await paypal.charge_vault(vault_id=facts.vault_id, order_id=repeat_id, currency="USD", lines=LINES,
                                        subtotal_cents=SUBTOTAL, tax_cents=TAX, total_cents=TOTAL,
                                        request_id=f"order:{repeat_id}:vault")
    vfacts = extract_capture(charged)
    assert vfacts is not None and vfacts.status == "COMPLETED" and value_to_cents(vfacts.amount_value) == TOTAL
    record("vault charge", True, f"{vfacts.capture_id} {vfacts.status}", t0)


async def test_forged_webhook_fails_real_verification(paypal: PayPalClient) -> None:
    t0 = time.perf_counter()
    headers = WebhookHeaders("forged-id", "2026-01-01T00:00:00Z",
                             "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-360caa42-fca2a594-1d93a270",
                             "SHA256withRSA", "Zm9yZ2Vk")
    body: dict[str, Any] = {"id": "WH-FORGED", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {}}
    import json

    assert await paypal.verify_webhook(headers, json.dumps(body).encode()) is False
    record("forged webhook rejected", True, "verify-webhook-signature -> not SUCCESS", t0)
