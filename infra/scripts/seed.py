"""
Seed script for COUNTER.
Pre-populates sample restaurant, verified catalog items, modifiers,
sample historical orders across all FSM states, and pickup slots.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from apps.api.db.models import (
    MenuItemModel,
    ModifierModel,
    OrderEventModel,
    OrderLineModel,
    OrderModel,
    PaymentModel,
    PickupSlotModel,
    Policy,
    Shop,
)
from apps.api.db.session import async_session_maker, init_db
from packages.domain.models import ActorType, OrderState


async def seed():
    print("[*] Initializing database and seeding Counter demo data...")
    await init_db()

    async with async_session_maker() as session:
        # 1. Shop
        shop = await session.get(Shop, "shop_counter_demo")
        if not shop:
            shop = Shop(
                id="shop_counter_demo",
                name="Luigi's Artisan Pizza & Trattoria",
                timezone="America/New_York",
                currency="USD",
                prep_capacity=15,
                demo_flag=True,
            )
            session.add(shop)

        # 2. Policy
        existing_policy = await session.get(Policy, "pol_demo_standard")
        if not existing_policy:
            policy = Policy(
                id="pol_demo_standard",
                cancel_full_refund_until_state=OrderState.CAPTURED.value,
                partial_refund_pct=50,
                link_ttl_min=15,
                max_order_value_cents=50000,
                refund_approval_threshold_cents=2500,
                catering_threshold_cents=20000,
            )
            session.add(policy)

        # 3. Menu items
        menu_items = [
            MenuItemModel(
                id="item_pizza_margherita",
                shop_id="shop_counter_demo",
                name="Margherita Wood-Fired Pizza",
                category="Pizza",
                price_cents=1450,
                prep_min=15,
                available=True,
                dietary_tags_json=json.dumps(["vegetarian"]),
            ),
            MenuItemModel(
                id="item_pizza_pepperoni",
                shop_id="shop_counter_demo",
                name="Spicy Pepperoni Pizza",
                category="Pizza",
                price_cents=1650,
                prep_min=15,
                available=True,
                dietary_tags_json=json.dumps(["pork"]),
            ),
            MenuItemModel(
                id="item_garlic_bread",
                shop_id="shop_counter_demo",
                name="Rosemary Garlic Bread",
                category="Sides",
                price_cents=650,
                prep_min=8,
                available=True,
                dietary_tags_json=json.dumps(["vegetarian"]),
            ),
            MenuItemModel(
                id="item_tiramisu",
                shop_id="shop_counter_demo",
                name="House-made Tiramisu",
                category="Dessert",
                price_cents=750,
                prep_min=5,
                available=True,
                dietary_tags_json=json.dumps(["vegetarian"]),
            ),
        ]
        for it in menu_items:
            existing = await session.get(MenuItemModel, it.id)
            if not existing:
                session.add(it)

        # 4. Modifiers
        mods = [
            ModifierModel(
                id="mod_demo_extra_cheese",
                item_id="item_pizza_margherita",
                name="Extra Buffalo Mozzarella",
                price_delta_cents=250,
                group_name="Cheese",
            ),
            ModifierModel(
                id="mod_demo_olives",
                item_id="item_pizza_margherita",
                name="Kalamata Olives",
                price_delta_cents=150,
                group_name="Toppings",
            ),
            ModifierModel(
                id="mod_demo_hot_honey",
                item_id="item_pizza_pepperoni",
                name="Hot Honey Drizzle",
                price_delta_cents=100,
                group_name="Finishing",
            ),
        ]
        for m in mods:
            existing_m = await session.get(ModifierModel, m.id)
            if not existing_m:
                session.add(m)

        # 5. Pickup Slots
        now = datetime.utcnow()
        slots = [
            PickupSlotModel(
                shop_id="shop_counter_demo",
                starts_at=now + timedelta(minutes=15 * i),
                capacity=8,
                booked=2 if i == 0 else 0,
            )
            for i in range(1, 8)
        ]
        session.add_all(slots)

        await session.commit()
    print("[+] Database seeding complete! Ready for demo calls and testing.")


if __name__ == "__main__":
    asyncio.run(seed())
