"""
Background worker: webhook processing, outbox publishing, and sweeps.

Run as its own process (`python -m apps.worker.worker`) in deployments, or inside the API
process in single-process dev/test (RUN_WORKER_IN_PROCESS=true).

Concurrency guarantees, assuming Postgres:
- Webhook events and outbox rows are claimed with FOR UPDATE SKIP LOCKED, so several workers
  can run without double-processing.
- Outbox rows for one order are published in seq order: a worker that claims a row skips it if
  an earlier unpublished row for the same order exists that it did not claim.
- Failures increment attempts; after MAX_ATTEMPTS a row is parked (DEAD / FAILED) and logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from apps.api.db.models import OrderModel, OutboxModel, WebhookEventModel, utc_now
from apps.api.db.repository import OptimisticLockConflictError, OrderRepository
from apps.core.container import Container
from packages.domain.models import ActorType, OrderAction, OrderState

logger = logging.getLogger("counter.worker")
MAX_ATTEMPTS = 8

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


class CounterWorker:
    def __init__(self, container: Container, extra_sinks: list[EventSink] | None = None):
        self.c = container
        self.extra_sinks: list[EventSink] = list(extra_sinks or [])
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ webhooks

    async def process_webhooks(self, limit: int = 20) -> int:
        processed = 0
        async with self.c.session_maker() as session:
            async with session.begin():
                rows = await OrderRepository(session).claim_webhook_events(limit)
                claimed = [(r.paypal_event_id, r.type, r.payload_json) for r in rows]
            for event_id, event_type, payload_json in claimed:
                await self._process_one(event_id, event_type, payload_json)
                processed += 1
        return processed

    async def _process_one(self, event_id: str, event_type: str, payload_json: str) -> None:
        resource = json.loads(payload_json).get("resource", {}) or {}
        try:
            if event_type == "CHECKOUT.ORDER.APPROVED":
                # Recovery trigger: same idempotent capture the return URL uses. PayPal calls happen
                # outside any transaction, so the status update is its own transaction.
                order = await self._order_by_paypal_id(str(resource.get("id", "")))
                outcome = "ignored:unknown_order"
                if order is not None:
                    result = await self.c.payments.capture_approved_order(order.id, trigger="webhook")
                    outcome = f"capture:{result.state or result.reason}"
                await self._mark(event_id, "PROCESSED", outcome)
                return

            async with self.c.session_maker() as session, session.begin():
                row = await session.get(WebhookEventModel, event_id, with_for_update=True)
                if row is None or row.status == "PROCESSED":
                    return
                outcome = await self.c.payments.apply_webhook(session, event_type, resource)
                row.status = "PROCESSED"
                row.processed_at = utc_now()
                row.last_error = outcome
        except OptimisticLockConflictError as exc:
            await self._mark(event_id, "RETRY", f"conflict: {exc}", failed=True)
        except Exception as exc:  # keep the worker alive; the event is retried
            logger.exception("Webhook %s (%s) failed", event_id, event_type)
            await self._mark(event_id, "RETRY", repr(exc)[:500], failed=True)

    async def _order_by_paypal_id(self, paypal_order_id: str) -> OrderModel | None:
        async with self.c.session_maker() as session:
            return await OrderRepository(session).get_order_by_paypal_id(paypal_order_id)

    async def _mark(self, event_id: str, status: str, note: str, failed: bool = False) -> None:
        async with self.c.session_maker() as session, session.begin():
            row = await session.get(WebhookEventModel, event_id)
            if row is None:
                return
            row.attempts += 1 if failed else 0
            row.status = "FAILED" if failed and row.attempts >= MAX_ATTEMPTS else status
            row.last_error = note
            if status == "PROCESSED":
                row.processed_at = utc_now()

    # ------------------------------------------------------------------ outbox

    async def publish_outbox(self, limit: int = 100) -> int:
        published = 0
        async with self.c.session_maker() as session, session.begin():
            rows = (
                await session.execute(
                    select(OutboxModel)
                    .where(OutboxModel.status == "PENDING")
                    .order_by(OutboxModel.id.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            if not rows:
                return 0

            order_ids = {r.order_id for r in rows}
            earliest = dict(
                (
                    await session.execute(
                        select(OutboxModel.order_id, func.min(OutboxModel.seq))
                        .where(OutboxModel.status == "PENDING", OutboxModel.order_id.in_(order_ids))
                        .group_by(OutboxModel.order_id)
                    )
                ).all()
            )
            blocked: set[str] = set()
            for row in sorted(rows, key=lambda r: (r.order_id, r.seq)):
                if row.order_id in blocked:
                    continue
                if row.seq != earliest.get(row.order_id):
                    blocked.add(row.order_id)  # an earlier row is held by another worker
                    continue
                event = {
                    "id": row.id,
                    "event_id": row.event_id,
                    "order_id": row.order_id,
                    "seq": row.seq,
                    "type": row.event_type,
                    "payload": json.loads(row.payload_json),
                    "created_at": row.created_at.isoformat(),
                }
                try:
                    await self.c.events.publish(event)
                except Exception as exc:
                    row.attempts += 1
                    row.last_error = repr(exc)[:500]
                    if row.attempts >= MAX_ATTEMPTS:
                        row.status = "DEAD"
                        logger.error("Outbox row %s parked after %s attempts", row.id, row.attempts)
                    blocked.add(row.order_id)  # keep per-order ordering: stop this order for now
                    continue
                row.status = "PUBLISHED"
                row.published_at = utc_now()
                for sink in self.extra_sinks:  # best-effort notifications must not block live updates
                    try:
                        await sink(event)
                    except Exception as exc:
                        logger.warning("Integration sink failed for outbox row %s: %s", row.id, exc)
                earliest[row.order_id] = row.seq + 1
                published += 1
        return published

    # ------------------------------------------------------------------ sweeps

    async def sweep_expired_links(self) -> int:
        ttl = timedelta(minutes=self.c.payments.guard.policy.link_ttl_min)
        return await self._sweep(OrderState.LINK_SENT, OrderModel.link_sent_at, utc_now() - ttl,
                                 OrderAction.EXPIRE, release_slot=True)

    async def sweep_no_shows(self, grace_minutes: int = 60) -> int:
        return await self._sweep(OrderState.READY, OrderModel.pickup_at,
                                 utc_now() - timedelta(minutes=grace_minutes), OrderAction.SWEEP_NO_SHOW)

    async def _sweep(self, state: OrderState, column: Any, threshold: datetime, action: OrderAction,
                     release_slot: bool = False) -> int:
        swept = 0
        async with self.c.session_maker() as session:
            ids = (
                await session.execute(
                    select(OrderModel.id, OrderModel.version)
                    .where(OrderModel.state == state.value, column.is_not(None), column <= threshold)
                    .limit(200)
                )
            ).all()
        for order_id, version in ids:
            try:
                async with self.c.session_maker() as session, session.begin():
                    repo = OrderRepository(session)
                    order = await repo.transition(order_id, version, action, ActorType.SYSTEM)
                    if release_slot:
                        await repo.release_slot(order.pickup_slot_id)
                swept += 1
            except OptimisticLockConflictError:
                continue  # someone else moved it (e.g. the buyer paid at the last second)
        return swept

    # ------------------------------------------------------------------ loop

    async def run_forever(self, fast_interval: float = 0.5, sweep_interval: float = 30.0) -> None:
        logger.info("Worker started (paypal_mode=%s)", self.c.settings.paypal_mode.value)
        last_sweep = 0.0
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            try:
                await self.process_webhooks()
                await self.publish_outbox()
                if loop.time() - last_sweep >= sweep_interval:
                    await self.sweep_expired_links()
                    await self.sweep_no_shows()
                    last_sweep = loop.time()
            except Exception:
                logger.exception("Worker iteration failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=fast_interval)

    def stop(self) -> None:
        self._stop.set()


async def _main() -> None:
    from apps.core.container import build_container
    from apps.core.integrations import outbox_sinks
    from apps.core.settings import get_settings

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    container = await build_container(settings, start_event_fanout=False)
    worker = CounterWorker(container, extra_sinks=outbox_sinks(container))
    try:
        await worker.run_forever()
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
