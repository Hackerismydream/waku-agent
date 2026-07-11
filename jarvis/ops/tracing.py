"""Trace — one trace per run (the LLM-Ops box, first step).

Two outputs from the same events:

1. JSONL, always on: every turn appends readable lines to
   .jarvis/traces/<date>.jsonl. A trace is just "what happened, in order" —
   open the file and read your agent's mind. Zero dependencies.

2. OpenTelemetry spans, when OTEL_EXPORTER_OTLP_ENDPOINT is set: the same
   events as a span tree any OTel backend can render. For a local dashboard:

       pip install 'launch-jarvis[tracing]'
       phoenix serve                                # localhost:6006
       OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 python -m jarvis

   Langfuse cloud speaks OTel too — point the endpoint + auth headers there
   instead. The instrumentation below doesn't know or care which.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone

from jarvis.config import Settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Tracer:
    """Doubles as a loop Observer: pass `tracer.event` anywhere an observer
    goes and every loop step lands in the trace."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.home / "traces" / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        self._otel_tracer = self._init_otel(settings)
        self._span_ctx = None

    def _init_otel(self, settings: Settings):
        if not settings.otel_endpoint:
            return None
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(resource=Resource.create({"service.name": "launch-jarvis"}))
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True))
            )
            trace.set_tracer_provider(provider)
            self._otel_provider = provider
            return trace.get_tracer("jarvis")
        except ImportError:
            print("(tracing) OTEL endpoint set but opentelemetry not installed — "
                  "pip install 'launch-jarvis[tracing]'. JSONL tracing still on.")
            return None

    def _write(self, record: dict) -> None:
        record["ts"] = _now()
        with self.path.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    # ---- the Observer: called by the loop for every llm/tool/gate/... event
    def event(self, kind: str, event: dict) -> None:
        if kind == "text":
            return  # streaming token deltas are for the live UI, not the trace
        self._write({"type": kind, **event})
        if self._otel_tracer and self._span_ctx is not None:
            with self._otel_tracer.start_as_current_span(
                f"{kind}.{event.get('tool', event.get('decision', ''))}".rstrip("."),
                attributes={
                    "openinference.span.kind": {"llm": "LLM", "tool": "TOOL"}.get(kind, "CHAIN"),
                    **{f"jarvis.{k}": json.dumps(v, default=str) for k, v in event.items()},
                },
            ):
                pass

    # ---- one run = one root span + turn_start/turn_end JSONL markers
    @contextmanager
    def turn(self, user_message: str):
        self._write({"type": "turn_start", "user_message": user_message})
        if self._otel_tracer:
            with self._otel_tracer.start_as_current_span(
                "agent_run",
                attributes={"openinference.span.kind": "AGENT", "jarvis.user_message": user_message},
            ) as span:
                self._span_ctx = span
                try:
                    yield self
                finally:
                    self._span_ctx = None
        else:
            yield self

    def end_turn(self, reply: str, iterations: int) -> None:
        self._write({"type": "turn_end", "reply": reply, "iterations": iterations})
        if getattr(self, "_otel_provider", None):
            # flush per turn: the trace should survive even a killed process
            self._otel_provider.force_flush(timeout_millis=2000)


def compose(*observers) -> callable:
    """Fan one loop event out to several observers (gateway display + tracer)."""
    active = [o for o in observers if o]
    def fanout(kind: str, event: dict) -> None:
        for obs in active:
            obs(kind, event)
    return fanout
