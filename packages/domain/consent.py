"""
Spoken consent for charging a saved PayPal wallet.

The agent reads one exact sentence that contains the amount and a short code derived from the
quote fingerprint. The caller's reply is classified deterministically into YES / NO / UNCLEAR.
The classifier is conservative on purpose: any negation, hedge or question makes the reply not
a YES, and the LLM never takes part in this decision.
"""

from __future__ import annotations

import re

from packages.domain.models import ConsentDecision
from packages.domain.pricing import format_cents

_NEGATIONS = (
    "no", "nope", "nah", "not", "don't", "dont", "do not", "stop", "cancel", "wait",
    "hold on", "never", "wrong", "isn't", "isnt", "that's not", "thats not", "negative",
)
_HEDGES = (
    "maybe", "i guess", "i think", "not sure", "probably", "what", "how much", "huh",
    "repeat", "say that again", "hmm", "um", "uh", "which", "why",
)
_AFFIRMATIVES = (
    "yes", "yeah", "yep", "yup", "sure", "go ahead", "do it", "confirm", "confirmed",
    "correct", "that's right", "thats right", "please do", "ok", "okay", "charge it",
    "sounds good", "affirmative", "absolutely", "of course",
)
_MAX_WORDS_FOR_YES = 12


def quote_short_code(quote_hash: str) -> str:
    """Four digits the caller can hear and the log can match: derived from the quote fingerprint."""
    return f"{int(quote_hash[:8], 16) % 10000:04d}"


def consent_prompt(total_cents: int, quote_hash: str) -> str:
    code = " ".join(quote_short_code(quote_hash))
    return (
        f"To confirm: I will charge {format_cents(total_cents)} to your saved PayPal "
        f"for order code {code}. Say yes to confirm or no to cancel."
    )


def _contains(text: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<![a-z']){re.escape(p)}(?![a-z'])", text) for p in phrases)


def classify_consent(transcript: str) -> ConsentDecision:
    raw = transcript.strip().lower()
    if not raw:
        return ConsentDecision.UNCLEAR
    text = re.sub(r"[^a-z' ]+", " ", raw.replace("’", "'"))
    text = re.sub(r"\s+", " ", text).strip()

    if _contains(text, _NEGATIONS):
        return ConsentDecision.NO
    if "?" in raw or _contains(text, _HEDGES):
        return ConsentDecision.UNCLEAR
    if len(text.split()) > _MAX_WORDS_FOR_YES:
        return ConsentDecision.UNCLEAR
    if _contains(text, _AFFIRMATIVES):
        return ConsentDecision.YES
    return ConsentDecision.UNCLEAR
