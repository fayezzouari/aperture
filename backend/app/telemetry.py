"""R5.3 - OpenTelemetry wiring.

Disabled by default so an air-gapped install makes no outbound calls (R7.7).
When enabled we export over OTLP/HTTP to a collector inside the deployment. The
inbound `traceparent` header is honoured, so a trace that starts in a product
continues here and shows the inference span in the product's own trace view.
"""

from __future__ import annotations

import logging

from app.settings import settings

log = logging.getLogger("aperture.telemetry")
_tracer = None


def setup_telemetry(app=None, engine=None) -> None:
    global _tracer
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({
            "service.name": "aperture-api",
            "service.version": "0.1.0",
            "deployment.environment": settings.env,
        })
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{settings.otel_endpoint}/v1/traces"))
        )
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("aperture")

        if app is not None:
            FastAPIInstrumentor.instrument_app(app, excluded_urls="/metrics,/healthz,/readyz")
        HTTPXClientInstrumentor().instrument()
        if engine is not None:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

            SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
        log.info("telemetry enabled", extra={"endpoint": settings.otel_endpoint})
    except Exception:  # never let telemetry stop the API from serving
        log.exception("telemetry setup failed; continuing without tracing")


def current_ids() -> tuple[str | None, str | None]:
    """(trace_id, span_id) as hex, or (None, None) when tracing is off."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if not ctx or not ctx.trace_id:
            return None, None
        return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    except Exception:
        return None, None


def start_span(name: str, attributes: dict | None = None):
    """Context manager that is a no-op when telemetry is disabled."""
    if _tracer is None:
        from contextlib import nullcontext

        return nullcontext()
    return _tracer.start_as_current_span(name, attributes=attributes or {})


def traceparent_header(incoming: str | None) -> dict[str, str]:
    """Forward the caller's traceparent to the runtime so the trace stays one tree."""
    return {"traceparent": incoming} if incoming else {}
