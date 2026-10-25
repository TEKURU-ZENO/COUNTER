"""
Concurrent Load Test Suite for COUNTER.
Simulates 20 simultaneous telephone callers contending for identical pickup slots.
Verifies that atomic database capacity limits guarantee ZERO slot overbooking.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List
from sqlalchemy import select, update

from apps.api.db.models import OrderModel, PickupSlotModel
from apps.api.db.session import async_session_maker


async def run_slot_concurrency_load_test(num_callers: int = 20, slot_capacity: int = 5) -> Dict[str, Any]:
    """
    Spawns num_callers concurrent tasks competing for a slot of size slot_capacity.
    Ensures booked <= slot_capacity and overbooked == 0.
    """
    slot_id = f"slot_load_{int(asyncio.get_event_loop().time() * 1000)}"
    async with async_session_maker() as session:
        slot = PickupSlotModel(
            id=slot_id,
            shop_id="shop_counter_demo",
            starts_at=datetime.utcnow() + timedelta(hours=1),
            capacity=slot_capacity,
            booked=0,
        )
        session.add(slot)
        await session.commit()

    async def attempt_booking(caller_idx: int) -> bool:
        async with async_session_maker() as session:
            # Atomic booking query: UPDATE ... WHERE booked < capacity
            stmt = (
                update(PickupSlotModel)
                .where(PickupSlotModel.id == slot_id)
                .where(PickupSlotModel.booked < PickupSlotModel.capacity)
                .values(booked=PickupSlotModel.booked + 1)
            )
            res = await session.execute(stmt)
            await session.commit()
            return res.rowcount > 0

    tasks = [attempt_booking(i) for i in range(num_callers)]
    results = await asyncio.gather(*tasks)

    successful_bookings = sum(1 for r in results if r)
    rejected_bookings = sum(1 for r in results if not r)

    async with async_session_maker() as session:
        final_slot = await session.get(PickupSlotModel, slot_id)
        final_booked = final_slot.booked if final_slot else 0

    return {
        "num_callers": num_callers,
        "slot_capacity": slot_capacity,
        "successful_bookings": successful_bookings,
        "rejected_bookings": rejected_bookings,
        "final_booked": final_booked,
        "overbooking_count": max(0, final_booked - slot_capacity),
        "is_safe": final_booked <= slot_capacity and successful_bookings == slot_capacity,
    }
