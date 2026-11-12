"""FastAPI application: lifespan-managed container, locked-down CORS, routers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api import routes_merchant, routes_public, routes_twilio
from apps.api.security import RateLimiter
from apps.core.container import Container, build_container
from apps.core.integrations import outbox_sinks
from apps.core.seed import ensure_demo_shop
from apps.core.settings import Settings, get_settings
from apps.core.telemetry import setup_tracing


def create_app(settings: Optional[Settings] = None, container: Optional[Container] = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_tracing("counter-api", settings.environment.value)
        c = container or await build_container(settings)
        app.state.container = c
        app.state.rate_limiter = RateLimiter(settings.redis_url)
        await ensure_demo_shop(c)
        worker_task: Optional[asyncio.Task[None]] = None
        worker = None
        if settings.run_worker_in_process:
            from apps.worker.worker import CounterWorker

            worker = CounterWorker(c, extra_sinks=outbox_sinks(c))
            worker_task = asyncio.create_task(worker.run_forever(), name="in-process-worker")
        try:
            yield
        finally:
            if worker is not None and worker_task is not None:
                worker.stop()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker_task
            if container is None:
                await c.aclose()

    app = FastAPI(title="COUNTER API", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type", "X-Call-Token", "Last-Event-ID"],
    )
    app.include_router(routes_public.router)
    app.include_router(routes_merchant.router)
    app.include_router(routes_twilio.router)
    return app


def __getattr__(name: str) -> FastAPI:
    # `uvicorn apps.api.main:app` builds the app lazily so importing this module has no side effects.
    if name == "app":
        return create_app()
    raise AttributeError(name)
