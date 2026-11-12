"""
PaymentService: the only code path that moves money.

Authoritative capture path (one function, one idempotency key):
    buyer approves on PayPal -> our server calls capture  (capture_approved_order)
The return URL is the normal trigger; CHECKOUT.ORDER.APPROVED is the recovery trigger for buyers
who close the tab. Both call the same function with PayPal-Request-Id "order:{id}:capture", so
PayPal executes at most one capture. PAYMENT.CAPTURE.* webhooks then confirm or correct state.

Every capture response is reconciled: only status COMPLETED with matching amount, currency and
custom_id moves an order to CAPTURED. PENDING goes to PAYMENT_PENDING (no kitchen ticket);
DECLINED to PAYMENT_DENIED; anything that disagrees with our quote to NEEDS_REVIEW.

PayPal calls never run inside an open database transaction.
"""

from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.db.models import ApprovalModel, CallModel, OrderModel, Shop, utc_now
from apps.api.db.repository import OrderRepository
from apps.core.settings import Settings
from packages.domain.fsm import can_transition, refund_action
from packages.domain.guard import Guard
from packages.domain.models import (
    ActorType,
    GuardDecisionKind,
    OrderAction,
    OrderState,
    Quote,
    QuoteLine,
    ShopPolicy,
)
from packages.domain.money import PayPalLine, value_to_cents, MoneyError
from packages.domain.reconcile import CaptureOutcome, reconcile_capture
from packages.paypal_gateway.types import PayPalError, PayPalGateway, approval_link, extract_capture

logger = logging.getLogger("counter.payments")

DEFAULT_MANDATE_CAP_CENTS = 5000


@dataclass
class FlowResult:
    ok: bool
    reason: str = ""
    order_id: Optional[str] = None
    state: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)


def quote_from_order(order: OrderModel) -> Quote:
    lines = tuple(
        QuoteLine(
            item_id=line.item_id,
            name=line.name,
            qty=line.qty,
            unit_base_cents=line.unit_total_cents,
            modifiers_delta_cents=0,
            unit_total_cents=line.unit_total_cents,
            line_total_cents=line.line_total_cents,
        )
        for line in order.lines
    )
    return Quote(
        shop_id=order.shop_id,
        lines=lines,
        subtotal_cents=order.subtotal_cents,
        tax_rate_bps=0,
        tax_cents=order.tax_cents,
        total_cents=order.total_cents,
        quote_hash=order.quote_hash or "",
        spoken_summary="",
    )


def paypal_lines(order: OrderModel) -> list[PayPalLine]:
    out = []
    for line in order.lines:
        mods = [m["name"] for m in json.loads(line.modifiers_json or "[]")]
        name = line.name + (f" ({', '.join(mods)})" if mods else "")
        out.append(PayPalLine(name=name, qty=line.qty, unit_cents=line.unit_total_cents))
    return out


