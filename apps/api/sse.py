"""
Server-sent events with heartbeats and Last-Event-ID resume.

The SSE `id` of each event is the outbox row id (a global, monotonically increasing sequence).
On reconnect the browser sends Last-Event-ID; we replay published outbox rows after it, then
continue with live events from the bus. A comment line is sent every 15 s so proxies keep the
connection open.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Callable, Optional

from sqlalchemy import select

from apps.api.db.models import OutboxModel
from apps.core.container import Container

HEARTBEAT_SECONDS = 15.0
REPLAY_LIMIT = 500


def _format(event: dict[str, Any]) -> str:
    return f"id: {event['id']}\nevent: order\ndata: {json.dumps(event, default=str)}\n\n"


async def event_stream(
    c: Container,
    *,
    last_event_id: Optional[str],
    accept: Callable[[dict[str, Any]], bool],
    heartbeat: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    queue = c.events.subscribe()  # subscribe before replay so nothing falls in the gap
    try:
        yield "retry: 3000\n\n"
        last_id = int(last_event_id) if last_event_id and last_event_id.isdigit() else None
        if last_id is not None:
            async with c.session_maker() as session:
                rows = (
                    await session.execute(
                        select(OutboxModel)
                        .where(OutboxModel.id > last_id, OutboxModel.status == "PUBLISHED")
                        .order_by(OutboxModel.id.asc())
                        .limit(REPLAY_LIMIT)
                    )
                ).scalars().all()
            for row in rows:
                event = {"id": row.id, "event_id": row.event_id, "order_id": row.order_id, "seq": row.seq,
                         "type": row.event_type, "payload": json.loads(row.payload_json),
                         "created_at": row.created_at.isoformat()}
                if accept(event):
                    last_id = row.id
                    yield _format(event)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if last_id is not None and int(event["id"]) <= last_id:
                continue  # already replayed
            if accept(event):
                yield _format(event)
    finally:
        c.events.unsubscribe(queue)
