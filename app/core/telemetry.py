import logging
from prometheus_client import Counter, Histogram, Gauge
from opentelemetry import trace, _logs
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from app.config import settings

logger = logging.getLogger(__name__)

# Prometheus Metrics Registry
REQUEST_COUNT = Counter("rag_requests_total", "Total API requests processed", ["method", "endpoint", "status"])
QUERY_DURATION = Histogram("rag_query_duration_seconds", "RAG query pipeline execution duration in seconds")
CACHE_HITS = Counter("rag_cache_hits_total", "Total semantic cache hits")
CACHE_MISSES = Counter("rag_cache_misses_total", "Total semantic cache misses")
PENDING_INGESTION_TASKS = Gauge("rag_celery_pending_tasks", "Pending async document ingestion tasks")

LLM_TOKENS = Counter(
    "rag_llm_tokens_total", "Total LLM tokens consumed", ["type", "model"]
)  # type = "prompt"|"completion"

LLM_COST_USD = Counter(
    "rag_llm_cost_usd_total", "Estimated LLM API cost in USD", ["model"]
)

RETRIEVAL_SCORE = Histogram(
    "rag_retrieval_relevance_score", "Top reranked confidence score per query",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
)

GROUNDEDNESS_SCORE = Histogram(
    "rag_groundedness_score", "Generated answer groundedness score",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
)

def setup_telemetry(app=None):
    if not settings.ENABLE_TELEMETRY:
        return

    try:
        resource = Resource.create({"service.name": settings.APP_NAME})

        # 1. Traces Setup
        provider = TracerProvider(resource=resource)
        otlp_trace_exporter = OTLPSpanExporter(
            endpoint=settings.OTEL_COLLECTOR_URL,
            insecure=True
        )
        provider.add_span_processor(BatchSpanProcessor(otlp_trace_exporter))
        trace.set_tracer_provider(provider)

        # 2. Logs Setup (Stream Python logs to OpenTelemetry Collector -> Loki)
        logger_provider = LoggerProvider(resource=resource)
        _logs.set_logger_provider(logger_provider)
        otlp_log_exporter = OTLPLogExporter(
            endpoint=settings.OTEL_COLLECTOR_URL,
            insecure=True
        )
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(otlp_log_exporter))

        handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
        logging.getLogger().addHandler(handler)

        # 3. FastAPI Auto-Instrumentation
        if app is not None:
            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
                FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
                logger.info("FastAPI auto-instrumentation active.")
            except Exception as fe:
                logger.warning(f"FastAPI auto-instrumentation error: {fe}")

        logger.info("OpenTelemetry tracer, logger provider, and FastAPI auto-instrumentation initialized.")
    except Exception as e:
        logger.warning(f"Telemetry setup skipped (collector offline or dev mode): {e}")

tracer = trace.get_tracer("rag.core.tracer")
