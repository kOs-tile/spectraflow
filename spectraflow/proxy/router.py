"""
FastAPI router for the OpenAI-compatible proxy.

Endpoints:
  POST /v1/chat/completions  — main proxy endpoint
  POST /v1/embeddings        — pass-through embeddings
  GET  /v1/models            — pass-through model list
  GET  /health               — health check
  GET  /ready                — readiness probe (checks dependencies)
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from spectraflow.config import get_settings
from spectraflow.proxy.handler import (
    proxy_chat_completions,
    proxy_embeddings,
    proxy_passthrough,
)

settings = get_settings()

router = APIRouter()
_startup_time = time.time()


# ── OpenAI-Compatible Proxy Endpoints ─────────────────────────────────────────

@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    """
    OpenAI-compatible chat completions endpoint.

    This is the primary proxy endpoint. All requests are forwarded to the
    configured upstream LLM provider. Telemetry is emitted asynchronously
    with zero impact on response latency.

    Custom metadata headers (optional):
      X-Pipeline-Name: <string>   — logical pipeline name for grouping
      X-Prompt-Version: <string>  — prompt template version for attribution
      X-User-Segment: <string>    — user cohort for segmented analysis
    """
    return await proxy_chat_completions(request)


@router.post("/v1/embeddings")
async def embeddings(request: Request) -> Any:
    """
    OpenAI-compatible embeddings endpoint (pass-through).
    Embeddings are not separately fingerprinted — they are used by the
    fingerprinting service itself.
    """
    return await proxy_embeddings(request)


@router.api_route(
    "/v1/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    include_in_schema=False,
)
async def passthrough(request: Request, path: str) -> Any:
    """
    Pass-through handler for all other /v1/* endpoints.
    Covers /v1/models, /v1/files, /v1/fine-tuning, etc.
    """
    if not settings.proxy_passthrough_unknown:
        return JSONResponse(
            {"error": {"message": f"Endpoint /v1/{path} is not proxied", "type": "unsupported_endpoint"}},
            status_code=404,
        )
    return await proxy_passthrough(request)


# ── Health & Readiness ────────────────────────────────────────────────────────

@router.get("/health")
async def health() -> JSONResponse:
    """
    Liveness probe — returns 200 if the process is running.
    """
    return JSONResponse(
        {
            "status": "ok",
            "service": "spectraflow-proxy",
            "version": settings.app_version,
            "uptime_seconds": round(time.time() - _startup_time, 1),
        }
    )


@router.get("/ready")
async def readiness() -> JSONResponse:
    """
    Readiness probe — checks that upstream infrastructure is reachable.
    Returns 200 only when Redis and TimescaleDB are accessible.
    """
    checks: dict[str, Any] = {}
    all_ok = True

    # Check Redis
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
        all_ok = False

    # Check TimescaleDB
    try:
        import asyncpg
        conn = await asyncpg.connect(settings.timescaledb_url, timeout=3)
        await conn.fetchval("SELECT 1")
        await conn.close()
        checks["timescaledb"] = "ok"
    except Exception as exc:
        checks["timescaledb"] = f"error: {exc}"
        all_ok = False

    # Check Qdrant
    try:
        from qdrant_client import AsyncQdrantClient
        qc = AsyncQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            timeout=3,
        )
        await qc.get_collections()
        await qc.close()
        checks["qdrant"] = "ok"
    except Exception as exc:
        checks["qdrant"] = f"error: {exc}"
        all_ok = False

    return JSONResponse(
        {
            "status": "ready" if all_ok else "degraded",
            "checks": checks,
        },
        status_code=200 if all_ok else 503,
    )
