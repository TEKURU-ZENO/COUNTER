"""
Menu extraction from a photo with a vision model, followed by deterministic validation.

The model must answer through a forced `submit_menu` tool call, giving each field with a
confidence in [0, 1]. Code then converts prices to integer cents, flags implausible prices,
low confidence and duplicate names. The result is stored as a draft; nothing reaches the live
menu until the owner reviews, edits and confirms it (POST .../publish with confirmed=true).
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rapidfuzz import fuzz

from apps.api.db.models import MenuDraftModel, MenuItemModel, utc_now
from apps.core.container import Container
from apps.core.settings import LLMProvider
from packages.domain.money import MoneyError, value_to_cents

MIN_PRICE_CENTS = 50
MAX_PRICE_CENTS = 20_000
LOW_CONFIDENCE = 0.8
DUPLICATE_SIMILARITY = 92


class ExtractionUnavailable(RuntimeError):
    pass


class _Field(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str
    confidence: float = Field(ge=0, le=1)


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: _Field
    price: _Field = Field(description="Price exactly as printed, e.g. '14.50'")
    category: _Field


class _Menu(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[_Item] = Field(max_length=200)


TOOL = {
    "name": "submit_menu",
    "description": "Submit every menu item visible in the photo.",
    "input_schema": {k: v for k, v in _Menu.model_json_schema().items() if k != "title"},
}
PROMPT = ("Read this restaurant menu photo. Report every item with its name, its price exactly as printed "
          "(digits and decimal point only), and its section/category. For each field give your confidence "
          "from 0 to 1 that you read it correctly. Do not invent items or prices you cannot see.")


def _price_cents(raw: str) -> int | None:
    cleaned = re.sub(r"[^0-9.]", "", raw)
    try:
        return value_to_cents(cleaned)
    except MoneyError:
        return None


def validate_items(items: list[_Item]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        flags: list[str] = []
        cents = _price_cents(item.price.value)
        if cents is None:
            flags.append("price_unreadable")
        elif not MIN_PRICE_CENTS <= cents <= MAX_PRICE_CENTS:
            flags.append("price_out_of_range")
        for field_name in ("name", "price", "category"):
            if getattr(item, field_name).confidence < LOW_CONFIDENCE:
                flags.append(f"low_confidence_{field_name}")
        out.append({
            "name": item.name.value.strip(), "category": item.category.value.strip() or "Menu",
            "price_cents": cents, "price_as_printed": item.price.value,
            "confidence": {"name": item.name.confidence, "price": item.price.confidence,
                           "category": item.category.confidence},
            "flags": flags,
        })
    for i, a in enumerate(out):
        for b in out[i + 1:]:
            if fuzz.token_sort_ratio(a["name"].lower(), b["name"].lower()) >= DUPLICATE_SIMILARITY:
                a["flags"].append("possible_duplicate")
                b["flags"].append("possible_duplicate")
    return out


async def extract_menu(c: Container, image: bytes, media_type: str) -> dict[str, Any]:
    s = c.settings
    if s.llm_provider != LLMProvider.ANTHROPIC or not s.anthropic_api_key or not (s.vision_model or s.llm_model):
        raise ExtractionUnavailable("Menu extraction needs LLM_PROVIDER=anthropic with an API key and a vision-capable model")
    from anthropic import AsyncAnthropic

    model = s.vision_model or s.llm_model
    client = AsyncAnthropic(api_key=s.anthropic_api_key.get_secret_value())
    msg = await client.messages.create(
        model=model, max_tokens=4000, tools=[TOOL], tool_choice={"type": "tool", "name": "submit_menu"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                          "data": base64.b64encode(image).decode()}},
            {"type": "text", "text": PROMPT}]}],
    )
    payload = next((b.input for b in msg.content if b.type == "tool_use"), None)
    try:
        parsed = _Menu.model_validate(payload)
    except ValidationError as exc:
        raise ExtractionUnavailable(f"The model's output did not match the menu schema: {exc.error_count()} errors") from None
    items = validate_items(parsed.items)
    draft_id = f"draft_{uuid.uuid4().hex[:12]}"
    async with c.session_maker() as session, session.begin():
        session.add(MenuDraftModel(id=draft_id, shop_id=s.default_shop_id, items_json=json.dumps(items), model=msg.model))
    return {"draft_id": draft_id, "model": msg.model, "items": items,
            "needs_review": sum(1 for i in items if i["flags"])}


async def publish_draft(c: Container, draft_id: str, items: list[dict[str, Any]], owner: str) -> int:
    """Owner-confirmed items replace availability: new items are added, existing names updated."""
    shop_id = c.settings.default_shop_id
    async with c.session_maker() as session, session.begin():
        draft = await session.get(MenuDraftModel, draft_id, with_for_update=True)
        if draft is None or draft.status != "DRAFT":
            return 0
        from sqlalchemy import select

        existing = {m.name.lower(): m for m in (await session.execute(
            select(MenuItemModel).where(MenuItemModel.shop_id == shop_id))).scalars()}
        for item in items:
            row = existing.get(item["name"].lower())
            if row is None:
                session.add(MenuItemModel(id=f"item_{uuid.uuid4().hex[:10]}", shop_id=shop_id, name=item["name"],
                                          category=item["category"], price_cents=item["price_cents"]))
            else:
                row.category, row.price_cents, row.available = item["category"], item["price_cents"], True
        draft.status = "PUBLISHED"
        draft.published_by = owner
        draft.published_at = utc_now()
        draft.items_json = json.dumps(items)
    return len(items)