class PaymentService:
    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        paypal: PayPalGateway,
        settings: Settings,
        policy: Optional[ShopPolicy] = None,
    ):
        self.sm = session_maker
        self.paypal = paypal
        self.settings = settings
        self.guard = Guard(policy or ShopPolicy())

    # ------------------------------------------------------------------ helpers

    async def _slot_for(self, repo: OrderRepository, order: OrderModel) -> tuple[Optional[str], bool]:
        if order.pickup_slot_id:
            return order.pickup_slot_id, True
        prep_minutes = 15
        slot = await repo.next_slot_with_capacity(order.shop_id, utc_now() + timedelta(minutes=prep_minutes))
        return (slot.id if slot else None), slot is not None

    async def _shop_open(self, session: AsyncSession, shop_id: str) -> bool:
        shop = await session.get(Shop, shop_id)
        return bool(shop and shop.is_open)

    async def _reserve_slot(self, repo: OrderRepository, order: OrderModel, slot_id: str) -> bool:
        if order.pickup_slot_id == slot_id:
            return True
        if not await repo.book_slot(slot_id):
            return False
        order.pickup_slot_id = slot_id
        from apps.api.db.models import PickupSlotModel

        slot = await repo.session.get(PickupSlotModel, slot_id)
        order.pickup_at = slot.starts_at if slot else None
        await repo.session.flush()
        return True

    # ------------------------------------------------------------------ payment link

    async def send_payment_link(self, order_id: str, quote_hash: str, *, vault_opt_in: bool) -> FlowResult:
        async with self.sm() as session, session.begin():
            repo = OrderRepository(session)
            order = await repo.get_order(order_id)
            if order is None:
                return FlowResult(False, "Order not found", order_id)
            slot_id, slot_available = await self._slot_for(repo, order)
            decision = self.guard.evaluate_send_payment_link(
                current_state=OrderState(order.state),
                provided_quote_hash=quote_hash,
                current_quote=quote_from_order(order),
                slot_available=slot_available,
                shop_open=await self._shop_open(session, order.shop_id),
            )
            if not decision.allowed:
                return FlowResult(False, decision.reason, order_id, order.state, {"kind": decision.kind.value})
            assert slot_id is not None
            if not await self._reserve_slot(repo, order, slot_id):
                return FlowResult(False, "Pickup slot filled up; please re-quote", order_id, order.state)
            order.vault_opt_in = vault_opt_in
            version = order.version
            snapshot = (order.id, order.currency, paypal_lines(order), order.subtotal_cents,
                        order.tax_cents, order.total_cents, order.quote_hash or "")

        oid, currency, lines, subtotal, tax, total, qhash = snapshot
        web = self.settings.web_base_url.rstrip("/")
        try:
            pp = await self.paypal.create_order(
                order_id=oid,
                currency=currency,
                lines=lines,
                subtotal_cents=subtotal,
                tax_cents=tax,
                total_cents=total,
                return_url=f"{web}/api/v1/paypal/return?order_id={oid}",
                cancel_url=f"{web}/api/v1/paypal/cancel?order_id={oid}",
                vault_on_success=vault_opt_in,
                request_id=f"order:{oid}:create:{qhash[:16]}",
            )
        except PayPalError as exc:
            logger.error("PayPal create_order failed for %s: %s", oid, exc)
            async with self.sm() as session, session.begin():
                repo = OrderRepository(session)
                order = await repo.get_order(oid)
                if order is not None:
                    await repo.release_slot(order.pickup_slot_id)
                    order.pickup_slot_id = None
            return FlowResult(False, "PayPal is unavailable right now", oid)

        link = approval_link(pp)
        if not link:
            return FlowResult(False, "PayPal did not return an approval link", oid)
        async with self.sm() as session, session.begin():
            repo = OrderRepository(session)
            order = await repo.transition(
                oid, version, OrderAction.SEND_LINK, ActorType.AGENT,
                {"paypal_order_id": pp["id"]},
                paypal_order_id=pp["id"], approval_url=link, link_sent_at=utc_now(),
            )
            return FlowResult(True, "Payment link created", oid, order.state,
                              {"approval_url": link, "paypal_order_id": pp["id"]})

    # ------------------------------------------------------------------ capture (authoritative)

    async def capture_approved_order(self, order_id: str, *, trigger: str) -> FlowResult:
        async with self.sm() as session:
            order = await OrderRepository(session).get_order(order_id)
            if order is None:
                return FlowResult(False, "Order not found", order_id)
            state = OrderState(order.state)
            if state not in (OrderState.LINK_SENT, OrderState.APPROVED):
                return FlowResult(state in (OrderState.CAPTURED, OrderState.PAYMENT_PENDING),
                                  f"Nothing to capture in state {state.value}", order_id, state.value)
            pp_id = order.paypal_order_id
        if not pp_id:
            return FlowResult(False, "Order has no PayPal order", order_id)

        try:
            response = await self.paypal.capture_order(pp_id, request_id=f"order:{order_id}:capture")
        except PayPalError as exc:
            if exc.issue == "ORDER_ALREADY_CAPTURED":
                response = await self.paypal.get_order(pp_id)
            else:
                logger.warning("Capture of %s failed (%s): %s", order_id, trigger, exc.issue or exc)
                return FlowResult(False, f"Capture failed: {exc.issue or exc.status_code}", order_id)

        async with self.sm() as session, session.begin():
            repo = OrderRepository(session)
            order = await repo.get_order(order_id)
            assert order is not None
            order = await self._apply_capture(repo, order, response, source="link", trigger=trigger)
            return FlowResult(order.state == OrderState.CAPTURED.value, order.state, order_id, order.state)

    async def _apply_capture(
        self,
        repo: OrderRepository,
        order: OrderModel,
        response: dict[str, Any],
        *,
        source: str,
        trigger: str,
        mandate_id: Optional[str] = None,
    ) -> OrderModel:
        facts = extract_capture(response)
        if facts is None:
            return await self._flag(repo, order, "PayPal response contained no capture")
        result = reconcile_capture(
            expected_order_id=order.id,
            expected_total_cents=order.total_cents,
            expected_currency=order.currency,
            status=facts.status,
            amount_value=facts.amount_value,
            currency=facts.currency,
            custom_id=facts.custom_id,
        )
        fee = None
        if facts.fee_value:
            try:
                fee = value_to_cents(facts.fee_value)
            except MoneyError:
                fee = None
        await repo.upsert_payment(
            order_id=order.id,
            paypal_order_id=str(response.get("id", order.paypal_order_id or "")),
            capture_id=facts.capture_id,
            status=facts.status,
            amount_cents=result.captured_cents or 0,
            currency=facts.currency or order.currency,
            fee_cents=fee,
            source=source,
            mandate_id=mandate_id,
        )
        meta = {"capture_id": facts.capture_id, "capture_status": facts.status, "trigger": trigger}
        if source == "vault":
            order.paypal_order_id = str(response.get("id", ""))
            await repo.session.flush()

        if result.outcome == CaptureOutcome.MISMATCH:
            return await self._flag(repo, order, "; ".join(result.reasons), meta)

        if OrderState(order.state) == OrderState.LINK_SENT:
            order = await repo.transition(order.id, order.version, OrderAction.APPROVE, ActorType.PAYPAL, meta)

        if result.outcome == CaptureOutcome.COMPLETED:
            action = OrderAction.CHARGE_SAVED_PAYMENT if source == "vault" else OrderAction.CAPTURE
            order = await repo.transition(order.id, order.version, action, ActorType.PAYPAL, meta)
            if facts.vault_id and order.vault_opt_in:
                await repo.create_mandate(
                    vault_id=facts.vault_id,
                    customer_hash=order.caller_hash,
                    shop_id=order.shop_id,
                    max_charge_cents=DEFAULT_MANDATE_CAP_CENTS,
                    source_order_id=order.id,
                )
        elif result.outcome == CaptureOutcome.PENDING:
            order = await repo.transition(order.id, order.version, OrderAction.MARK_PAYMENT_PENDING, ActorType.PAYPAL,
                                          {**meta, "reason": facts.status_reason})
        else:  # DENIED
            order = await repo.transition(order.id, order.version, OrderAction.DENY_PAYMENT, ActorType.PAYPAL, meta)
            await repo.release_slot(order.pickup_slot_id)
        return order

    async def _flag(
        self, repo: OrderRepository, order: OrderModel, reason: str, meta: Optional[dict[str, Any]] = None
    ) -> OrderModel:
        logger.error("RECONCILIATION ALERT order=%s: %s", order.id, reason)
        if not can_transition(OrderState(order.state), OrderAction.FLAG_REVIEW):
            await repo.append_event(order.id, "RECONCILIATION_ALERT", {"reason": reason, **(meta or {})},
                                    ActorType.SYSTEM)
            return order
        return await repo.transition(order.id, order.version, OrderAction.FLAG_REVIEW, ActorType.SYSTEM,
                                     {"reason": reason, **(meta or {})}, review_reason=reason)

    # ------------------------------------------------------------------ saved wallet

    async def charge_saved_payment(self, order_id: str, quote_hash: str, *, call_id: str) -> FlowResult:
        async with self.sm() as session, session.begin():
            repo = OrderRepository(session)
            order = await repo.get_order(order_id)
            call = await session.get(CallModel, call_id)
            if order is None or call is None or order.call_id != call_id:
                return FlowResult(False, "Order does not belong to this call", order_id)
            mandate_row = await repo.active_mandate(call.caller_hash, order.shop_id)
            mandate = repo.to_domain_mandate(mandate_row) if mandate_row else None
            step_up = await repo.verified_step_up(call_id, mandate.id) if mandate else None
            charges = (
                await repo.vault_charges_since(mandate.id, utc_now() - timedelta(hours=24)) if mandate else 0
            )
            slot_id, slot_available = await self._slot_for(repo, order)
            decision = self.guard.evaluate_charge_saved_payment(
                current_state=OrderState(order.state),
                provided_quote_hash=quote_hash,
                current_quote=quote_from_order(order),
                mandate=mandate,
                consent=await repo.latest_consent(order_id),
                step_up=step_up,
                vault_charges_today=charges,
                call_id=call_id,
                order_id=order_id,
                now=utc_now(),
                slot_available=slot_available,
                shop_open=await self._shop_open(session, order.shop_id),
            )
            if not decision.allowed:
                await repo.append_event(order_id, "VAULT_CHARGE_DENIED", {"reason": decision.reason}, ActorType.SYSTEM)
                return FlowResult(False, decision.reason, order_id, order.state)
            assert mandate is not None and slot_id is not None
            if not await self._reserve_slot(repo, order, slot_id):
                return FlowResult(False, "Pickup slot filled up", order_id, order.state)
            snapshot = (order.currency, paypal_lines(order), order.subtotal_cents, order.tax_cents, order.total_cents)

        currency, lines, subtotal, tax, total = snapshot
        try:
            response = await self.paypal.charge_vault(
                vault_id=mandate.vault_id,
                order_id=order_id,
                currency=currency,
                lines=lines,
                subtotal_cents=subtotal,
                tax_cents=tax,
                total_cents=total,
                request_id=f"order:{order_id}:vault:{quote_hash[:16]}",
            )
        except PayPalError as exc:
            logger.warning("Vault charge failed for %s: %s", order_id, exc.issue or exc)
            async with self.sm() as session, session.begin():
                repo = OrderRepository(session)
                order = await repo.get_order(order_id)
                if order is not None:
                    await repo.release_slot(order.pickup_slot_id)
                    order.pickup_slot_id = None
                    await repo.append_event(order_id, "VAULT_CHARGE_FAILED", {"issue": exc.issue}, ActorType.PAYPAL)
            return FlowResult(False, "PayPal declined the saved wallet", order_id)

        async with self.sm() as session, session.begin():
            repo = OrderRepository(session)
            order = await repo.get_order(order_id)
            assert order is not None
            order = await self._apply_capture(repo, order, response, source="vault", trigger="vault_charge",
                                              mandate_id=mandate.id)
            return FlowResult(order.state == OrderState.CAPTURED.value, order.state, order_id, order.state,
                              {"amount_cents": order.total_cents})

    # ------------------------------------------------------------------ cancel / refund

    async def cancel_unpaid(self, order_id: str, *, actor: ActorType, actor_id: Optional[str] = None) -> FlowResult:
        async with self.sm() as session, session.begin():
            repo = OrderRepository(session)
            order = await repo.get_order(order_id)
            if order is None:
                return FlowResult(False, "Order not found", order_id)
            if not can_transition(OrderState(order.state), OrderAction.CANCEL):
                return FlowResult(False, f"Cannot cancel in state {order.state}", order_id, order.state)
            await repo.release_slot(order.pickup_slot_id)
            order = await repo.transition(order_id, order.version, OrderAction.CANCEL, actor, actor_id=actor_id)
            return FlowResult(True, "Cancelled", order_id, order.state)

    async def caller_refund(self, order_id: str, *, caller_hash: str) -> FlowResult:
        """Policy refund requested by phone. The amount comes from the guard, never the caller."""
        async with self.sm() as session, session.begin():
            repo = OrderRepository(session)
            order = await repo.get_order(order_id)
            if order is None:
                return FlowResult(False, "Order not found", order_id)
            payment = await repo.payment_for_order(order_id)
            if payment is None or payment.status != "COMPLETED":
                return FlowResult(False, "No completed payment to refund", order_id, order.state)
            already = await repo.refunded_total(payment.capture_id)
            decision = self.guard.evaluate_refund(
                current_state=OrderState(order.state),
                captured_amount_cents=payment.amount_cents,
                already_refunded_cents=already,
                caller_authenticated=hmac.compare_digest(order.caller_hash, caller_hash),
            )
            if decision.kind == GuardDecisionKind.NEEDS_APPROVAL:
                approval = await repo.create_approval(
                    order_id=order_id, amount_cents=decision.computed_amount_cents or 0, reason=decision.reason
                )
                await repo.append_event(order_id, "REFUND_APPROVAL_REQUESTED",
                                        {"approval_id": approval.id, "amount_cents": approval.requested_amount_cents},
                                        ActorType.AGENT)
                return FlowResult(False, decision.reason, order_id, order.state,
                                  {"needs_approval": True, "approval_id": approval.id})
            if not decision.allowed:
                return FlowResult(False, decision.reason, order_id, order.state)
            amount = decision.computed_amount_cents or 0
        return await self._execute_refund(order_id, amount, source="agent", approved_by=None,
                                          request_id=f"order:{order_id}:refund:policy:{already}",
                                          reason=decision.reason)

    async def owner_refund(self, order_id: str, amount_cents: int, *, owner: str, client_request_id: str) -> FlowResult:
        return await self._execute_refund(order_id, amount_cents, source="owner", approved_by=owner,
                                          request_id=f"order:{order_id}:refund:owner:{client_request_id}",
                                          reason="Owner refund")

    async def decide_approval(self, approval_id: str, *, approve: bool, decided_by: str) -> FlowResult:
        async with self.sm() as session, session.begin():
            approval = await session.get(ApprovalModel, approval_id, with_for_update=True)
            if approval is None or approval.status != "PENDING":
                return FlowResult(False, "Approval not pending")
            approval.status = "APPROVED" if approve else "REJECTED"
            approval.decided_by = decided_by
            approval.decided_at = utc_now()
            order_id, amount = approval.order_id, approval.requested_amount_cents
            await OrderRepository(session).append_event(
                order_id, f"REFUND_{approval.status}", {"approval_id": approval_id, "amount_cents": amount},
                ActorType.OWNER, actor_id=decided_by,
            )
        if not approve:
            return FlowResult(True, "Rejected", order_id)
        return await self._execute_refund(order_id, amount, source="owner", approved_by=decided_by,
                                          request_id=f"approval:{approval_id}", reason="Approved refund")

    async def _execute_refund(
        self, order_id: str, amount: int, *, source: str, approved_by: Optional[str], request_id: str, reason: str
    ) -> FlowResult:
        async with self.sm() as session:
            repo = OrderRepository(session)
            order = await repo.get_order(order_id)
            payment = await repo.payment_for_order(order_id)
            if order is None or payment is None or payment.status != "COMPLETED":
                return FlowResult(False, "No completed payment to refund", order_id)
            already = await repo.refunded_total(payment.capture_id)
            bound = self.guard.evaluate_owner_refund(
                current_state=OrderState(order.state),
                requested_cents=amount,
                captured_amount_cents=payment.amount_cents,
                already_refunded_cents=already,
            )
            if not bound.allowed:
                return FlowResult(False, bound.reason, order_id, order.state)
            capture_id, currency, captured = payment.capture_id, payment.currency, payment.amount_cents

        try:
            resp = await self.paypal.refund_capture(
                capture_id, amount_cents=amount, currency=currency, request_id=request_id,
                note_to_payer="Refund from your takeaway order",
            )
        except PayPalError as exc:
            logger.error("Refund failed for %s: %s", order_id, exc.issue or exc)
            return FlowResult(False, f"PayPal refused the refund: {exc.issue or exc.status_code}", order_id)

        async with self.sm() as session, session.begin():
            repo = OrderRepository(session)
            await self._record_refund(repo, order_id, capture_id, captured, resp, currency, source, approved_by, reason)
            order = await repo.get_order(order_id)
            assert order is not None
            return FlowResult(resp.get("status") == "COMPLETED", str(resp.get("status")), order_id, order.state,
                              {"refund_cents": amount, "paypal_refund_id": resp.get("id")})

    async def _record_refund(
        self,
        repo: OrderRepository,
        order_id: str,
        capture_id: str,
        captured_cents: int,
        resp: dict[str, Any],
        currency: str,
        source: str,
        approved_by: Optional[str],
        reason: str,
    ) -> None:
        refund_id = str(resp.get("id", ""))
        status = str(resp.get("status", "PENDING"))
        existing = await repo.refund_by_paypal_id(refund_id) if refund_id else None
        if existing is not None:
            if existing.status == status:
                return
            existing.status = status
            await repo.session.flush()
        else:
            amount = value_to_cents(resp["amount"]["value"])
            await repo.record_refund(
                order_id=order_id, capture_id=capture_id, paypal_refund_id=refund_id or None,
                amount_cents=amount, currency=currency, reason=reason, status=status,
                source=source, approved_by=approved_by,
            )
        if status != "COMPLETED":
            return
        total = await repo.refunded_total(capture_id)
        order = await repo.get_order(order_id)
        assert order is not None
        action = refund_action(captured_cents, min(total, captured_cents))
        if can_transition(OrderState(order.state), action):
            await repo.transition(order_id, order.version, action, ActorType.PAYPAL,
                                  {"refunded_total_cents": total, "paypal_refund_id": refund_id},
                                  actor_id=approved_by)

    # ------------------------------------------------------------------ webhook application

    async def apply_webhook(self, session: AsyncSession, event_type: str, resource: dict[str, Any]) -> str:
        """Apply a verified PayPal event inside the caller's transaction. Returns an outcome label."""
        repo = OrderRepository(session)

        if event_type.startswith("PAYMENT.CAPTURE."):
            if event_type == "PAYMENT.CAPTURE.REFUNDED":
                return await self._webhook_refund(repo, resource)
            order = await self._order_for_capture(repo, resource)
            if order is None:
                return "ignored:unknown_order"
            wrapped = {"id": order.paypal_order_id, "purchase_units": [
                {"custom_id": resource.get("custom_id"), "payments": {"captures": [resource]}}]}
            state = OrderState(order.state)
            if event_type == "PAYMENT.CAPTURE.COMPLETED":
                if state in (OrderState.LINK_SENT, OrderState.APPROVED, OrderState.PAYMENT_PENDING, OrderState.QUOTED):
                    if state == OrderState.PAYMENT_PENDING:
                        facts = extract_capture(wrapped)
                        assert facts is not None
                        result = reconcile_capture(
                            expected_order_id=order.id, expected_total_cents=order.total_cents,
                            expected_currency=order.currency, status=facts.status,
                            amount_value=facts.amount_value, currency=facts.currency, custom_id=facts.custom_id,
                        )
                        if result.outcome != CaptureOutcome.COMPLETED:
                            await self._flag(repo, order, "; ".join(result.reasons) or "unexpected capture")
                            return "flagged"
                        await repo.upsert_payment(order_id=order.id, paypal_order_id=order.paypal_order_id or "",
                                                  capture_id=facts.capture_id, status="COMPLETED",
                                                  amount_cents=result.captured_cents or 0, currency=facts.currency,
                                                  fee_cents=None, source="link")
                        await repo.transition(order.id, order.version, OrderAction.CAPTURE, ActorType.PAYPAL,
                                              {"capture_id": facts.capture_id, "trigger": "webhook"})
                        return "captured_after_pending"
                    await self._apply_capture(repo, order, wrapped, source="link", trigger="webhook")
                    return "captured_from_webhook"
                await repo.append_event(order.id, "PAYPAL_CAPTURE_CONFIRMED", {"capture_id": resource.get("id")},
                                        ActorType.PAYPAL)
                return "confirmed"
            if event_type == "PAYMENT.CAPTURE.PENDING":
                if can_transition(state, OrderAction.MARK_PAYMENT_PENDING):
                    await repo.transition(order.id, order.version, OrderAction.MARK_PAYMENT_PENDING,
                                          ActorType.PAYPAL, {"capture_id": resource.get("id")})
                    return "pending"
                return "noop"
            if event_type in ("PAYMENT.CAPTURE.DENIED", "PAYMENT.CAPTURE.DECLINED"):
                if can_transition(state, OrderAction.DENY_PAYMENT):
                    await repo.transition(order.id, order.version, OrderAction.DENY_PAYMENT, ActorType.PAYPAL,
                                          {"capture_id": resource.get("id")})
                    await repo.release_slot(order.pickup_slot_id)
                    return "denied"
                await self._flag(repo, order, f"{event_type} received in state {state.value}")
                return "flagged"
            if event_type == "PAYMENT.CAPTURE.REVERSED":
                if can_transition(state, OrderAction.REVERSE):
                    await repo.transition(order.id, order.version, OrderAction.REVERSE, ActorType.PAYPAL,
                                          {"capture_id": resource.get("id")})
                    logger.error("REVERSAL ALERT order=%s capture=%s", order.id, resource.get("id"))
                    return "reversed"
                return "noop"
            return "ignored:unhandled_capture_event"

        if event_type == "VAULT.PAYMENT-TOKEN.CREATED":
            pp_order_id = (resource.get("metadata") or {}).get("order_id")
            order = await repo.get_order_by_paypal_id(pp_order_id) if pp_order_id else None
            if order is None or not order.vault_opt_in or not resource.get("id"):
                return "ignored:no_opt_in"
            await repo.create_mandate(vault_id=str(resource["id"]), customer_hash=order.caller_hash,
                                      shop_id=order.shop_id, max_charge_cents=DEFAULT_MANDATE_CAP_CENTS,
                                      source_order_id=order.id)
            return "mandate_created"

        if event_type == "INVOICING.INVOICE.PAID":
            invoice_number = (resource.get("detail") or {}).get("invoice_number")
            order = await repo.get_order(invoice_number) if invoice_number else None
            if order and can_transition(OrderState(order.state), OrderAction.CONFIRM_CATERING):
                await repo.transition(order.id, order.version, OrderAction.CONFIRM_CATERING, ActorType.PAYPAL)
                return "catering_confirmed"
            return "noop"

        return "ignored:unhandled_type"

    async def _order_for_capture(self, repo: OrderRepository, capture: dict[str, Any]) -> Optional[OrderModel]:
        if capture.get("custom_id"):
            order = await repo.get_order(str(capture["custom_id"]))
            if order:
                return order
        related = ((capture.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
        if related:
            return await repo.get_order_by_paypal_id(str(related))
        return None

    async def _webhook_refund(self, repo: OrderRepository, refund: dict[str, Any]) -> str:
        capture_id = None
        for link in refund.get("links", []) or []:
            if link.get("rel") == "up" and "/captures/" in str(link.get("href")):
                capture_id = str(link["href"]).rstrip("/").split("/")[-1]
        payment = await repo.payment_by_capture(capture_id) if capture_id else None
        if payment is None:
            return "ignored:unknown_capture"
        await self._record_refund(repo, payment.order_id, payment.capture_id, payment.amount_cents, refund,
                                  payment.currency, "paypal", None, "Refund reported by PayPal")
        return "refund_recorded"

    # ------------------------------------------------------------------ merchant transitions

    async def merchant_transition(self, order_id: str, action: OrderAction, *, owner: str) -> FlowResult:
        allowed = {OrderAction.KITCHEN_ACCEPT, OrderAction.MARK_READY, OrderAction.PICKUP, OrderAction.RESOLVE_REVIEW}
        if action not in allowed:
            return FlowResult(False, "Action not available to merchants", order_id)
        async with self.sm() as session, session.begin():
            repo = OrderRepository(session)
            order = await repo.get_order(order_id)
            if order is None:
                return FlowResult(False, "Order not found", order_id)
            if not can_transition(OrderState(order.state), action):
                return FlowResult(False, f"Cannot {action.value} from {order.state}", order_id, order.state)
            order = await repo.transition(order_id, order.version, action, ActorType.OWNER, actor_id=owner)
            return FlowResult(True, "ok", order_id, order.state)
