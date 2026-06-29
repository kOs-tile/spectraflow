"""
Celery drift monitoring task.

Runs every 5 minutes (configurable). For each active pipeline:
  1. Pull centroid distance time-series from BaselineEngine
  2. Run CUSUM on the series
  3. If drift detected, fire an incident and trigger root cause analysis

Celery app configuration uses Redis as both broker and result backend.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg
from celery import Celery
from celery.schedules import crontab
from celery.utils.log import get_task_logger
from loguru import logger as loguru_logger

from spectraflow.config import get_settings
from spectraflow.detection.baseline import BaselineEngine
from spectraflow.detection.cusum import (
    CUSUMResult,
    compute_cusum_summary,
    run_cusum_on_metrics,
)
from spectraflow.monitoring.metrics import (
    DRIFT_SCORE,
    INCIDENTS_TOTAL,
)

settings = get_settings()

# ── Celery App ────────────────────────────────────────────────────────────────

celery_app = Celery(
    "spectraflow",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Beat schedule
    beat_schedule={
        "drift-monitor-periodic": {
            "task": "spectraflow.detection.drift_monitor.run_drift_monitor",
            "schedule": settings.drift_monitor_interval,  # seconds
            "options": {"expires": settings.drift_monitor_interval - 10},
        },
        "baseline-refresh-hourly": {
            "task": "spectraflow.detection.drift_monitor.refresh_baselines",
            "schedule": 3600,  # every hour
        },
    },
)

task_logger = get_task_logger(__name__)


# ── Async helpers ─────────────────────────────────────────────────────────────

def _run_async(coro: Any) -> Any:
    """Run an async coroutine synchronously in Celery tasks."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Incident management ───────────────────────────────────────────────────────

async def _create_incident(
    pg_pool: asyncpg.Pool,
    pipeline_name: str,
    drift_magnitude: float,
    cusum_statistic: float,
    first_signal_at: datetime | None,
) -> str:
    """Create a new incident record in TimescaleDB."""
    # Determine severity based on magnitude
    if drift_magnitude > 10.0:
        severity = "critical"
    elif drift_magnitude > 7.0:
        severity = "high"
    elif drift_magnitude > 4.0:
        severity = "medium"
    else:
        severity = "low"

    async with pg_pool.acquire() as conn:
        incident_id = await conn.fetchval(
            """
            INSERT INTO incidents (
                pipeline_name, severity, drift_magnitude,
                cusum_statistic, first_signal_at, status
            ) VALUES ($1, $2, $3, $4, $5, 'open')
            RETURNING id
            """,
            pipeline_name,
            severity,
            drift_magnitude,
            cusum_statistic,
            first_signal_at,
        )

    return str(incident_id)


