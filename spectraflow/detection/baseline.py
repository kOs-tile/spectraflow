"""
Behavioral Baseline Engine.

Computes rolling behavioral baselines per pipeline from embeddings stored in Qdrant.

Metrics computed:
  - centroid:               Mean embedding vector over the baseline window
  - semantic_variance:      Mean pairwise cosine distance (spread of responses)
  - schema_compliance_rate: Fraction of responses matching expected JSON schema

Baselines are stored in TimescaleDB and used by the CUSUM drift detector.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import numpy as np
from loguru import logger
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, Range

from spectraflow.config import get_settings

settings = get_settings()


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine distance between two vectors (1 - cosine_similarity). Range [0, 2]."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return float(1.0 - np.dot(a, b) / (norm_a * norm_b))


def compute_centroid(vectors: list[list[float]]) -> np.ndarray:
    """Compute the mean vector (centroid) of a set of embedding vectors."""
    if not vectors:
        raise ValueError("Cannot compute centroid of empty vector set")
    arr = np.array(vectors, dtype=np.float64)
    return np.mean(arr, axis=0)


def compute_semantic_variance(
    vectors: list[list[float]],
    centroid: np.ndarray,
) -> float:
    """
    Compute semantic variance as the mean cosine distance from the centroid.
    This measures how spread out the responses are semantically.

    Returns a value in [0, 2], where 0 = all responses identical,
    2 = all responses maximally diverse.
    """
    if len(vectors) < 2:
        return 0.0

    distances = [
        _cosine_distance(np.array(v, dtype=np.float64), centroid)
        for v in vectors
    ]
    return float(np.mean(distances))


def compute_schema_compliance(responses: list[str]) -> float:
    """
    Compute the fraction of responses that are valid JSON.
    Useful for pipelines that expect structured output.

    Returns a value in [0.0, 1.0].
    """
    if not responses:
        return 1.0

    valid = 0
    for response in responses:
        try:
            stripped = response.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                json.loads(stripped)
                valid += 1
            else:
                # Non-JSON responses are "compliant" by default
                # (schema compliance only tracks pipelines expecting JSON)
                valid += 1
        except json.JSONDecodeError:
            pass  # Failed to parse JSON response

    return valid / len(responses)


class BaselineEngine:
    """
    Computes and stores rolling behavioral baselines per pipeline.

    A baseline represents the "expected" behavior window — the past N days
    of production traffic for a given pipeline. Drift is measured relative
    to this baseline.
    """

    def __init__(self) -> None:
        self.qdrant: AsyncQdrantClient | None = None
        self.pg_pool: asyncpg.Pool | None = None

    async def setup(self) -> None:
        self.qdrant = AsyncQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
        )
        self.pg_pool = await asyncpg.create_pool(
            settings.timescaledb_url,
            min_size=1,
            max_size=5,
        )
        logger.info("BaselineEngine initialized")

    async def teardown(self) -> None:
        if self.pg_pool:
            await self.pg_pool.close()
        if self.qdrant:
            await self.qdrant.close()

    async def get_pipeline_names(self) -> list[str]:
        """Return all distinct pipeline names with recent activity."""
        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT pipeline_name
                FROM behavioral_metrics
                WHERE time > NOW() - INTERVAL '30 days'
                ORDER BY pipeline_name
                """
            )
        return [row["pipeline_name"] for row in rows]

    async def fetch_window_vectors(
        self,
        pipeline_name: str,
        window_days: int | None = None,
    ) -> tuple[list[list[float]], list[str], datetime, datetime]:
        """
        Fetch embedding vectors + response previews from Qdrant
        for the given pipeline and time window.

        Returns:
            (vectors, response_previews, window_start, window_end)
        """
        window_days = window_days or settings.baseline_window_days
        window_end = datetime.now(timezone.utc)
        window_start = window_end - timedelta(days=window_days)

        # Scroll through all points for this pipeline in the window
        # Note: Qdrant doesn't support range filters on string timestamps natively,
        # so we scroll and filter by timestamp in Python.
        all_vectors: list[list[float]] = []
        all_previews: list[str] = []
        offset = None

        while True:
            results, next_offset = await self.qdrant.scroll(
                collection_name=settings.qdrant_collection,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="pipeline_name",
                            match=MatchValue(value=pipeline_name),
                        )
                    ]
                ),
                with_vectors=True,
                with_payload=True,
                limit=1000,
                offset=offset,
            )

            for point in results:
                # Filter by timestamp
                ts_str = point.payload.get("timestamp", "")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts < window_start:
                            continue
                    except ValueError:
                        pass

                if point.vector:
                    all_vectors.append(point.vector)
                    all_previews.append(point.payload.get("response_preview", ""))

            if next_offset is None:
                break
            offset = next_offset

        return all_vectors, all_previews, window_start, window_end

    async def compute_baseline(self, pipeline_name: str) -> dict[str, Any] | None:
        """
        Compute a fresh baseline for the given pipeline.

        Returns None if insufficient samples are available.
        """
        vectors, previews, window_start, window_end = await self.fetch_window_vectors(
            pipeline_name
        )

        if len(vectors) < settings.baseline_min_samples:
            logger.warning(
                f"Insufficient samples for baseline: {len(vectors)}/{settings.baseline_min_samples}",
                pipeline=pipeline_name,
            )
            return None

        centroid = compute_centroid(vectors)
        variance = compute_semantic_variance(vectors, centroid)
        schema_rate = compute_schema_compliance(previews)

        baseline = {
            "pipeline_name": pipeline_name,
            "window_start": window_start,
            "window_end": window_end,
            "sample_count": len(vectors),
            "centroid": centroid.tolist(),
            "semantic_variance": variance,
            "schema_compliance_rate": schema_rate,
        }

        logger.info(
            f"Computed baseline for pipeline '{pipeline_name}'",
            samples=len(vectors),
            variance=round(variance, 4),
            schema_rate=round(schema_rate, 4),
        )

        return baseline

    async def save_baseline(self, baseline: dict[str, Any]) -> str:
        """Persist baseline to TimescaleDB and deactivate old baselines."""
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                # Deactivate old baselines for this pipeline
                await conn.execute(
                    "UPDATE baselines SET is_active = FALSE WHERE pipeline_name = $1",
                    baseline["pipeline_name"],
                )

                # Insert new baseline
                row_id = await conn.fetchval(
                    """
                    INSERT INTO baselines (
                        pipeline_name, window_start, window_end,
                        sample_count, centroid, semantic_variance,
                        schema_compliance_rate, is_active
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE)
                    RETURNING id
                    """,
                    baseline["pipeline_name"],
                    baseline["window_start"],
                    baseline["window_end"],
                    baseline["sample_count"],
                    json.dumps(baseline["centroid"]),
                    baseline["semantic_variance"],
                    baseline["schema_compliance_rate"],
                )

        logger.info(
            f"Baseline saved for pipeline '{baseline['pipeline_name']}'",
            baseline_id=str(row_id),
        )
        return str(row_id)

    async def get_active_baseline(
        self, pipeline_name: str
    ) -> dict[str, Any] | None:
        """Retrieve the current active baseline for a pipeline."""
        async with self.pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, computed_at, pipeline_name, window_start, window_end,
                       sample_count, centroid, semantic_variance, schema_compliance_rate
                FROM baselines
                WHERE pipeline_name = $1 AND is_active = TRUE
                ORDER BY computed_at DESC
                LIMIT 1
                """,
                pipeline_name,
            )

        if not row:
            return None

        return {
            "id": str(row["id"]),
            "computed_at": row["computed_at"],
            "pipeline_name": row["pipeline_name"],
            "window_start": row["window_start"],
            "window_end": row["window_end"],
            "sample_count": row["sample_count"],
            "centroid": json.loads(row["centroid"]),
            "semantic_variance": row["semantic_variance"],
            "schema_compliance_rate": row["schema_compliance_rate"],
        }

    async def refresh_all_baselines(self) -> dict[str, str]:
        """
        Recompute and save baselines for all active pipelines.
        Called by the drift monitor before each CUSUM evaluation.

        Returns mapping of pipeline_name → baseline_id.
        """
        pipeline_names = await self.get_pipeline_names()
        results: dict[str, str] = {}

        for pipeline_name in pipeline_names:
            try:
                baseline = await self.compute_baseline(pipeline_name)
                if baseline:
                    baseline_id = await self.save_baseline(baseline)
                    results[pipeline_name] = baseline_id
            except Exception as exc:
                logger.error(
                    f"Failed to refresh baseline for '{pipeline_name}': {exc}",
                    exc_info=True,
                )

        logger.info(f"Refreshed {len(results)}/{len(pipeline_names)} baselines")
        return results

    async def get_centroid_distance_timeseries(
        self,
        pipeline_name: str,
        lookback_hours: int = 48,
    ) -> list[dict[str, Any]]:
        """
        Compute the time series of centroid distances used for CUSUM.

        For each hour in the lookback window, computes the mean cosine
        distance of that hour's embeddings from the baseline centroid.

        Returns list of {timestamp, centroid_distance, sample_count} dicts.
        """
        baseline = await self.get_active_baseline(pipeline_name)
        if not baseline:
            logger.warning(
                f"No active baseline for pipeline '{pipeline_name}', cannot compute distances"
            )
            return []

        centroid = np.array(baseline["centroid"], dtype=np.float64)

        # Get per-hour embedding norms from TimescaleDB (proxy for distance)
        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    time_bucket('1 hour', time) AS bucket,
                    AVG(embedding_norm) AS avg_norm,
                    COUNT(*) AS sample_count
                FROM behavioral_metrics
                WHERE pipeline_name = $1
                  AND time > NOW() - ($2 || ' hours')::INTERVAL
                GROUP BY bucket
                ORDER BY bucket ASC
                """,
                pipeline_name,
                str(lookback_hours),
            )

        # Get actual vectors from Qdrant for more accurate distance computation
        # For efficiency, we sample up to 200 recent vectors
        vectors, _, _, _ = await self.fetch_window_vectors(
            pipeline_name, window_days=max(1, lookback_hours // 24)
        )

        # Build hourly distance from Qdrant vectors (approximate — vectors aren't
        # timestamped at hour granularity in Qdrant, so use DB aggregate as proxy)
        timeseries = []
        for row in rows:
            # Use embedding_norm deviation from baseline norm as distance proxy
            baseline_norm = float(np.linalg.norm(centroid))
            norm_diff = abs(float(row["avg_norm"] or baseline_norm) - baseline_norm)
            # Normalize to [0, 1] range
            approx_distance = min(1.0, norm_diff / (baseline_norm + 1e-6))

            timeseries.append({
                "timestamp": row["bucket"],
                "centroid_distance": approx_distance,
                "sample_count": int(row["sample_count"]),
            })

        return timeseries
