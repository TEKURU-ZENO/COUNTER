"""
The one factory. Every process builds exactly one Container from Settings; FastAPI routes,
the worker, the agent and scripts receive collaborators from it instead of constructing their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from apps.api.db.session import create_schema, make_engine, make_session_maker
from apps.core.events import EventBus, LocalEventBus, RedisEventBus
from apps.core.payments import PaymentService
from apps.core.settings import PayPalMode, Settings
from apps.core.sms import DemoInboxSms, NullSms, SmsSender, TwilioSms
from packages.paypal_gateway import MockPayPalGateway, PayPalClient, PayPalGateway


def build_paypal_gateway(settings: Settings) -> PayPalGateway:
    if settings.paypal_mode == PayPalMode.MOCK:
        return MockPayPalGateway(
            webhook_secret=settings.secret_key.get_secret_value(),
            approval_base_url=settings.web_base_url,
        )
    assert settings.paypal_client_id and settings.paypal_client_secret and settings.paypal_webhook_id
    return PayPalClient(
        client_id=settings.paypal_client_id,
        client_secret=settings.paypal_client_secret.get_secret_value(),
        webhook_id=settings.paypal_webhook_id,
        mode=settings.paypal_mode.value,
        partner_attribution_id=settings.paypal_partner_attribution_id,
    )


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    session_maker: async_sessionmaker[AsyncSession]
    paypal: PayPalGateway
    events: EventBus
    payments: PaymentService
    sms: SmsSender
    http: httpx.AsyncClient
    llm: Any | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    async def aclose(self) -> None:
        await self.events.stop()
        await self.paypal.aclose()
        await self.http.aclose()
        await self.engine.dispose()


async def build_container(settings: Settings, *, start_event_fanout: bool = True) -> Container:
    engine = make_engine(settings.async_database_url)
    await create_schema(engine)
    session_maker = make_session_maker(engine)
    http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
    paypal = build_paypal_gateway(settings)

    events: EventBus = RedisEventBus(settings.redis_url) if settings.redis_url else LocalEventBus()
    if start_event_fanout:
        await events.start()

    sms: SmsSender
    if settings.twilio_configured:
        assert settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_phone_number
        sms = TwilioSms(settings.twilio_account_sid, settings.twilio_auth_token.get_secret_value(),
                        settings.twilio_phone_number, http)
    elif settings.demo_sms_inbox:
        sms = DemoInboxSms()
    else:
        sms = NullSms()

    from apps.agent.llm import build_llm  # local import: the agent package depends on core

    return Container(
        settings=settings,
        engine=engine,
        session_maker=session_maker,
        paypal=paypal,
        events=events,
        payments=PaymentService(session_maker=session_maker, paypal=paypal, settings=settings),
        sms=sms,
        http=http,
        llm=build_llm(settings, http),
    )
