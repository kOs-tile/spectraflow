"""
Prometheus metrics for SPECTRAFLOW.

All metrics are registered at module import time (singleton pattern).
The /metrics endpoint is served by the prometheus_client ASGI middleware.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    Summary,
    generate_latest,
    multiprocess,
    CollectorRegistry,
    REGISTRY,
)
from fastapi import APIRouter
from fastapi.responses import Response

# ── Metric Definitions ────────────────────────────────────────────────────────

# Proxy request counter
REQUEST_COUNTER = Counter(
    "spectraflow_requests_total",
    "Total proxied LLM requests",
    labelnames=["pipeline", "model", "endpoint"],
)

# End-to-end proxy latency (includes upstream + overhead)
REQUEST_LATENCY = Histogram(
    "spectraflow_request_latency_seconds",
    "End-to-end proxy latency in seconds",
    labelnames=["pipeline", "model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

# Upstream LLM latency only
UPSTREAM_LATENCY = Histogram(
    "spectraflow_upstream_latency_seconds",
    "Upstream LLM API latency in seconds",
    labelnames=["pipeline", "model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

# Current CUSUM statistic per pipeline
DRIFT_SCORE = Gauge(
    "spectraflow_drift_score",
    "Current CUSUM drift statistic per pipeline (higher = more drift)",
    labelnames=["pipeline"],
)

# Baseline centroid distance per pipeline
BASELINE_CENTROID_DISTANCE = Gauge(
    "spectraflow_baseline_centroid_distance",
    "Mean cosine distance from rolling baseline centroid",
    labelnames=["pipeline"],
)

# Semantic variance per pipeline
SEMANTIC_VARIANCE = Gauge(
    "spectraflow_semantic_variance",
    "Semantic variance (spread) of responses per pipeline",
    labelnames=["pipeline"],
)

# Fired incidents
INCIDENTS_TOTAL = Counter(
    "spectraflow_incidents_total",
    "Total drift incidents fired",
    labelnames=["pipeline", "severity"],
)

# Regression test counts
REGRESSION_TESTS_TOTAL = Gauge(
    "spectraflow_regression_tests_total",
    "Number of golden regression test cases in registry",
    labelnames=["pipeline"],
)

# Regression test pass rate
REGRESSION_PASS_RATE = Gauge(
    "spectraflow_regression_pass_rate",
    "Pass rate of last regression test run (0-1)",
    labelnames=["pipeline"],
)

# Schema compliance rate
SCHEMA_COMPLIANCE_RATE = Gauge(
    "spectraflow_schema_compliance_rate",
    "Fraction of responses matching expected schema (0-1)",
    labelnames=["pipeline"],
)

# Telemetry queue depth (approximation from Redis stream length)
TELEMETRY_QUEUE_DEPTH = Gauge(
    "spectraflow_telemetry_queue_depth",
    "Approximate number of pending events in the telemetry stream",
)

# Fingerprinting throughput
FINGERPRINTING_THROUGHPUT = Counter(
    "spectraflow_fingerprints_processed_total",
    "Total response embeddings processed",
    labelnames=["pipeline", "model"],
)

# Fingerprinting errors
FINGERPRINTING_ERRORS = Counter(
    "spectraflow_fingerprinting_errors_total",
    "Total errors in the fingerprinting pipeline",
    labelnames=["pipeline", "error_type"],
)

# Token throughput
TOKENS_TOTAL = Counter(
    "spectraflow_tokens_total",
    "Total tokens processed (prompt + completion)",
    labelnames=["pipeline", "model", "token_type"],
)

# Active pipelines
ACTIVE_PIPELINES = Gauge(
    "spectraflow_active_pipelines",
    "Number of pipelines with activity in the last 24 hours",
)

# Root cause analysis duration
ROOT_CAUSE_DURATION = Histogram(
    "spectraflow_root_cause_analysis_seconds",
    "Duration of root cause analysis runs",
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

# Baseline refresh duration
BASELINE_REFRESH_DURATION = Histogram(
    "spectraflow_baseline_refresh_seconds",
    "Duration of baseline computation per pipeline",
    labelnames=["pipeline"],
    buckets=(0.5, 1.0, 5.0, 10.0, 30.0, 60.0),
)


# ── Metrics Endpoint ──────────────────────────────────────────────────────────

metrics_router = APIRouter()


@metrics_router.get(
    "/metrics",
    response_class=Response,
    include_in_schema=False,
)
async def prometheus_metrics() -> Response:
    """
    Prometheus metrics endpoint.
    Scraped by Prometheus at the configured interval.
    """
    data = generate_latest(REGISTRY)
    return Response(
        content=data,
        media_type=CONTENT_TYPE_LATEST,
    )
