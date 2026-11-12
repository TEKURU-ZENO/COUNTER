"""
Pickup-slot contention test against a real database.

N concurrent callers try to book one slot of capacity K, each in its own connection and
transaction, using the same conditional UPDATE the app uses. Run it against Postgres:

    python -m harness.load_test --database-url postgresql://user:pass@localhost:5432/counter_test

CI runs this against a Postgres service container (see .github/workflows/ci.yml).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta

from apps.api.db.models import PickupSlotModel, utc_now
from apps.api.db.repository import OrderRepository
from apps.api.db.session import create_schema, make_engine, make_session_maker
from apps.core.settings import Settings


async def run(database_url: str, callers: int, capacity: int) -> dict[str, int]:
    url = Settings(paypal_mode="mock", database_url=database_url, _env_file=None).async_database_url  # type: ignore[call-arg]
    engine = make_engine(url)
    await create_schema(engine)
    sm = make_session_maker(engine)
    async with sm() as s, s.begin():
        slot = PickupSlotModel(shop_id="load_test", starts_at=utc_now() + timedelta(days=30), capacity=capacity)
        s.add(slot)
    slot_id = slot.id
    start = asyncio.Event()

    async def caller() -> bool:
        await start.wait()
        async with sm() as s, s.begin():
            return await OrderRepository(s).book_slot(slot_id)

    tasks = [asyncio.create_task(caller()) for _ in range(callers)]
    start.set()
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    async with sm() as s:
        booked = (await s.get(PickupSlotModel, slot_id)).booked  # type: ignore[union-attr]
    await engine.dispose()
    return {
        "callers": callers,
        "capacity": capacity,
        "accepted": sum(1 for o in outcomes if o is True),
        "rejected": sum(1 for o in outcomes if o is False),
        "errors": sum(1 for o in outcomes if isinstance(o, BaseException)),
        "final_booked": booked,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", required=True)
    ap.add_argument("--callers", type=int, default=200)
    ap.add_argument("--capacity", type=int, default=8)
    a = ap.parse_args()
    result = asyncio.run(run(a.database_url, a.callers, a.capacity))
    print(result)
    ok = result["final_booked"] == result["accepted"] <= result["capacity"] and result["errors"] == 0
    print("OK: no overbooking" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
