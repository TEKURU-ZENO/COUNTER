"""
Authentication, signed tokens and rate limiting.

- Merchant endpoints: signed, http-only session cookie issued after owner login.
- Agent/telephony-to-API calls: bearer SERVICE_TOKEN.
- Customers: a signed, expiring token in their status link scoped to one order; web demo
  callers get a signed token scoped to one call.
- Public endpoints are rate-limited per client IP (Redis-backed when REDIS_URL is set).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from apps.core.container import Container
from apps.core.settings import Settings

SESSION_COOKIE = "counter_session"
SESSION_MAX_AGE = 12 * 3600
ORDER_TOKEN_MAX_AGE = 2 * 24 * 3600
CALL_TOKEN_MAX_AGE = 2 * 3600


def get_container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


def _serializer(settings: Settings, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key.get_secret_value(), salt=salt)


# ---------------------------------------------------------------------- passwords


def hash_password(password: str, *, iterations: int = 390_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt_b64, digest_b64 = encoded.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iterations))
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


# ---------------------------------------------------------------------- tokens


def issue_session(settings: Settings, email: str) -> str:
    return _serializer(settings, "merchant-session").dumps({"sub": email})


def issue_order_token(settings: Settings, order_id: str) -> str:
    return _serializer(settings, "order-status").dumps(order_id)


def check_order_token(settings: Settings, order_id: str, token: str) -> bool:
    try:
        return bool(_serializer(settings, "order-status").loads(token, max_age=ORDER_TOKEN_MAX_AGE) == order_id)
    except (BadSignature, SignatureExpired):
        return False


def issue_call_token(settings: Settings, call_id: str) -> str:
    return _serializer(settings, "web-call").dumps(call_id)


def check_call_token(settings: Settings, call_id: str, token: Optional[str]) -> bool:
    if not token:
        return False
    try:
        return bool(_serializer(settings, "web-call").loads(token, max_age=CALL_TOKEN_MAX_AGE) == call_id)
    except (BadSignature, SignatureExpired):
        return False


# ---------------------------------------------------------------------- dependencies


async def require_merchant(request: Request, c: Container = Depends(get_container)) -> str:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    try:
        data = _serializer(c.settings, "merchant-session").loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    if data.get("sub") != c.settings.owner_email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    return str(data["sub"])


async def require_service(request: Request, c: Container = Depends(get_container)) -> None:
    expected = c.settings.service_token.get_secret_value() if c.settings.service_token else ""
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Service token required")


# ---------------------------------------------------------------------- rate limiting


class RateLimiter:
    """Fixed-window counter per (bucket, client). Redis keeps it consistent across processes."""

    def __init__(self, redis_url: Optional[str]):
        self._local: dict[tuple[str, str, int], int] = defaultdict(int)
        self._redis = None
        if redis_url:
            import redis.asyncio as redis_asyncio

            self._redis = redis_asyncio.from_url(redis_url)

    async def hit(self, bucket: str, client: str, limit: int, window_s: int = 60) -> bool:
        window = int(time.time() // window_s)
        if self._redis is not None:
            key = f"rl:{bucket}:{client}:{window}"
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, window_s * 2)
            return int(count) <= limit
        k = (bucket, client, window)
        self._local[k] += 1
        if len(self._local) > 50_000:
            self._local = defaultdict(int, {kk: v for kk, v in self._local.items() if kk[2] >= window})
        return self._local[k] <= limit


def rate_limit(bucket: str, per_minute: Optional[int] = None):  # type: ignore[no-untyped-def]
    async def _dep(request: Request, c: Container = Depends(get_container)) -> None:
        limiter: RateLimiter = request.app.state.rate_limiter
        client = request.client.host if request.client else "unknown"
        if not await limiter.hit(bucket, client, per_minute or c.settings.rate_limit_per_minute):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests")

    return _dep
