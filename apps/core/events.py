"""
Live event delivery from the outbox to SSE clients.

The worker claims outbox rows and publishes them to an EventBus. With REDIS_URL set the bus is
Redis pub/sub, and every API process runs a fan-out task that subscribes once and pushes to its
local SSE connections, so events published by any worker reach clients on any API instance.
Without Redis (tests, single-process dev) an in-process bus does the same job.

Clients resume after a reconnect with Last-Event-ID; the SSE endpoint replays outbox rows with a
higher id before switching to live delivery.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Protocol

logger = logging.getLogger("counter.events")
CHANNEL = "counter:events"


class EventBus(Protocol):
    async def publish(self, event: dict[str, Any]) -> None: ...

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]: ...

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class _LocalFanout:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def deliver(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stuck client loses live events; it will catch up via Last-Event-ID on reconnect.
                self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


class LocalEventBus(_LocalFanout):
    async def publish(self, event: dict[str, Any]) -> None:
        self.deliver(event)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class RedisEventBus(_LocalFanout):
    def __init__(self, redis_url: str):
        super().__init__()
        import redis.asyncio as redis_asyncio

        self._redis = redis_asyncio.from_url(redis_url, decode_responses=True)
        self._task: asyncio.Task[None] | None = None

    async def publish(self, event: dict[str, Any]) -> None:
        await self._redis.publish(CHANNEL, json.dumps(event, default=str))

    async def start(self) -> None:
        """Start the fan-out loop (API processes only; the worker only publishes)."""
        if self._task is None:
            self._task = asyncio.create_task(self._fanout_loop(), name="redis-fanout")

    async def _fanout_loop(self) -> None:
        backoff = 0.5
        while True:
            try:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(CHANNEL)
                backoff = 0.5
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        self.deliver(json.loads(message["data"]))
                    except (ValueError, KeyError):
                        logger.warning("Dropping malformed event from Redis")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # reconnect on any Redis failure
                logger.warning("Redis fan-out disconnected (%s); retrying in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._redis.aclose()
