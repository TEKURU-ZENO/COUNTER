"""
Test fixtures. Every test gets its own SQLite file and its own Container in explicit
PAYPAL_MODE=mock. Sandbox tests live in tests/sandbox and are opt-in (pytest -m sandbox).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

os.environ.setdefault("PAYPAL_MODE", "mock")
os.environ.setdefault("ENVIRONMENT", "test")

from apps.agent.llm import ScriptedLLM  # noqa: E402
from apps.agent.session import CallSession  # noqa: E402
from apps.api.security import hash_password  # noqa: E402
from apps.core.container import Container, build_container  # noqa: E402
from apps.core.seed import ensure_demo_shop  # noqa: E402
from apps.core.settings import Settings  # noqa: E402

OWNER_EMAIL = "owner@example.test"
OWNER_PASSWORD = "correct horse battery staple"
_OWNER_HASH = hash_password(OWNER_PASSWORD, iterations=1_000)


def make_settings(tmp_path: Any, **overrides: Any) -> Settings:
    values: dict[str, Any] = dict(
        _env_file=None,
        environment="test",
        paypal_mode="mock",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        redis_url=None,
        web_base_url="http://web.test",
        api_public_url="http://api.test",
        owner_email=OWNER_EMAIL,
        owner_password_hash=_OWNER_HASH,
        service_token="svc-token-for-tests",
        demo_sms_inbox=True,
        llm_provider=None,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    return make_settings(tmp_path)


@pytest_asyncio.fixture
async def container(settings: Settings) -> AsyncIterator[Container]:
    c = await build_container(settings, start_event_fanout=False)
    await ensure_demo_shop(c)
    yield c
    await c.aclose()


@pytest_asyncio.fixture
async def client(container: Container) -> AsyncIterator[httpx.AsyncClient]:
    from apps.api.main import create_app

    app = create_app(container.settings, container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as ac:
            yield ac


async def login(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/v1/merchant/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert resp.status_code == 200, resp.text


async def start_call(container: Container, script: list[Any], phone: str = "+1 415 555 0100",
                     channel: str = "web") -> tuple[CallSession, ScriptedLLM]:
    llm = ScriptedLLM(script)
    session, _ = await CallSession.start(container, caller_phone=phone, channel=channel, llm=llm)
    return session, llm


def last_tool_result(llm: ScriptedLLM, name: str | None = None) -> dict[str, Any]:
    import json

    for msg in reversed(llm.requests[-1]["messages"]):
        if msg["role"] == "tool" and (name is None or msg["name"] == name):
            return json.loads(msg["content"])  # type: ignore[no-any-return]
    raise AssertionError("no tool result found")
