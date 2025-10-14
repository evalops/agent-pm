"""OpenTelemetry instrumentation for distributed tracing."""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from agent_pm.settings import settings

logger = logging.getLogger(__name__)


def configure_telemetry(app):
    """Configure OpenTelemetry tracing for FastAPI app."""
    if not settings.enable_opentelemetry:
        logger.info("OpenTelemetry disabled")
        return

    # Create resource with service name
    resource = Resource.create({"service.name": settings.otel_service_name})

    # Setup tracer provider
    provider = TracerProvider(resource=resource)

    # Add exporters
    if settings.otel_exporter_endpoint:
        # OTLP exporter for production (Jaeger, Tempo, etc.)
        otlp_exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_endpoint)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        logger.info("OpenTelemetry OTLP exporter configured: %s", settings.otel_exporter_endpoint)
    else:
        # Console exporter for development
        console_exporter = ConsoleSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(console_exporter))
        logger.info("OpenTelemetry console exporter configured")

    # Set as global tracer provider
    trace.set_tracer_provider(provider)

    # Instrument FastAPI
    FastAPIInstrumentor.instrument_app(app)

    # Instrument HTTPX (for OpenAI/Jira/Slack calls)
    HTTPXClientInstrumentor().instrument()

    logger.info("OpenTelemetry instrumentation enabled")


def get_tracer(name: str = "agent-pm"):
    """Get tracer for manual span creation."""
    return trace.get_tracer(name)
