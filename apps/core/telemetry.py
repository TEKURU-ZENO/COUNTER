"""
OpenTelemetry setup.

Spans: agent.turn > llm.complete (per model call) and tool.<name>; stt and tts spans are
recorded from durations the speech client measured (browser Web Speech API in the web demo)
and are tagged measured_by=client so they are never confused with server measurements.

Export: set OTEL_EXPORTER_OTLP_ENDPOINT (and OTEL_EXPORTER_OTLP_HEADERS) to send traces to any
OTLP backend. Elastic Observability ingests OTLP directly, e.g.
  OTEL_EXPORTER_OTLP_ENDPOINT=https://<deployment>.apm.<region>.cloud.es.io:443
  OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer <secret-token>"
Without an endpoint, spans are created but not exported; per-turn timings are still persisted
in call_turns, which is what infra/scripts/latency_report.py reads.
"""

from __future__ import annotations

import os
import time

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_configured = False


def setup_tracing(service_name: str, environment: str) -> None:
    global _configured
    if _configured:
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name, "deployment.environment": environment})
    )
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True


tracer = trace.get_tracer("counter")


def record_client_span(name: str, duration_ms: int | None, **attrs: object) -> None:
    """Record a span whose duration was measured by the client (end = now)."""
    if duration_ms is None or duration_ms < 0:
        return
    end = time.time_ns()
    span = tracer.start_span(name, start_time=end - duration_ms * 1_000_000)
    span.set_attribute("measured_by", "client")
    for key, value in attrs.items():
        span.set_attribute(key, value)  # type: ignore[arg-type]
    span.end(end_time=end)
