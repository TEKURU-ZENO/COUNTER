"""
Repository layer for COUNTER.
Implements optimistic locking on order transitions, transactional outbox writes,
and webhook exact-once deduplication.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models import (
    ApprovalModel,
    MenuItemModel,
    OrderEventModel,
    OrderLineModel,
    OrderModel,
    OutboxModel,
    PaymentModel,
    PickupSlotModel,
    Policy,
    RefundModel,
    Shop,
    WebhookEventModel,
)
from packages.domain.fsm import get_next_state, get_side_effects
from packages.domain.models import ActorType, OrderAction, OrderState


class RepositoryError(Exception):
    pass


class OptimisticLockConflictError(RepositoryError):
    """Raised when an order transition fails due to concurrent modification."""

    def __init__(self, order_id: str, expected_version: int, current_version: Optional[int] = None):
        self.order_id = order_id
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__(
            f"Optimistic lock conflict for order {order_id}: expected version {expected_version}, but order was modified concurrently."
        )


class OrderRepository:
    """Async repository for order persistence and guarded state transitions."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_order_by_id(self, order_id: str) -> Optional[OrderModel]:
        stmt = select(OrderModel).where(OrderModel.id == order_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_order_by_paypal_id(self, paypal_order_id: str) -> Optional[OrderModel]:
        stmt = select(OrderModel).where(OrderModel.paypal_order_id == paypal_order_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def create_order(
        self,
        order_id: str,
        shop_id: str,
        caller_hash: str,
        subtotal_cents: int,
        tax_cents: int,
        total_cents: int,
        quote_hash: Optional[str] = None,
        lines: Optional[List[Dict[str, Any]]] = None,
    ) -> OrderModel:
        """Create a new order in DRAFT state and record creation event in outbox."""
        order = OrderModel(
            id=order_id,
            shop_id=shop_id,
            caller_hash=caller_hash,
            state=OrderState.DRAFT.value,
            version=1,
            subtotal_cents=subtotal_cents,
            tax_cents=tax_cents,
            total_cents=total_cents,
            quote_hash=quote_hash,
        )
        self.session.add(order)

        if lines:
            for line in lines:
                order_line = OrderLineModel(
                    order_id=order_id,
                    item_id=line["item_id"],
                    name=line["name"],
                    qty=line.get("qty", 1),
                    modifiers_json=json.dumps(line.get("modifiers", [])),
                    line_total_cents=line.get("line_total_cents", 0),
                )
                self.session.add(order_line)

        # Initial event & outbox
        event_id = f"evt_{order_id}_create"
        event = OrderEventModel(
            id=event_id,
            order_id=order_id,
            seq=1,
            type="ORDER_CREATED",
            payload_json=json.dumps({"total_cents": total_cents}),
            actor=ActorType.AGENT.value,
        )
        self.session.add(event)

        outbox = OutboxModel(
            event_id=event_id,
            order_id=order_id,
            event_type="ORDER_CREATED",
            payload_json=json.dumps({"total_cents": total_cents}),
        )
        self.session.add(outbox)

        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def transition_order_with_lock(
        self,
        order_id: str,
        expected_version: int,
        action: OrderAction,
        actor: ActorType = ActorType.SYSTEM,
        payload: Optional[Dict[str, Any]] = None,
        paypal_order_id: Optional[str] = None,
    ) -> OrderModel:
        """
        Executes a deterministic state transition using optimistic locking:
        UPDATE orders SET state=:to_state, version=version+1
        WHERE id=:order_id AND version=:expected_version

        Guarantees that race conditions between webhooks and cancellations
        result in exactly one winner and one conflict error.
        """
        payload = payload or {}
        # Fetch current order to validate FSM
        order = await self.get_order_by_id(order_id)
        if not order:
            raise RepositoryError(f"Order {order_id} not found")

        if order.version != expected_version:
            raise OptimisticLockConflictError(order_id, expected_version, order.version)

        current_state = OrderState(order.state)
        next_state = get_next_state(current_state, action)
        side_effect = get_side_effects(action, current_state)

        # Build update values
        update_vals: Dict[str, Any] = {
            "state": next_state.value,
            "version": order.version + 1,
            "updated_at": datetime.utcnow(),
        }
        if paypal_order_id:
            update_vals["paypal_order_id"] = paypal_order_id

        # Optimistic locking atomic query
        stmt = (
            update(OrderModel)
            .where(OrderModel.id == order_id)
            .where(OrderModel.version == expected_version)
            .values(**update_vals)
        )
        result = await self.session.execute(stmt)

        if result.rowcount == 0:
            # Re-fetch for conflict diagnostics
            await self.session.rollback()
            current = await self.get_order_by_id(order_id)
            curr_ver = current.version if current else None
            raise OptimisticLockConflictError(order_id, expected_version, curr_ver)

        # Append to order_events (immutable log)
        event_seq = (
            await self.session.scalar(
                select(OrderEventModel.seq)
                .where(OrderEventModel.order_id == order_id)
                .order_by(OrderEventModel.seq.desc())
                .limit(1)
            )
            or 0
        ) + 1

        event_id = f"evt_{order_id}_{event_seq}"
        event = OrderEventModel(
            id=event_id,
            order_id=order_id,
            seq=event_seq,
            type=f"ORDER_{action.value.upper()}",
            payload_json=json.dumps({"from": current_state.value, "to": next_state.value, "side_effect": side_effect, **payload}),
            actor=actor.value,
        )
        self.session.add(event)

        # Append to transactional outbox
        outbox = OutboxModel(
            event_id=event_id,
            order_id=order_id,
            event_type=f"ORDER_{action.value.upper()}",
            payload_json=json.dumps({"order_id": order_id, "state": next_state.value, **payload}),
        )
        self.session.add(outbox)

        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def record_webhook_if_unique(
        self,
        paypal_event_id: str,
        event_type: str,
        payload: Dict[str, Any],
        verified: bool = True,
    ) -> Optional[WebhookEventModel]:
        """
        Record a PayPal webhook event with exact-once semantics.
        If paypal_event_id already exists, returns None (duplicate no-op).
        """
        existing = await self.session.get(WebhookEventModel, paypal_event_id)
        if existing:
            return None  # Duplicate delivery; idempotent no-op

        webhook_event = WebhookEventModel(
            paypal_event_id=paypal_event_id,
            type=event_type,
            verified=verified,
            payload_json=json.dumps(payload),
            processed_at=datetime.utcnow(),
        )
        self.session.add(webhook_event)
        try:
            await self.session.commit()
            return webhook_event
        except Exception:
            await self.session.rollback()
            return None

    async def record_payment(
        self,
        order_id: str,
        paypal_order_id: str,
        capture_id: str,
        amount_cents: int,
        fee_cents: int = 0,
        status: str = "COMPLETED",
    ) -> PaymentModel:
        """Record completed payment capture in ledger."""
        payment = PaymentModel(
            order_id=order_id,
            paypal_order_id=paypal_order_id,
            capture_id=capture_id,
            amount_cents=amount_cents,
            fee_cents=fee_cents,
            status=status,
        )
        self.session.add(payment)
        await self.session.commit()
        return payment

    async def record_refund(
        self,
        order_id: str,
        capture_id: str,
        paypal_refund_id: str,
        amount_cents: int,
        reason: str,
        status: str = "COMPLETED",
        approved_by: Optional[str] = None,
    ) -> RefundModel:
        """Record refund in ledger."""
        refund = RefundModel(
            order_id=order_id,
            capture_id=capture_id,
            paypal_refund_id=paypal_refund_id,
            amount_cents=amount_cents,
            reason=reason,
            status=status,
            approved_by=approved_by,
        )
        self.session.add(refund)
        await self.session.commit()
        return refund
