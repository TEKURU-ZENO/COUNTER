"""
Repository for orders, ledger, mandates, consent and webhooks.

Methods never commit. The caller owns the transaction, so a state change, its order_event,
its outbox row and (for webhooks) the webhook status update commit together or not at all.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models import (
    ApprovalModel,
    ConsentEventModel,
    MenuItemModel,
    ModifierModel,
    OrderEventModel,
    OrderLineModel,
    OrderModel,
    OutboxModel,
    PaymentModel,
    PickupSlotModel,
    RefundModel,
    StepUpChallengeModel,
    VaultMandateModel,
    WebhookEventModel,
    generate_uuid,
    utc_now,
)
from packages.domain.fsm import get_next_state, get_side_effects
from packages.domain.models import (
    ActorType,
    ConsentDecision,
    ConsentRecord,
    MenuItem,
    ModifierDefinition,
    OrderAction,
    OrderState,
    Quote,
    StepUpRecord,
    VaultMandate,
)


class RepositoryError(Exception):
    pass


class OptimisticLockConflictError(RepositoryError):
    def __init__(self, order_id: str, expected_version: int, current_version: int | None = None):
        self.order_id = order_id
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__(
            f"Order {order_id} changed concurrently (expected version {expected_version}, "
            f"found {current_version})"
        )


class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------ menu

    async def load_menu(self, shop_id: str) -> dict[str, MenuItem]:
        items = (await self.session.execute(select(MenuItemModel).where(MenuItemModel.shop_id == shop_id))).scalars()
        rows = list(items)
        mods = (
            await self.session.execute(
                select(ModifierModel).where(ModifierModel.item_id.in_([r.id for r in rows]))
            )
        ).scalars()
        by_item: dict[str, list[ModifierDefinition]] = {}
        for m in mods:
            by_item.setdefault(m.item_id, []).append(
                ModifierDefinition(m.id, m.name, m.price_delta_cents, m.group_name, m.required, m.max_select)
            )
        return {
            r.id: MenuItem(
                id=r.id,
                shop_id=r.shop_id,
                name=r.name,
                category=r.category,
                price_cents=r.price_cents,
                prep_min=r.prep_min,
                available=r.available,
                dietary_tags=tuple(json.loads(r.dietary_tags_json or "[]")),
                paypal_product_id=r.paypal_product_id,
                modifiers=tuple(by_item.get(r.id, [])),
            )
            for r in rows
        }

    # ------------------------------------------------------------------ orders

    async def get_order(self, order_id: str) -> OrderModel | None:
        return await self.session.get(OrderModel, order_id, populate_existing=True)

    async def get_order_by_paypal_id(self, paypal_order_id: str) -> OrderModel | None:
        res = await self.session.execute(select(OrderModel).where(OrderModel.paypal_order_id == paypal_order_id))
        return res.scalar_one_or_none()

    async def latest_order_for_caller(self, caller_hash: str, shop_id: str) -> OrderModel | None:
        res = await self.session.execute(
            select(OrderModel)
            .where(OrderModel.caller_hash == caller_hash, OrderModel.shop_id == shop_id)
            .order_by(OrderModel.created_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def create_order(
        self, *, order_id: str, shop_id: str, call_id: str | None, caller_hash: str, currency: str
    ) -> OrderModel:
        order = OrderModel(
            id=order_id,
            shop_id=shop_id,
            call_id=call_id,
            caller_hash=caller_hash,
            currency=currency,
            state=OrderState.DRAFT.value,
            version=1,
        )
        self.session.add(order)
        await self.session.flush()
        await self.append_event(order_id, "ORDER_CREATED", {"currency": currency}, ActorType.AGENT)
        return order

    async def apply_quote(self, order: OrderModel, quote: Quote) -> OrderModel:
        """Replace lines and totals. Only legal before a payment link exists."""
        state = OrderState(order.state)
        if state not in (OrderState.DRAFT, OrderState.QUOTED):
            raise RepositoryError(f"Cannot re-quote an order in state {state.value}")
        lines = await order.awaitable_attrs.lines
        lines.clear()
        await self.session.flush()
        for line in quote.lines:
            lines.append(
                OrderLineModel(
                    item_id=line.item_id,
                    name=line.name,
                    qty=line.qty,
                    unit_total_cents=line.unit_total_cents,
                    line_total_cents=line.line_total_cents,
                    modifiers_json=json.dumps(
                        [
                            {"modifier_id": m.modifier_id, "name": m.name, "price_delta_cents": m.price_delta_cents}
                            for m in line.modifiers
                        ]
                    ),
                )
            )
        order.subtotal_cents = quote.subtotal_cents
        order.tax_cents = quote.tax_cents
        order.total_cents = quote.total_cents
        order.quote_hash = quote.quote_hash
        await self.session.flush()
        if state == OrderState.DRAFT:
            return await self.transition(order.id, order.version, OrderAction.QUOTE, ActorType.AGENT,
                                         {"total_cents": quote.total_cents})
        await self.append_event(order.id, "ORDER_REQUOTED", {"total_cents": quote.total_cents}, ActorType.AGENT)
        return order

    async def transition(
        self,
        order_id: str,
        expected_version: int,
        action: OrderAction,
        actor: ActorType,
        payload: dict[str, Any] | None = None,
        *,
        actor_id: str | None = None,
        **fields: Any,
    ) -> OrderModel:
        """UPDATE ... WHERE id=:id AND version=:expected. Exactly one concurrent writer wins."""
        order = await self.get_order(order_id)
        if order is None:
            raise RepositoryError(f"Order {order_id} not found")
        current = OrderState(order.state)
        next_state = get_next_state(current, action)

        values: dict[str, Any] = {"state": next_state.value, "version": expected_version + 1, "updated_at": utc_now()}
        values.update(fields)
        result = await self.session.execute(
            update(OrderModel)
            .where(OrderModel.id == order_id, OrderModel.version == expected_version)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            fresh = await self.get_order(order_id)
            raise OptimisticLockConflictError(order_id, expected_version, fresh.version if fresh else None)

        await self.append_event(
            order_id,
            f"ORDER_{action.value.upper()}",
            {"from": current.value, "to": next_state.value, "side_effect": get_side_effects(action), **(payload or {})},
            actor,
            actor_id=actor_id,
            state=next_state,
        )
        refreshed = await self.get_order(order_id)
        assert refreshed is not None
        return refreshed

    async def append_event(
        self,
        order_id: str,
        event_type: str,
        payload: dict[str, Any],
        actor: ActorType,
        *,
        actor_id: str | None = None,
        state: OrderState | None = None,
    ) -> None:
        seq = (
            await self.session.scalar(
                select(func.coalesce(func.max(OrderEventModel.seq), 0)).where(OrderEventModel.order_id == order_id)
            )
            or 0
        ) + 1
        event_id = generate_uuid()
        if state is None:
            order = await self.session.get(OrderModel, order_id)
            state = OrderState(order.state) if order else None
        body = {"order_id": order_id, "state": state.value if state else None, **payload}
        self.session.add(
            OrderEventModel(
                id=event_id,
                order_id=order_id,
                seq=seq,
                type=event_type,
                payload_json=json.dumps(body, default=str),
                actor=actor.value,
                actor_id=actor_id,
            )
        )
        self.session.add(
            OutboxModel(
                event_id=event_id,
                order_id=order_id,
                seq=seq,
                event_type=event_type,
                payload_json=json.dumps(body, default=str),
            )
        )
        await self.session.flush()

    # ------------------------------------------------------------------ pickup slots

    async def next_slot_with_capacity(self, shop_id: str, not_before: datetime) -> PickupSlotModel | None:
        res = await self.session.execute(
            select(PickupSlotModel)
            .where(
                PickupSlotModel.shop_id == shop_id,
                PickupSlotModel.starts_at >= not_before,
                PickupSlotModel.booked < PickupSlotModel.capacity,
            )
            .order_by(PickupSlotModel.starts_at.asc())
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def book_slot(self, slot_id: str) -> bool:
        """Conditional increment: correct under concurrency on Postgres without explicit locks."""
        res = await self.session.execute(
            update(PickupSlotModel)
            .where(PickupSlotModel.id == slot_id, PickupSlotModel.booked < PickupSlotModel.capacity)
            .values(booked=PickupSlotModel.booked + 1)
            .execution_options(synchronize_session=False)
        )
        return bool(res.rowcount)  # type: ignore[attr-defined]

    async def release_slot(self, slot_id: str | None) -> None:
        if not slot_id:
            return
        await self.session.execute(
            update(PickupSlotModel)
            .where(PickupSlotModel.id == slot_id, PickupSlotModel.booked > 0)
            .values(booked=PickupSlotModel.booked - 1)
            .execution_options(synchronize_session=False)
        )

    # ------------------------------------------------------------------ ledger

    async def payment_for_order(self, order_id: str) -> PaymentModel | None:
        res = await self.session.execute(
            select(PaymentModel).where(PaymentModel.order_id == order_id).order_by(PaymentModel.created_at.desc())
        )
        return res.scalars().first()

    async def payment_by_capture(self, capture_id: str) -> PaymentModel | None:
        res = await self.session.execute(select(PaymentModel).where(PaymentModel.capture_id == capture_id))
        return res.scalar_one_or_none()

    async def upsert_payment(
        self,
        *,
        order_id: str,
        paypal_order_id: str,
        capture_id: str,
        status: str,
        amount_cents: int,
        currency: str,
        fee_cents: int | None,
        source: str,
        mandate_id: str | None = None,
    ) -> PaymentModel:
        existing = await self.payment_by_capture(capture_id)
        if existing:
            existing.status = status
            if fee_cents is not None:
                existing.fee_cents = fee_cents
            await self.session.flush()
            return existing
        payment = PaymentModel(
            order_id=order_id,
            paypal_order_id=paypal_order_id,
            capture_id=capture_id,
            status=status,
            amount_cents=amount_cents,
            currency=currency,
            fee_cents=fee_cents,
            source=source,
            mandate_id=mandate_id,
        )
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def refunded_total(self, capture_id: str) -> int:
        """Cumulative refunds PayPal confirmed or is still processing for one capture."""
        total = await self.session.scalar(
            select(func.coalesce(func.sum(RefundModel.amount_cents), 0)).where(
                RefundModel.capture_id == capture_id, RefundModel.status.in_(("COMPLETED", "PENDING"))
            )
        )
        return int(total or 0)

    async def refund_by_paypal_id(self, paypal_refund_id: str) -> RefundModel | None:
        res = await self.session.execute(select(RefundModel).where(RefundModel.paypal_refund_id == paypal_refund_id))
        return res.scalar_one_or_none()

    async def record_refund(self, **kwargs: Any) -> RefundModel:
        refund = RefundModel(**kwargs)
        self.session.add(refund)
        await self.session.flush()
        return refund

    async def create_approval(self, *, order_id: str, amount_cents: int, reason: str) -> ApprovalModel:
        approval = ApprovalModel(order_id=order_id, requested_amount_cents=amount_cents, reason=reason)
        self.session.add(approval)
        await self.session.flush()
        return approval

    # ------------------------------------------------------------------ mandates

    async def active_mandate(self, customer_hash: str, shop_id: str) -> VaultMandateModel | None:
        res = await self.session.execute(
            select(VaultMandateModel)
            .where(
                VaultMandateModel.customer_hash == customer_hash,
                VaultMandateModel.shop_id == shop_id,
                VaultMandateModel.status == "ACTIVE",
            )
            .order_by(VaultMandateModel.created_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def create_mandate(
        self,
        *,
        vault_id: str,
        customer_hash: str,
        shop_id: str,
        max_charge_cents: int,
        source_order_id: str | None = None,
    ) -> VaultMandateModel:
        existing = await self.session.execute(
            select(VaultMandateModel).where(VaultMandateModel.vault_id == vault_id, VaultMandateModel.status == "ACTIVE")
        )
        if (found := existing.scalar_one_or_none()) is not None:
            return found
        mandate = VaultMandateModel(
            vault_id=vault_id,
            customer_hash=customer_hash,
            shop_id=shop_id,
            max_charge_cents=max_charge_cents,
            source_order_id=source_order_id,
        )
        self.session.add(mandate)
        await self.session.flush()
        return mandate

    async def revoke_mandate(self, revoke_token: str) -> bool:
        res = await self.session.execute(
            update(VaultMandateModel)
            .where(VaultMandateModel.revoke_token == revoke_token, VaultMandateModel.status == "ACTIVE")
            .values(status="REVOKED", revoked_at=utc_now())
            .execution_options(synchronize_session=False)
        )
        return bool(res.rowcount)  # type: ignore[attr-defined]

    async def vault_charges_since(self, mandate_id: str, since: datetime) -> int:
        count = await self.session.scalar(
            select(func.count(PaymentModel.id)).where(
                PaymentModel.mandate_id == mandate_id, PaymentModel.created_at >= since
            )
        )
        return int(count or 0)

    @staticmethod
    def to_domain_mandate(m: VaultMandateModel) -> VaultMandate:
        return VaultMandate(
            id=m.id,
            vault_id=m.vault_id,
            customer_hash=m.customer_hash,
            shop_id=m.shop_id,
            max_charge_cents=m.max_charge_cents,
            status=m.status,
        )

    # ------------------------------------------------------------------ consent & step-up

    async def record_consent(
        self,
        *,
        call_id: str,
        order_id: str,
        quote_hash: str,
        amount_cents: int,
        prompt_text: str,
        transcript: str,
        decision: ConsentDecision,
    ) -> ConsentEventModel:
        row = ConsentEventModel(
            call_id=call_id,
            order_id=order_id,
            quote_hash=quote_hash,
            amount_cents=amount_cents,
            prompt_text=prompt_text,
            transcript=transcript,
            decision=decision.value,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def latest_consent(self, order_id: str) -> ConsentRecord | None:
        res = await self.session.execute(
            select(ConsentEventModel)
            .where(ConsentEventModel.order_id == order_id)
            .order_by(ConsentEventModel.created_at.desc())
            .limit(1)
        )
        row = res.scalar_one_or_none()
        if row is None:
            return None
        return ConsentRecord(
            order_id=row.order_id,
            call_id=row.call_id,
            quote_hash=row.quote_hash,
            amount_cents=row.amount_cents,
            decision=ConsentDecision(row.decision),
            recorded_at=row.created_at,
        )

    async def create_challenge(self, *, call_id: str, mandate_id: str, code_hash: str, ttl_seconds: int) -> None:
        self.session.add(
            StepUpChallengeModel(
                call_id=call_id,
                mandate_id=mandate_id,
                code_hash=code_hash,
                expires_at=utc_now() + timedelta(seconds=ttl_seconds),
            )
        )
        await self.session.flush()

    async def open_challenge(self, call_id: str, mandate_id: str) -> StepUpChallengeModel | None:
        res = await self.session.execute(
            select(StepUpChallengeModel)
            .where(
                StepUpChallengeModel.call_id == call_id,
                StepUpChallengeModel.mandate_id == mandate_id,
                StepUpChallengeModel.verified_at.is_(None),
            )
            .order_by(StepUpChallengeModel.created_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def verified_step_up(self, call_id: str, mandate_id: str) -> StepUpRecord | None:
        res = await self.session.execute(
            select(StepUpChallengeModel)
            .where(
                StepUpChallengeModel.call_id == call_id,
                StepUpChallengeModel.mandate_id == mandate_id,
                StepUpChallengeModel.verified_at.is_not(None),
            )
            .limit(1)
        )
        row = res.scalar_one_or_none()
        if row is None or row.verified_at is None:
            return None
        return StepUpRecord(call_id=row.call_id, mandate_id=row.mandate_id, verified_at=row.verified_at)

    # ------------------------------------------------------------------ webhooks

    async def insert_webhook_event(
        self,
        *,
        paypal_event_id: str,
        event_type: str,
        raw_payload: str,
        paypal_create_time: datetime | None,
    ) -> bool:
        """True if newly stored, False if PayPal already delivered this event id."""
        try:
            async with self.session.begin_nested():
                self.session.add(
                    WebhookEventModel(
                        paypal_event_id=paypal_event_id,
                        type=event_type,
                        payload_json=raw_payload,
                        paypal_create_time=paypal_create_time,
                    )
                )
        except IntegrityError:
            return False
        return True

    async def claim_webhook_events(self, limit: int = 20) -> Sequence[WebhookEventModel]:
        res = await self.session.execute(
            select(WebhookEventModel)
            .where(WebhookEventModel.status.in_(("RECEIVED", "RETRY")))
            .order_by(WebhookEventModel.received_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return res.scalars().all()
