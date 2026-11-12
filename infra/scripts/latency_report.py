"""
Write docs/latency.md from measurements stored by the running system. Nothing is typed in by hand.

- LLM latency and time-to-first-token: call_turns.llm_ms / llm_ttft_ms (measured from the stream).
- Speech: call_turns.stt_ms / tts_ms, reported by the speech client (browser Web Speech API in the
  web demo); labelled as client-measured. Twilio's speech pipeline is not instrumented.
- Webhook delivery: webhook_events.received_at minus the event's create_time, for real sandbox
  events only (mock-mode runs have no meaningful create_time).

    DATABASE_URL=... PAYPAL_MODE=sandbox python -m infra.scripts.latency_report --environment "Render staging, oregon"
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from apps.api.db.models import CallTurnModel, WebhookEventModel
from apps.api.db.session import make_engine, make_session_maker
from apps.core.settings import get_settings

OUT = Path(__file__).resolve().parents[2] / "docs" / "latency.md"


def stats(xs: list[int]) -> str:
    if not xs:
        return "no data | – | – | –"
    xs = sorted(xs)

    def q(p: float) -> int:
        return xs[min(len(xs) - 1, int(p * len(xs)))]

    return f"{len(xs)} | {q(0.5)} | {q(0.95)} | {xs[-1]}"


async def main(environment: str) -> None:
    settings = get_settings()
    engine = make_engine(settings.async_database_url)
    sm = make_session_maker(engine)
    async with sm() as s:
        turns = (await s.execute(select(CallTurnModel).where(CallTurnModel.speaker == "agent"))).scalars().all()
        hooks = (await s.execute(select(WebhookEventModel).where(WebhookEventModel.paypal_create_time.is_not(None)))).scalars().all()
    await engine.dispose()

    llm = [t.llm_ms for t in turns if t.llm_ms is not None]
    ttft = [t.llm_ttft_ms for t in turns if t.llm_ttft_ms is not None]
    server = [t.latency_ms for t in turns if t.latency_ms is not None and t.model]
    stt = [t.stt_ms for t in turns if t.stt_ms is not None]
    tts = [t.tts_ms for t in turns if t.tts_ms is not None]
    models = sorted({t.model for t in turns if t.model})
    webhook = [int((h.received_at - h.paypal_create_time).total_seconds() * 1000)  # type: ignore[operator]
               for h in hooks] if settings.paypal_mode.value != "mock" else []

    OUT.write_text("\n".join([
        "# Latency measurements", "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC} by `infra/scripts/latency_report.py` from the database "
        f"of: **{environment}** (PayPal mode `{settings.paypal_mode.value}`; models: {', '.join(models) or 'none'}).", "",
        "Only measured values appear here. A row that says *no data* has not been measured yet.", "",
        "| Measurement (ms) | Source | n | p50 | p95 | max |", "| --- | --- | ---: | ---: | ---: | ---: |",
        f"| LLM time to first token | server, streamed | {stats(ttft)} |",
        f"| LLM total per turn (all tool steps) | server | {stats(llm)} |",
        f"| Agent turn, server side (LLM + tools + DB) | server | {stats(server)} |",
        f"| Speech-to-text | client-reported (browser) | {stats(stt)} |",
        f"| Text-to-speech start | client-reported (browser) | {stats(tts)} |",
        f"| PayPal webhook: event create_time → received | server clock vs PayPal | {stats(webhook)} |",
        "", "Webhook delivery compares PayPal's clock with ours, so clock skew of a few hundred ms is possible.", "",
    ]), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--environment", required=True, help="where the data came from, e.g. 'Render staging'")
    asyncio.run(main(ap.parse_args().environment))