async def _check_existing_open_incident(
    pg_pool: asyncpg.Pool, pipeline_name: str
) -> bool:
    """Return True if there's already an open incident for this pipeline."""
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id FROM incidents
            WHERE pipeline_name = $1
              AND status IN ('open', 'investigating')
              AND detected_at > NOW() - INTERVAL '1 hour'
            LIMIT 1
            """,
            pipeline_name,
        )
    return row is not None


async def _monitor_pipeline(
    engine: BaselineEngine,
    pg_pool: asyncpg.Pool,
    pipeline_name: str,
) -> dict[str, Any]:
    """
    Run full drift monitoring cycle for one pipeline.

    Returns a result dict with detection outcome.
    """
    result: dict[str, Any] = {
        "pipeline_name": pipeline_name,
        "drift_detected": False,
        "incident_id": None,
        "error": None,
    }

    try:
        # 1. Pull centroid distance timeseries (last 48 hours)
        timeseries = await engine.get_centroid_distance_timeseries(
            pipeline_name=pipeline_name,
            lookback_hours=48,
        )

        if len(timeseries) < 4:
            loguru_logger.debug(
                f"Insufficient timeseries data for '{pipeline_name}': {len(timeseries)} points"
            )
            return result

        centroid_distances = [ts["centroid_distance"] for ts in timeseries]

        # 2. Run CUSUM
        cusum_results = run_cusum_on_metrics(
            centroid_distances=centroid_distances,
            threshold_h=settings.cusum_threshold_h,
            slack_k=settings.cusum_slack_k,
        )

        summary = compute_cusum_summary(cusum_results)
        primary_result: CUSUMResult = cusum_results["centroid_distance"]

        # 3. Update Prometheus metrics
        DRIFT_SCORE.labels(pipeline=pipeline_name).set(
            primary_result.current_statistic
        )

        result["cusum_summary"] = summary
        result["drift_detected"] = bool(summary["drift_detected"])
        result["magnitude"] = float(summary["max_magnitude"])
        result["current_statistic"] = float(summary["current_max_statistic"])

        loguru_logger.info(
            f"CUSUM result for '{pipeline_name}'",
            drift=summary["drift_detected"],
            magnitude=round(float(summary["max_magnitude"]), 3),
            statistic=round(float(summary["current_max_statistic"]), 3),
        )

        # 4. Fire incident if drift detected and no active incident
        if summary["drift_detected"]:
            already_open = await _check_existing_open_incident(pg_pool, pipeline_name)

            if not already_open:
                first_signal_at = None
                if primary_result.first_signal_index >= 0:
                    ts_idx = min(primary_result.first_signal_index, len(timeseries) - 1)
                    first_signal_at = timeseries[ts_idx].get("timestamp")

                incident_id = await _create_incident(
                    pg_pool=pg_pool,
                    pipeline_name=pipeline_name,
                    drift_magnitude=float(summary["max_magnitude"]),
                    cusum_statistic=float(summary["current_max_statistic"]),
                    first_signal_at=first_signal_at,
                )

                result["incident_id"] = incident_id

                INCIDENTS_TOTAL.labels(
                    pipeline=pipeline_name,
                    severity="auto",
                ).inc()

                loguru_logger.warning(
                    f"DRIFT INCIDENT FIRED: pipeline='{pipeline_name}'",
                    incident_id=incident_id,
                    magnitude=round(float(summary["max_magnitude"]), 3),
                )

                # 5. Trigger root cause analysis asynchronously
                analyze_root_cause.delay(
                    incident_id=incident_id,
                    pipeline_name=pipeline_name,
                    drift_magnitude=float(summary["max_magnitude"]),
                )

            else:
                loguru_logger.info(
                    f"Drift detected for '{pipeline_name}' but open incident exists — skipping"
                )

    except Exception as exc:
        loguru_logger.error(
            f"Error monitoring pipeline '{pipeline_name}': {exc}",
            exc_info=True,
        )
        result["error"] = str(exc)

    return result


# ── Celery Tasks ──────────────────────────────────────────────────────────────

@celery_app.task(
    name="spectraflow.detection.drift_monitor.run_drift_monitor",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=240,
    time_limit=300,
)
def run_drift_monitor(self: Any) -> dict[str, Any]:
    """
    Main drift monitoring task — runs every DRIFT_MONITOR_INTERVAL seconds.

    Monitors all active pipelines and fires incidents for detected drift.
    """
    task_logger.info("Starting drift monitor cycle")
    start_time = datetime.now(timezone.utc)

    async def _async_monitor() -> dict[str, Any]:
        engine = BaselineEngine()
        pg_pool = await asyncpg.create_pool(settings.timescaledb_url, min_size=1, max_size=3)

        try:
            await engine.setup()
            pipeline_names = await engine.get_pipeline_names()

            if not pipeline_names:
                return {
                    "status": "no_pipelines",
                    "monitored": 0,
                    "incidents_fired": 0,
                }

            # Monitor all pipelines concurrently
            tasks = [
                _monitor_pipeline(engine, pg_pool, name)
                for name in pipeline_names
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            incidents_fired = sum(
                1 for r in results
                if isinstance(r, dict) and r.get("incident_id")
            )
            drifts_detected = sum(
                1 for r in results
                if isinstance(r, dict) and r.get("drift_detected")
            )

            duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
            task_logger.info(
                f"Drift monitor cycle complete: {len(pipeline_names)} pipelines, "
                f"{drifts_detected} drifts, {incidents_fired} incidents "
                f"in {duration_s:.1f}s"
            )

            return {
                "status": "ok",
                "monitored": len(pipeline_names),
                "drifts_detected": drifts_detected,
                "incidents_fired": incidents_fired,
                "duration_seconds": round(duration_s, 2),
            }

        finally:
            await engine.teardown()
            await pg_pool.close()

    try:
        return _run_async(_async_monitor())
    except Exception as exc:
        task_logger.error(f"Drift monitor task failed: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(
    name="spectraflow.detection.drift_monitor.refresh_baselines",
    soft_time_limit=600,
    time_limit=700,
)
def refresh_baselines() -> dict[str, Any]:
    """Hourly task to refresh behavioral baselines for all pipelines."""

    async def _async_refresh() -> dict[str, Any]:
        engine = BaselineEngine()
        await engine.setup()
        try:
            results = await engine.refresh_all_baselines()
            return {"status": "ok", "baselines_refreshed": len(results)}
        finally:
            await engine.teardown()

    return _run_async(_async_refresh())


@celery_app.task(
    name="spectraflow.detection.drift_monitor.analyze_root_cause",
    soft_time_limit=120,
    time_limit=180,
)
def analyze_root_cause(
    incident_id: str,
    pipeline_name: str,
    drift_magnitude: float,
) -> dict[str, Any]:
    """
    Trigger root cause analysis for a detected incident.
    Runs asynchronously after incident creation.
    """
    from spectraflow.agents.root_cause import RootCauseAgent

    async def _async_analyze() -> dict[str, Any]:
        agent = RootCauseAgent()
        await agent.setup()
        try:
            report = await agent.analyze(
                incident_id=incident_id,
                pipeline_name=pipeline_name,
                drift_magnitude=drift_magnitude,
            )
            return report.model_dump()
        finally:
            await agent.teardown()

    try:
        return _run_async(_async_analyze())
    except Exception as exc:
        task_logger.error(f"Root cause analysis failed for incident {incident_id}: {exc}")
        return {"error": str(exc), "incident_id": incident_id}
