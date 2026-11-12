"""
Single settings object for every process (API, worker, agent, scripts).

Rules enforced here rather than scattered across call sites:
- PayPal mock mode exists only when PAYPAL_MODE=mock is set explicitly. It is never inferred
  from the shape of a client ID.
- sandbox/live modes refuse to start without real credentials.
- Deployed environments (staging, production) refuse to start in mock mode, with a default
  secret, without merchant credentials, or on SQLite.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Optional

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-only-secret-not-for-deploys"
_PLACEHOLDER_PREFIXES = ("your_", "changeme", "replace_me")


class Environment(str, Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class PayPalMode(str, Enum):
    MOCK = "mock"
    SANDBOX = "sandbox"
    LIVE = "live"


class LLMProvider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"


class ConfigurationError(RuntimeError):
    """Raised at startup when the configuration is unsafe to run with."""


def _missing(value: Optional[str | SecretStr]) -> bool:
    if value is None:
        return True
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    raw = raw.strip()
    return not raw or raw.lower().startswith(_PLACEHOLDER_PREFIXES)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"

    # Storage
    database_url: str = "sqlite+aiosqlite:///./counter.db"
    redis_url: Optional[str] = None
    run_worker_in_process: bool = False

    # Secrets
    secret_key: SecretStr = SecretStr(DEV_SECRET)
    caller_hash_key: SecretStr = SecretStr(DEV_SECRET + ":caller")
    service_token: Optional[SecretStr] = None

    # Public URLs. The browser only ever talks to the web origin; Next.js proxies /api.
    web_base_url: str = "http://localhost:3000"
    api_public_url: str = "http://localhost:8000"

    # Merchant login (single owner for the demo). Hash via infra/scripts/hash_password.py.
    owner_email: Optional[str] = None
    owner_password_hash: Optional[str] = None

    # PayPal. No default for the mode: it must be chosen.
    paypal_mode: PayPalMode
    paypal_client_id: Optional[str] = None
    paypal_client_secret: Optional[SecretStr] = None
    paypal_webhook_id: Optional[str] = None
    paypal_partner_attribution_id: Optional[str] = None
    paypal_sandbox_buyer_email: Optional[str] = None
    paypal_sandbox_buyer_password: Optional[SecretStr] = None

    # Shop defaults
    default_shop_id: str = "shop_counter_demo"
    currency: str = "USD"
    tax_rate_bps: int = 825

    # LLM
    llm_provider: Optional[LLMProvider] = None
    llm_model: Optional[str] = None
    vision_model: Optional[str] = None
    anthropic_api_key: Optional[SecretStr] = None
    openai_api_key: Optional[SecretStr] = None
    openai_base_url: str = "https://api.openai.com/v1"

    # Voice-vault identity controls
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 3
    demo_sms_inbox: bool = False  # shows texted codes in the web demo; refused when deployed live

    # Twilio (phone channel and SMS)
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[SecretStr] = None
    twilio_phone_number: Optional[str] = None
    human_handoff_number: Optional[str] = None

    # Integrations
    zapier_webhook_url: Optional[str] = None
    zapier_signing_secret: Optional[SecretStr] = None
    kernel_api_key: Optional[SecretStr] = None

    # Abuse controls
    rate_limit_per_minute: int = 30

    @property
    def is_deployed(self) -> bool:
        return self.environment in (Environment.STAGING, Environment.PRODUCTION)

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url[len(prefix):]
        return url

    @property
    def cors_origins(self) -> list[str]:
        return [self.web_base_url.rstrip("/")]

    @property
    def twilio_configured(self) -> bool:
        return not (
            _missing(self.twilio_account_sid)
            or _missing(self.twilio_auth_token)
            or _missing(self.twilio_phone_number)
        )

    @model_validator(mode="after")
    def _check_safety(self) -> "Settings":
        problems: list[str] = []

        if self.paypal_mode in (PayPalMode.SANDBOX, PayPalMode.LIVE):
            for name in ("paypal_client_id", "paypal_client_secret", "paypal_webhook_id"):
                if _missing(getattr(self, name)):
                    problems.append(f"{name.upper()} is required when PAYPAL_MODE={self.paypal_mode.value}")

        if self.is_deployed:
            if self.paypal_mode == PayPalMode.MOCK:
                problems.append("PAYPAL_MODE=mock is not allowed in a deployed environment")
            if self.secret_key.get_secret_value() == DEV_SECRET:
                problems.append("SECRET_KEY must be set in a deployed environment")
            if self.caller_hash_key.get_secret_value().startswith(DEV_SECRET):
                problems.append("CALLER_HASH_KEY must be set in a deployed environment")
            if _missing(self.service_token):
                problems.append("SERVICE_TOKEN must be set in a deployed environment")
            if _missing(self.owner_email) or _missing(self.owner_password_hash):
                problems.append("OWNER_EMAIL and OWNER_PASSWORD_HASH must be set in a deployed environment")
            if self.async_database_url.startswith("sqlite"):
                problems.append("SQLite is not allowed in a deployed environment")

        if self.environment == Environment.PRODUCTION and self.demo_sms_inbox:
            problems.append("DEMO_SMS_INBOX cannot be enabled in production")
        if self.paypal_mode == PayPalMode.LIVE and self.demo_sms_inbox:
            problems.append("DEMO_SMS_INBOX cannot be enabled with live PayPal")

        if problems:
            raise ConfigurationError("Refusing to start: " + "; ".join(problems))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment


def reset_settings_cache() -> None:
    get_settings.cache_clear()


__all__ = [
    "ConfigurationError",
    "Environment",
    "LLMProvider",
    "PayPalMode",
    "Settings",
    "get_settings",
    "reset_settings_cache",
]
