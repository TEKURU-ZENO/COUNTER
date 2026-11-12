"""
End-to-end payment flows against the explicit mock gateway: agent tools -> PaymentService ->
PayPal (mock) -> return URL / webhooks -> worker -> ledger. Assertions read the database.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import func, select

from apps.agent.llm import call, say
from apps.api.db.models import (
    ConsentEventModel,
    OrderModel,
    OutboxModel,
    PaymentModel,
    RefundModel,
    VaultMandateModel,
    WebhookEventModel,
)
from apps.core.container import Container
from apps.worker.worker import CounterWorker
from packages.paypal_gateway import MockPayPalGateway, sign_mock_webhook
from tests.conftest import last_tool_result, login, start_call

MARGHERITA = {"item": "item_pizza_margherita", "qty": 1, "modifiers": []}


def state_of(kwargs: dict[str, Any]) -> dict[str, Any]:
    system = kwargs["system"]
    return json.loads(system.split("CURRENT CALL STATE (authoritative):\n", 1)[1].strip())


def with_hash(name: str, **extra: Any):  # type: ignore[no-untyped-def]
    return lambda kw: call(name, {"quote_hash": state_of(kw)["quote_hash"], **extra})


async def _order(c: Container, order_id: str) -> OrderModel:
    async with c.session_maker() as s:
        order = await s.get(OrderModel, order_id)
        assert order is not None
        return order


async def _place_link_order(container: Container, items: list[dict[str, Any]] | None = None, **link_kwargs: Any):
    session, llm = await start_call(container, [
        call("set_cart", {"items": items or [MARGHERITA]}), say("That's one Margherita, $15.70. Link?"),
        with_hash("send_payment_link", **link_kwargs), say("Sent!"),
    ])
    await session.handle_turn("One margherita please")
    result = await session.handle_turn("Yes send the link")
    assert result.order and result.order["state"] == "LINK_SENT", result
    return session, result.order


# ---------------------------------------------------------------------- link flow


async def test_link_flow_captures_once_via_return_url(container: Container, client) -> None:
    session, order = await _place_link_order(container)
    gw = container.paypal
    assert isinstance(gw, MockPayPalGateway)
    pp_id = (await _order(container, order["id"])).paypal_order_id
    gw.approve(pp_id)

    r1 = await client.get(f"/api/v1/paypal/return?order_id={order['id']}&token={pp_id}")
    r2 = await client.get(f"/api/v1/paypal/return?order_id={order['id']}&token={pp_id}")
    assert r1.status_code == r2.status_code == 303
    assert "/order-status/" in r1.headers["location"]

    o = await _order(container, order["id"])
    assert o.state == "CAPTURED"
    async with container.session_maker() as s:
        payments = (await s.execute(select(PaymentModel).where(PaymentModel.order_id == o.id))).scalars().all()
    assert len(payments) == 1 and payments[0].amount_cents == 1570 and payments[0].currency == "USD"
    assert [k for name, k in gw.calls if name == "capture_order"] == [f"order:{o.id}:capture"]


async def test_return_url_without_approval_does_not_capture(container: Container, client) -> None:
    _, order = await _place_link_order(container)
    await client.get(f"/api/v1/paypal/return?order_id={order['id']}")
    assert (await _order(container, order["id"])).state == "LINK_SENT"


async def test_pending_capture_never_reaches_kitchen(container: Container, client) -> None:
    _, order = await _place_link_order(container)
    gw = container.paypal
    assert isinstance(gw, MockPayPalGateway)
    pp_id = (await _order(container, order["id"])).paypal_order_id
    gw.approve(pp_id)
    gw.next_capture_status = "PENDING"
    await client.get(f"/api/v1/paypal/return?order_id={order['id']}")
    assert (await _order(container, order["id"])).state == "PAYMENT_PENDING"

    await login(client)
    r = await client.post(f"/api/v1/merchant/orders/{order['id']}/actions", json={"action": "kitchen_accept"})
    assert r.status_code == 409

    capture_id = next(iter(gw.captures))
    await _deliver_webhook(container, client, "PAYMENT.CAPTURE.COMPLETED", {
        "id": capture_id, "status": "COMPLETED", "custom_id": order["id"],
        "amount": {"currency_code": "USD", "value": "15.70"}})
    assert (await _order(container, order["id"])).state == "CAPTURED"
    r = await client.post(f"/api/v1/merchant/orders/{order['id']}/actions", json={"action": "kitchen_accept"})
    assert r.status_code == 200 and r.json()["state"] == "IN_KITCHEN"


async def test_amount_mismatch_goes_to_review(container: Container, client) -> None:
    _, order = await _place_link_order(container)
    gw = container.paypal
    assert isinstance(gw, MockPayPalGateway)
    gw.approve((await _order(container, order["id"])).paypal_order_id)
    gw.next_capture_amount_value = "1.00"
    await client.get(f"/api/v1/paypal/return?order_id={order['id']}")
    o = await _order(container, order["id"])
    assert o.state == "NEEDS_REVIEW" and "amount 100 != quoted 1570" in (o.review_reason or "")


async def test_declined_capture_releases_slot(container: Container, client) -> None:
    _, order = await _place_link_order(container)
    gw = container.paypal
    assert isinstance(gw, MockPayPalGateway)
    gw.approve((await _order(container, order["id"])).paypal_order_id)
    gw.next_capture_status = "DECLINED"
    await client.get(f"/api/v1/paypal/return?order_id={order['id']}")
    assert (await _order(container, order["id"])).state == "PAYMENT_DENIED"


async def test_send_link_rejects_stale_quote_hash(container: Container) -> None:
    session, llm = await start_call(container, [
        call("set_cart", {"items": [MARGHERITA]}), say("ok"),
        call("send_payment_link", {"quote_hash": "0" * 64}), say("hmm"),
    ])
    await session.handle_turn("one margherita")
    await session.handle_turn("send it")
    assert "does not match" in last_tool_result(llm, "send_payment_link")["error"]
    assert (await _order(container, session.state.order_id or "")).state == "QUOTED"


# ---------------------------------------------------------------------- webhooks


async def _deliver_webhook(container: Container, client, event_type: str, resource: dict[str, Any],
                           event_id: str | None = None) -> None:
    body = json.dumps({"id": event_id or f"WH-{event_type}-{resource.get('id')}", "event_type": event_type,
                       "create_time": "2026-11-12T18:00:00Z", "resource": resource}).encode()
    headers = sign_mock_webhook(container.settings.secret_key.get_secret_value(), body)
    r = await client.post("/api/v1/paypal/webhooks", content=body, headers=headers)
    assert r.status_code == 200, r.text
    await CounterWorker(container).process_webhooks()


async def test_webhook_rejects_missing_headers_bad_signature_and_missing_id(container: Container, client) -> None:
    body = json.dumps({"id": "WH-1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {}}).encode()
    assert (await client.post("/api/v1/paypal/webhooks", content=body)).status_code == 400

    headers = sign_mock_webhook(container.settings.secret_key.get_secret_value(), body)
    headers["paypal-transmission-sig"] = "forged"
    assert (await client.post("/api/v1/paypal/webhooks", content=body, headers=headers)).status_code == 400

    no_id = json.dumps({"event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {}}).encode()
    headers = sign_mock_webhook(container.settings.secret_key.get_secret_value(), no_id)
    assert (await client.post("/api/v1/paypal/webhooks", content=no_id, headers=headers)).status_code == 400

    tampered_headers = sign_mock_webhook(container.settings.secret_key.get_secret_value(), body)
    tampered = body.replace(b"WH-1", b"WH-2")
    assert (await client.post("/api/v1/paypal/webhooks", content=tampered, headers=tampered_headers)).status_code == 400

    async with container.session_maker() as s:
        assert await s.scalar(select(func.count()).select_from(WebhookEventModel)) == 0


async def test_duplicate_webhook_is_stored_and_applied_once(container: Container, client) -> None:
    _, order = await _place_link_order(container)
    gw = container.paypal
    assert isinstance(gw, MockPayPalGateway)
    pp_id = (await _order(container, order["id"])).paypal_order_id
    gw.approve(pp_id)
    body = json.dumps({"id": "WH-APPROVED-1", "event_type": "CHECKOUT.ORDER.APPROVED",
                       "resource": {"id": pp_id, "status": "APPROVED"}}).encode()
    headers = sign_mock_webhook(container.settings.secret_key.get_secret_value(), body)
    first = await client.post("/api/v1/paypal/webhooks", content=body, headers=headers)
    second = await client.post("/api/v1/paypal/webhooks", content=body, headers=headers)
    assert first.json()["duplicate"] is False and second.json()["duplicate"] is True
    worker = CounterWorker(container)
    assert await worker.process_webhooks() == 1
    assert await worker.process_webhooks() == 0
    assert (await _order(container, order["id"])).state == "CAPTURED"


# ---------------------------------------------------------------------- refunds


async def _captured_order(container: Container, client) -> str:
    _, order = await _place_link_order(container)
    gw = container.paypal
    assert isinstance(gw, MockPayPalGateway)
    gw.approve((await _order(container, order["id"])).paypal_order_id)
    await client.get(f"/api/v1/paypal/return?order_id={order['id']}")
    return str(order["id"])


async def test_owner_partial_refunds_are_bounded_by_captured_amount(container: Container, client) -> None:
    order_id = await _captured_order(container, client)
    await login(client)
    r = await client.post(f"/api/v1/merchant/orders/{order_id}/refunds",
                          json={"amount_cents": 500, "client_request_id": "req-aaaaaaa1"})
    assert r.status_code == 200 and r.json()["state"] == "REFUNDED_PARTIAL"
    r = await client.post(f"/api/v1/merchant/orders/{order_id}/refunds",
                          json={"amount_cents": 1071, "client_request_id": "req-aaaaaaa2"})
    assert r.status_code == 409 and "exceeds the remaining" in r.json()["detail"]
    r = await client.post(f"/api/v1/merchant/orders/{order_id}/refunds",
                          json={"amount_cents": 1070, "client_request_id": "req-aaaaaaa3"})
    assert r.status_code == 200 and r.json()["state"] == "REFUNDED_FULL"

    from apps.api.copilot import DateRange, _ledger_summary

    from datetime import date, timedelta

    today = date.today()
    window = DateRange(start_date=(today - timedelta(days=1)).isoformat(), end_date=(today + timedelta(days=1)).isoformat())
    summary = await _ledger_summary(container, window)
    assert summary["refunded_cents"] == 1570 and summary["refund_count"] == 2


async def test_refund_webhook_is_recorded_once(container: Container, client) -> None:
    order_id = await _captured_order(container, client)
    async with container.session_maker() as s:
        capture_id = (await s.execute(select(PaymentModel.capture_id).where(PaymentModel.order_id == order_id))).scalar_one()
    refund = {"id": "REF-EXT-1", "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "3.00"},
              "links": [{"rel": "up", "href": f"https://api.sandbox.paypal.com/v2/payments/captures/{capture_id}"}]}
    await _deliver_webhook(container, client, "PAYMENT.CAPTURE.REFUNDED", refund, event_id="WH-R1")
    await _deliver_webhook(container, client, "PAYMENT.CAPTURE.REFUNDED", refund, event_id="WH-R2")
    async with container.session_maker() as s:
        refunds = (await s.execute(select(RefundModel).where(RefundModel.order_id == order_id))).scalars().all()
    assert len(refunds) == 1 and refunds[0].amount_cents == 300
    assert (await _order(container, order_id)).state == "REFUNDED_PARTIAL"


async def test_caller_refund_requires_matching_caller(container: Container, client) -> None:
    order_id = await _captured_order(container, client)
    other, llm = await start_call(container, [call("cancel_order", {}), say("sorry")], phone="+1 212 555 0199")
    await other.handle_turn("cancel my order")
    assert "error" in last_tool_result(llm, "cancel_order")
    assert (await _order(container, order_id)).state == "CAPTURED"


# ---------------------------------------------------------------------- saved PayPal (vault)


async def _create_mandate_via_link(container: Container, client, phone: str) -> None:
    session, llm = await start_call(container, [
        call("set_cart", {"items": [MARGHERITA]}), say("ok"),
        with_hash("send_payment_link", save_paypal_for_next_time=True), say("sent"),
    ], phone=phone)
    await session.handle_turn("margherita")
    await session.handle_turn("send link and save my paypal")
    gw = container.paypal
    assert isinstance(gw, MockPayPalGateway)
    order = await _order(container, session.state.order_id or "")
    gw.approve(order.paypal_order_id)
    await client.get(f"/api/v1/paypal/return?order_id={order.id}")
    async with container.session_maker() as s:
        assert await s.scalar(select(func.count()).select_from(VaultMandateModel)) == 1


async def _vault_call(container: Container, phone: str, reply: str, code_override: str | None = None):
    sms = container.sms
    code_holder: dict[str, str] = {}

    def read_code(kw: dict[str, Any]):  # type: ignore[no-untyped-def]
        messages = sms.inbox(session.state.caller_hash)  # type: ignore[attr-defined]
        code_holder["code"] = messages[-1].split("code is ")[1][:6]
        return call("verify_sms_code", {"code": code_override or code_holder["code"]})

    session, llm = await start_call(container, [
        call("set_cart", {"items": [MARGHERITA]}), say("Use your saved PayPal?"),
        with_hash("use_saved_paypal"), say("What's the code we texted?"),
        read_code,
    ], phone=phone)
    assert session.state.has_saved_paypal
    await session.handle_turn("margherita again")
    await session.handle_turn("use my saved paypal")
    verify_turn = await session.handle_turn("it's the code")
    final = await session.handle_turn(reply) if verify_turn.awaiting == "consent" else verify_turn
    return session, verify_turn, final


async def test_vault_charge_requires_code_and_spoken_yes(container: Container, client) -> None:
    phone = "+1 415 555 0142"
    await _create_mandate_via_link(container, client, phone)
    session, verify_turn, final = await _vault_call(container, phone, "yes")
    assert verify_turn.awaiting == "consent"
    assert "$15.70" in verify_turn.speech and "order code" in verify_turn.speech
    assert final.order and final.order["state"] == "CAPTURED"
    async with container.session_maker() as s:
        consent = (await s.execute(select(ConsentEventModel).where(ConsentEventModel.order_id == session.state.order_id))).scalar_one()
        vault_payment = (await s.execute(select(PaymentModel).where(PaymentModel.order_id == session.state.order_id))).scalar_one()
    assert consent.decision == "yes" and consent.transcript == "yes" and consent.amount_cents == 1570
    assert vault_payment.source == "vault" and vault_payment.mandate_id


async def test_vault_charge_not_made_on_no_or_wrong_code(container: Container, client) -> None:
    phone = "+1 415 555 0143"
    await _create_mandate_via_link(container, client, phone)
    session, _, final = await _vault_call(container, phone, "no, wait")
    assert final.order and final.order["state"] == "QUOTED"

    session2, verify_turn, _ = await _vault_call(container, phone, "yes", code_override="000000")
    assert verify_turn.awaiting == "otp"
    assert (await _order(container, session2.state.order_id or "")).state == "QUOTED"


async def test_spoofed_number_without_code_cannot_charge(container: Container, client) -> None:
    """Even a caller who spoofs the number on file cannot charge without the SMS code."""
    phone = "+1 415 555 0144"
    await _create_mandate_via_link(container, client, phone)
    session, _ = await start_call(container, [call("set_cart", {"items": [MARGHERITA]}), say("ok")], phone=phone)
    await session.handle_turn("margherita")
    # Attempt to charge directly, bypassing the agent (as a compromised tool layer would).
    result = await container.payments.charge_saved_payment(session.state.order_id or "", session.state.quote_hash or "",
                                                           call_id=session.call_id)
    assert not result.ok and "Possession check" in result.reason


async def test_barge_in_repeats_consent_sentence(container: Container, client) -> None:
    phone = "+1 415 555 0145"
    await _create_mandate_via_link(container, client, phone)
    sms = container.sms

    def read_code(kw):  # type: ignore[no-untyped-def]
        code = sms.inbox(session.state.caller_hash)[-1].split("code is ")[1][:6]  # type: ignore[attr-defined]
        return call("verify_sms_code", {"code": code})

    session, _ = await start_call(container, [
        call("set_cart", {"items": [MARGHERITA]}), say("ok"), with_hash("use_saved_paypal"), say("code?"), read_code,
    ], phone=phone)
    await session.handle_turn("margherita")
    await session.handle_turn("saved paypal")
    prompt = (await session.handle_turn("123456")).speech
    barge = await session.handle_turn("yes", interrupted=True)
    assert barge.speech == prompt and barge.awaiting == "consent"
    async with container.session_maker() as s:
        assert await s.scalar(select(func.count()).select_from(ConsentEventModel)) == 0


# ---------------------------------------------------------------------- outbox


async def test_outbox_publishes_in_per_order_sequence(container: Container) -> None:
    await _place_link_order(container)
    received: list[dict[str, Any]] = []

    async def capture(event: dict[str, Any]) -> None:
        received.append(event)

    container.events.publish = capture  # type: ignore[method-assign]
    assert await CounterWorker(container).publish_outbox() > 0
    by_order: dict[str, list[int]] = {}
    for e in received:
        by_order.setdefault(e["order_id"], []).append(e["seq"])
    assert all(seqs == sorted(seqs) for seqs in by_order.values())
    assert [e["id"] for e in received] == sorted(e["id"] for e in received)
    async with container.session_maker() as s:
        assert await s.scalar(select(func.count()).select_from(OutboxModel).where(OutboxModel.status == "PENDING")) == 0


async def test_outbox_failure_retries_without_skipping(container: Container) -> None:
    await _place_link_order(container)

    async def broken(event: dict[str, Any]) -> None:
        raise RuntimeError("redis down")

    container.events.publish = broken  # type: ignore[method-assign]
    assert await CounterWorker(container).publish_outbox() == 0
    async with container.session_maker() as s:
        rows = (await s.execute(select(OutboxModel))).scalars().all()
    assert all(r.status == "PENDING" for r in rows)
    assert any(r.attempts == 1 for r in rows)


@pytest.mark.parametrize("phone", ["+1 (415) 555-0100", "4155550100"])
async def test_caller_hash_is_keyed_and_normalised(container: Container, phone: str) -> None:
    import hashlib

    session, _ = await start_call(container, [], phone=phone)
    assert session.state.caller_hash != hashlib.sha256(b"+14155550100").hexdigest()
    assert len(session.state.caller_hash) == 64
    other, _ = await start_call(container, [], phone="+14155550100")
    assert other.state.caller_hash == session.state.caller_hash
