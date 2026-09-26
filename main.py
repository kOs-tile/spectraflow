"""
SPECTRAFLOW — main FastAPI application entry point.

Run with:
    uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
"""

from __future__ import annotations

import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from spectraflow.config import get_settings
from spectraflow.proxy.router import router as proxy_router
from spectraflow.api.incidents import router as incidents_router
from spectraflow.api.authority import router as authority_router
from spectraflow.api.tests import router as tests_router
from spectraflow.monitoring.metrics import metrics_router

settings = get_settings()

# ── Logging setup ─────────────────────────────────────────────────────────────
logger.remove()
if settings.log_format == "json":
    # Let Loguru own JSON serialization. Hand-rolled JSON in `format=`
    # is parsed as a Loguru format string and literal braces become fields.
    logger.add(
        sys.stdout,
        level=settings.log_level,
        serialize=True,
    )
else:
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> — <level>{message}</level>",
        level=settings.log_level,
        colorize=True,
    )

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="SPECTRAFLOW",
    description="LLM observability and semantic-drift research platform",
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(proxy_router, tags=["Proxy"])
app.include_router(incidents_router, prefix="/api/v1", tags=["Incidents"])
app.include_router(authority_router, prefix="/api/v1", tags=["Authority Drift"])
app.include_router(tests_router, prefix="/api/v1", tags=["Regression Tests"])
app.include_router(metrics_router, tags=["Monitoring"])


# ── Startup / Shutdown ────────────────────────────────────────────────────────
@app.on_event("startup")
async def on_startup() -> None:
    logger.info(
        f"SPECTRAFLOW {settings.app_version} starting up",
        upstream=settings.upstream_base_url,
        embedding_model=settings.embedding_model,
    )
    # Initialize TimescaleDB schema
    try:
        from spectraflow.telemetry.fingerprinting import init_timescale_schema
        await init_timescale_schema()
        logger.info("TimescaleDB schema initialized")
    except Exception as exc:
        logger.warning(f"TimescaleDB schema init skipped (not critical at startup): {exc}")

    # Ensure Qdrant collection exists
    try:
        from spectraflow.telemetry.fingerprinting import ensure_qdrant_collection
        await ensure_qdrant_collection()
        logger.info("Qdrant collection ready")
    except Exception as exc:
        logger.warning(f"Qdrant collection init skipped: {exc}")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("SPECTRAFLOW shutting down")
