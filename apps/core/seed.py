"""Demo shop, menu and pickup slots. Idempotent; also keeps the next 12 hours of slots available."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import func, select

from apps.api.db.models import MenuItemModel, ModifierModel, PickupSlotModel, Shop, utc_now
from apps.core.container import Container

DEMO_MENU = [
    ("item_pizza_margherita", "Margherita Wood-Fired Pizza", "Pizza", 1450, ["vegetarian"]),
    ("item_pizza_pepperoni", "Spicy Pepperoni Pizza", "Pizza", 1650, []),
    ("item_garlic_bread", "Rosemary Garlic Bread", "Sides", 650, ["vegetarian"]),
    ("item_tiramisu", "House-made Tiramisu", "Dessert", 750, ["vegetarian"]),
]
DEMO_MODIFIERS = [
    ("mod_extra_mozzarella", "item_pizza_margherita", "Extra Buffalo Mozzarella", 250, "Cheese"),
    ("mod_olives", "item_pizza_margherita", "Kalamata Olives", 150, "Toppings"),
    ("mod_hot_honey", "item_pizza_pepperoni", "Hot Honey Drizzle", 100, "Finishing"),
]
SLOT_MINUTES = 15
SLOT_HOURS_AHEAD = 12


async def ensure_demo_shop(c: Container, *, capacity: int = 8) -> None:
    shop_id = c.settings.default_shop_id
    async with c.session_maker() as session, session.begin():
        if await session.get(Shop, shop_id) is None:
            session.add(Shop(id=shop_id, name="Luigi's Artisan Pizza & Trattoria", currency=c.settings.currency,
                             prep_capacity=capacity, demo_flag=True))
            for item_id, name, category, price, tags in DEMO_MENU:
                session.add(MenuItemModel(id=item_id, shop_id=shop_id, name=name, category=category,
                                          price_cents=price, dietary_tags_json=json.dumps(tags)))
            for mod_id, item_id, name, delta, group in DEMO_MODIFIERS:
                session.add(ModifierModel(id=mod_id, item_id=item_id, name=name, price_delta_cents=delta,
                                          group_name=group))
    await ensure_slots(c, capacity=capacity)


async def ensure_slots(c: Container, *, capacity: int = 8) -> int:
    shop_id = c.settings.default_shop_id
    now = utc_now().replace(second=0, microsecond=0)
    start = now - timedelta(minutes=now.minute % SLOT_MINUTES)
    created = 0
    async with c.session_maker() as session, session.begin():
        latest = await session.scalar(select(func.max(PickupSlotModel.starts_at)).where(PickupSlotModel.shop_id == shop_id))
        cursor = max(start, latest + timedelta(minutes=SLOT_MINUTES)) if latest else start
        horizon = start + timedelta(hours=SLOT_HOURS_AHEAD)
        while cursor <= horizon:
            session.add(PickupSlotModel(shop_id=shop_id, starts_at=cursor, capacity=capacity, booked=0))
            cursor += timedelta(minutes=SLOT_MINUTES)
            created += 1
    return created
