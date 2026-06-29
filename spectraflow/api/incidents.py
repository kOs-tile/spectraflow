"""
Incident management API routes.

Endpoints:
  GET  /incidents                        — list all incidents (with filtering)
  GET  /incidents/{id}                   — get single incident with root cause report
  POST /incidents/{id}/resolve           — mark incident as resolved
  GET  /pipelines/{name}/drift-history   — drift score history for a pipeline
  GET  /pipelines/{name}/baseline        — active baseline for a pipeline
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from spectraflow.config import get_settings

settings = get_settings()

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_pool() -> asyncpg.Pool:
    """Create a short-lived connection pool for request handling."""
    return await asyncpg.create_pool(settings.timescaledb_url, min_size=1, max_size=3)


def _row_to_incident(row: asyncpg.Record) -> dict[str, Any]:
    """Serialize an incident DB row to a JSON-safe dict."""
    return {
        "id": str(row["id"]),
        "detected_at": row["detected_at"].isoformat(),
        "pipeline_name": row["pipeline_name"],
        "severity": row["severity"],
        "drift_magnitude": round(float(row["drift_magnitude"]), 4),
        "cusum_statistic": round(float(row["cusum_statistic"]), 4),
        "first_signal_at": row["first_signal_at"].isoformat() if row["first_signal_at"] else None,
        "root_cause": json.loads(row["root_cause"]) if row["root_cause"] else None,
        "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
        "resolved_by": row["resolved_by"],
        "status": row["status"],
    }


# ── Request/Response Models ───────────────────────────────────────────────────

class ResolveIncidentRequest(BaseModel):
    resolved_by: str = "manual"
    resolution_notes: str = ""


class IncidentListResponse(BaseModel):
    incidents: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/incidents")
async def list_incidents(
    status: str | None = Query(default=None, description="Filter by status: open|investigating|resolved"),
    pipeline: str | None = Query(default=None, description="Filter by pipeline name"),
    severity: str | None = Query(default=None, description="Filter by severity: low|medium|high|critical"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> JSONResponse:
    """
    List drift incidents with optional filtering.

    Returns paginated list of incidents, sorted by detection time descending.
    """
    pool = await _get_pool()
    try:
        conditions: list[str] = []
        params: list[Any] = []

        if status:
            params.append(status)
            conditions.append(f"status = ${len(params)}")

        if pipeline:
            params.append(pipeline)
            conditions.append(f"pipeline_name = ${len(params)}")

        if severity:
            params.append(severity)
            conditions.append(f"severity = ${len(params)}")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        offset = (page - 1) * page_size

        async with pool.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM incidents {where_clause}",
                *params,
            )

            params_with_pagination = params + [page_size, offset]
            rows = await conn.fetch(
                f"""
                SELECT id, detected_at, pipeline_name, severity, drift_magnitude,
                       cusum_statistic, first_signal_at, root_cause, resolved_at,
                       resolved_by, status
                FROM incidents
                {where_clause}
                ORDER BY detected_at DESC
                LIMIT ${len(params_with_pagination) - 1}
                OFFSET ${len(params_with_pagination)}
                """,
                *params_with_pagination,
            )

        return JSONResponse({
            "incidents": [_row_to_incident(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        })

    finally:
        await pool.close()


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: str) -> JSONResponse:
    """
    Get a single incident by ID, including full root cause analysis report.
    """
    pool = await _get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, detected_at, pipeline_name, severity, drift_magnitude,
                       cusum_statistic, first_signal_at, root_cause, resolved_at,
                       resolved_by, status
                FROM incidents
                WHERE id = $1::uuid
                """,
                incident_id,
            )

        if not row:
            raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

        return JSONResponse(_row_to_incident(row))

    finally:
        await pool.close()


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident(
    incident_id: str,
    body: ResolveIncidentRequest,
) -> JSONResponse:
    """
    Mark an incident as resolved.
    """
    pool = await _get_pool()
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE incidents
                SET status = 'resolved',
                    resolved_at = NOW(),
                    resolved_by = $1
                WHERE id = $2::uuid AND status != 'resolved'
                """,
                body.resolved_by,
                incident_id,
            )

        if result == "UPDATE 0":
            raise HTTPException(
                status_code=404,
                detail=f"Incident {incident_id} not found or already resolved",
            )

        logger.info(f"Incident {incident_id} resolved by {body.resolved_by}")
        return JSONResponse({"status": "resolved", "incident_id": incident_id})

    finally:
        await pool.close()


@router.get("/pipelines/{pipeline_name}/drift-history")
async def get_drift_history(
    pipeline_name: str,
    hours: int = Query(default=168, ge=1, le=720, description="Lookback hours (default 168 = 7 days)"),
) -> JSONResponse:
    """
    Get drift score history for a pipeline.

    Returns hourly aggregated metrics useful for rendering the drift timeline chart.
    """
    pool = await _get_pool()
    try:
        async with pool.acquire() as conn:
            # Per-hour metric aggregates
            metric_rows = await conn.fetch(
                """
                SELECT
                    time_bucket('1 hour', time) AS bucket,
                    AVG(embedding_norm) AS avg_norm,
                    COUNT(*) AS sample_count,
                    AVG(total_tokens) AS avg_tokens,
                    AVG(latency_seconds) AS avg_latency,
                    AVG(response_length) AS avg_response_length
                FROM behavioral_metrics
                WHERE pipeline_name = $1
                  AND time > NOW() - ($2 || ' hours')::INTERVAL
                GROUP BY bucket
                ORDER BY bucket ASC
                """,
                pipeline_name,
                str(hours),
            )

            # Incidents in the same window
            incident_rows = await conn.fetch(
                """
                SELECT id, detected_at, severity, drift_magnitude, status
                FROM incidents
                WHERE pipeline_name = $1
                  AND detected_at > NOW() - ($2 || ' hours')::INTERVAL
                ORDER BY detected_at ASC
                """,
                pipeline_name,
                str(hours),
            )

            # Active baseline
            baseline_row = await conn.fetchrow(
                """
                SELECT semantic_variance, schema_compliance_rate,
                       sample_count, computed_at
                FROM baselines
                WHERE pipeline_name = $1 AND is_active = TRUE
                ORDER BY computed_at DESC
                LIMIT 1
                """,
                pipeline_name,
            )

        metrics = [
            {
                "timestamp": row["bucket"].isoformat(),
                "sample_count": int(row["sample_count"]),
                "avg_embedding_norm": round(float(row["avg_norm"] or 0), 4),
                "avg_tokens": round(float(row["avg_tokens"] or 0), 1),
                "avg_latency_seconds": round(float(row["avg_latency"] or 0), 3),
                "avg_response_length": round(float(row["avg_response_length"] or 0), 0),
            }
            for row in metric_rows
        ]

        incidents = [
            {
                "id": str(row["id"]),
                "detected_at": row["detected_at"].isoformat(),
                "severity": row["severity"],
                "drift_magnitude": round(float(row["drift_magnitude"]), 4),
                "status": row["status"],
            }
            for row in incident_rows
        ]

        baseline_summary = None
        if baseline_row:
            baseline_summary = {
                "semantic_variance": round(float(baseline_row["semantic_variance"]), 4),
                "schema_compliance_rate": round(float(baseline_row["schema_compliance_rate"]), 4),
                "sample_count": baseline_row["sample_count"],
                "computed_at": baseline_row["computed_at"].isoformat(),
            }

        return JSONResponse({
            "pipeline_name": pipeline_name,
            "lookback_hours": hours,
            "metrics": metrics,
            "incidents": incidents,
            "baseline": baseline_summary,
        })

    finally:
        await pool.close()


@router.get("/pipelines/{pipeline_name}/baseline")
async def get_pipeline_baseline(pipeline_name: str) -> JSONResponse:
    """
    Get the active behavioral baseline for a pipeline.
    """
    pool = await _get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, computed_at, pipeline_name, window_start, window_end,
                       sample_count, semantic_variance, schema_compliance_rate
                FROM baselines
                WHERE pipeline_name = $1 AND is_active = TRUE
                ORDER BY computed_at DESC
                LIMIT 1
                """,
                pipeline_name,
            )

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No active baseline found for pipeline '{pipeline_name}'",
            )

        return JSONResponse({
            "id": str(row["id"]),
            "computed_at": row["computed_at"].isoformat(),
            "pipeline_name": row["pipeline_name"],
            "window_start": row["window_start"].isoformat(),
            "window_end": row["window_end"].isoformat(),
            "sample_count": row["sample_count"],
            "semantic_variance": round(float(row["semantic_variance"]), 4),
            "schema_compliance_rate": round(float(row["schema_compliance_rate"]), 4),
        })

    finally:
        await pool.close()


@router.get("/pipelines")
async def list_pipelines() -> JSONResponse:
    """List all active pipelines with summary statistics."""
    pool = await _get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    pipeline_name,
                    COUNT(*) AS total_requests,
                    MAX(time) AS last_seen,
                    AVG(latency_seconds) AS avg_latency
                FROM behavioral_metrics
                WHERE time > NOW() - INTERVAL '24 hours'
                GROUP BY pipeline_name
                ORDER BY total_requests DESC
                """
            )

        return JSONResponse({
            "pipelines": [
                {
                    "pipeline_name": r["pipeline_name"],
                    "total_requests_24h": int(r["total_requests"]),
                    "last_seen": r["last_seen"].isoformat(),
                    "avg_latency_seconds": round(float(r["avg_latency"] or 0), 3),
                }
                for r in rows
            ]
        })

    finally:
        await pool.close()
