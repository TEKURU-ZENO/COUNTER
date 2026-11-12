"""
Map what a caller said (as transcribed) to a catalog item, with a confidence score.

Score = max(lexical similarity, phonetic similarity). Phonetic keys catch transcription slips
like "margarita" vs "Margherita" or "tear a me sue" vs "tiramisu". Below ACCEPT the agent must
confirm with the caller; below SUGGEST the item is treated as not on the menu.

Embedding similarity is not implemented: it is listed under limitations.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz

ACCEPT = 88.0
SUGGEST = 65.0

_STOPWORDS = {"a", "an", "the", "of", "with", "please", "some", "one", "order"}


def _norm(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(w for w in text.split() if w not in _STOPWORDS)


def phonetic_key(text: str) -> str:
    """Small Metaphone-style key: good enough to merge common spelling/ASR variants."""
    s = re.sub(r"[^a-z]", "", text.lower())
    if not s:
        return ""
    rules = [
        (r"^kn|^gn|^pn|^wr", lambda m: m.group(0)[1]),
        (r"ph", "f"), (r"gh", "g"), (r"ck", "k"), (r"sch", "sk"), (r"tch", "ch"),
        (r"c(?=[eiy])", "s"), (r"c", "k"), (r"q", "k"), (r"x", "ks"), (r"z", "s"),
        (r"dg(?=[eiy])", "j"), (r"v", "f"), (r"th", "t"), (r"w(?![aeiou])", ""),
        (r"h(?![aeiou])", ""),
    ]
    for pattern, repl in rules:
        s = re.sub(pattern, repl, s)
    head, tail = s[0], re.sub(r"[aeiouy]", "", s[1:])
    key = head + tail
    return re.sub(r"(.)\1+", r"\1", key)


@dataclass(frozen=True)
class Candidate:
    id: str
    name: str
    score: float


def _word_similarity(a: str, b: str) -> float:
    lexical = fuzz.ratio(a, b)
    pa, pb = phonetic_key(a), phonetic_key(b)
    phonetic = fuzz.ratio(pa, pb) * 0.95 if pa and pb else 0.0
    return float(max(lexical, phonetic))


def score(spoken: str, name: str) -> float:
    """How well the caller's words are covered by the item name (0-100).

    Each spoken word is matched to its best word in the name (spelling or sound), and the
    scores are averaged, so "margarita pizza" covers "Margherita Wood-Fired Pizza" well while
    extra descriptive words in the menu name do not count against it.
    """
    a, b = _norm(spoken), _norm(name)
    if not a or not b:
        return 0.0
    name_words = b.split()
    coverage = sum(max(_word_similarity(w, nw) for nw in name_words) for w in a.split()) / len(a.split())
    return float(max(coverage, fuzz.token_set_ratio(a, b)))


def rank(spoken: str, options: Iterable[tuple[str, str]], limit: int = 3) -> list[Candidate]:
    scored = [Candidate(oid, name, round(score(spoken, name), 1)) for oid, name in options]
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:limit]


def resolve(spoken: str, options: Sequence[tuple[str, str]]) -> tuple[Candidate | None, list[Candidate]]:
    """Exact id match wins. Otherwise return (accepted candidate or None, ranked candidates)."""
    for oid, name in options:
        if spoken == oid:
            return Candidate(oid, name, 100.0), []
    ranked = rank(spoken, options)
    if not ranked:
        return None, []
    top = ranked[0]
    runner_up = ranked[1].score if len(ranked) > 1 else 0.0
    if top.score >= ACCEPT and top.score - runner_up >= 5:
        return top, ranked
    return None, [c for c in ranked if c.score >= SUGGEST]
