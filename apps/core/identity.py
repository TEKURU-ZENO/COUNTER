"""
Caller identity helpers.

Caller ID is spoofable, so a phone number is only ever an *identifier*, never an authenticator.
It is stored as a keyed HMAC (not a plain hash, which is trivially reversible over the
~10^10 phone-number space). Possession is proven separately with an SMS one-time code.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"[^0-9]", "", phone)
    if not digits:
        raise ValueError("Phone number has no digits")
    if len(digits) == 10:  # assume NANP when no country code was given
        digits = "1" + digits
    return "+" + digits


def caller_hash(phone: str, key: str) -> str:
    return hmac.new(key.encode(), normalize_phone(phone).encode(), hashlib.sha256).hexdigest()


def last4(phone: str) -> str:
    return normalize_phone(phone)[-4:]


def new_otp() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def otp_hash(code: str, key: str, call_id: str) -> str:
    return hmac.new(key.encode(), f"{call_id}:{code}".encode(), hashlib.sha256).hexdigest()


def otp_matches(code: str, expected_hash: str, key: str, call_id: str) -> bool:
    cleaned = re.sub(r"[^0-9]", "", code)
    return hmac.compare_digest(otp_hash(cleaned, key, call_id), expected_hash)
