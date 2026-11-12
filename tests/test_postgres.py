"""Postgres-only tests: slot contention and SKIP LOCKED claiming. Run with TEST_POSTGRES_URL set and -m postgres."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.postgres
PG = os.getenv("TEST_POSTGRES_URL")


@pytest.mark.skipif(not PG, reason="TEST_POSTGRES_URL not set")
async def test_slot_contention_on_postgres() -> None:
    from harness.load_test import run

    result = await run(PG or "", callers=200, capacity=8)
    assert result["errors"] == 0
    assert result["final_booked"] == result["accepted"] == 8


@pytest.mark.skipif(not PG, reason="TEST_POSTGRES_URL not set")
async def test_full_flow_on_postgres(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The link flow end to end on Postgres (row locks, unique constraints, outbox ids)."""
    from apps.core.container import build_container
    from apps.core.seed import ensure_demo_shop
    from apps.worker.worker import CounterWorker
    from tests.conftest import make_settings
    from tests.test_payment_flows import _place_link_order

    c = await build_container(make_settings(tmp_path, database_url=PG), start_event_fanout=False)
    try:
        await ensure_demo_shop(c)
        _, order = await _place_link_order(c)
        c.paypal.approve((await _get(c, order["id"])).paypal_order_id)  # type: ignore[attr-defined]
        result = await c.payments.capture_approved_order(order["id"], trigger="test")
        assert result.state == "CAPTURED"
        assert await CounterWorker(c).publish_outbox() > 0
    finally:
        await c.aclose()


async def _get(c, order_id):  # type: ignore[no-untyped-def]
    from apps.api.db.models import OrderModel

    async with c.session_maker() as s:
        return await s.get(OrderModel, order_id)
