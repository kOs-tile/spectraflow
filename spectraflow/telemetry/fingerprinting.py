"""
Semantic Fingerprinting Service.

For each LLM response:
  1. Embed the response text using OpenAI text-embedding-3-small
  2. Store the vector + metadata in Qdrant for similarity search
  3. Write behavioral metrics to TimescaleDB (centroid distance, variance contributions)

TimescaleDB schema:
  behavioral_metrics — per-inference metrics hypertable
  baselines          — per-pipeline rolling baseline snapshots
  incidents          — detected drift incidents
  golden_tests       — synthesized regression test cases
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg
import numpy as np
from loguru import logger
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)

from spectraflow.config import get_settings

settings = get_settings()

# TimescaleDB DDL — executed once on startup
_SCHEMA_SQL = """
-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Per-inference behavioral metrics
CREATE TABLE IF NOT EXISTS behavioral_metrics (
    time            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_id        TEXT NOT NULL,
    pipeline_name   TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    prompt_version  TEXT NOT NULL DEFAULT 'unknown',
    user_segment    TEXT NOT NULL DEFAULT 'unknown',
    response_hash   TEXT NOT NULL,
    response_length INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    latency_seconds FLOAT NOT NULL DEFAULT 0.0,
    finish_reason   TEXT NOT NULL DEFAULT 'unknown',
    embedding_norm  FLOAT,
    PRIMARY KEY (time, event_id)
);

-- Convert to hypertable (time-partitioned)
SELECT create_hypertable(
    'behavioral_metrics', 'time',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Index for pipeline queries
CREATE INDEX IF NOT EXISTS idx_behavioral_pipeline
    ON behavioral_metrics (pipeline_name, time DESC);

-- Rolling baseline snapshots
CREATE TABLE IF NOT EXISTS baselines (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pipeline_name   TEXT NOT NULL,
    window_start    TIMESTAMPTZ NOT NULL,
    window_end      TIMESTAMPTZ NOT NULL,
    sample_count    INTEGER NOT NULL,
    centroid        JSONB NOT NULL,   -- serialized vector
    semantic_variance FLOAT NOT NULL,
    schema_compliance_rate FLOAT NOT NULL DEFAULT 1.0,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_baselines_pipeline
    ON baselines (pipeline_name, computed_at DESC);

-- Detected drift incidents
CREATE TABLE IF NOT EXISTS incidents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pipeline_name   TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'medium',  -- low | medium | high | critical
    drift_magnitude FLOAT NOT NULL,
    cusum_statistic FLOAT NOT NULL,
    first_signal_at TIMESTAMPTZ,
    root_cause      JSONB,            -- structured IncidentReport
    resolved_at     TIMESTAMPTZ,
    resolved_by     TEXT,
    status          TEXT NOT NULL DEFAULT 'open'  -- open | investigating | resolved
);

CREATE INDEX IF NOT EXISTS idx_incidents_pipeline
    ON incidents (pipeline_name, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_incidents_status
    ON incidents (status, detected_at DESC);

-- Synthesized regression tests
CREATE TABLE IF NOT EXISTS golden_tests (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pipeline_name           TEXT NOT NULL,
    input_messages          JSONB NOT NULL,
    expected_behavior       TEXT NOT NULL,
    expected_keywords       JSONB NOT NULL DEFAULT '[]',
    expected_schema         JSONB,
    source_incident_id      UUID REFERENCES incidents(id),
    last_run_at             TIMESTAMPTZ,
    last_run_passed         BOOLEAN,
    last_run_score          FLOAT,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_golden_tests_pipeline
    ON golden_tests (pipeline_name, created_at DESC);
"""


class FingerprintingService:
    """
    Embeds LLM responses and stores behavioral metrics.
    """

    def __init__(self) -> None:
        self.openai: AsyncOpenAI | None = None
        self.qdrant: AsyncQdrantClient | None = None
        self.pg_pool: asyncpg.Pool | None = None

    async def setup(self) -> None:
        """Initialize all clients."""
        self.openai = AsyncOpenAI(api_key=settings.openai_api_key)
        self.qdrant = AsyncQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
        )
        self.pg_pool = await asyncpg.create_pool(
            settings.timescaledb_url,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        logger.info("FingerprintingService clients initialized")

    async def teardown(self) -> None:
        if self.pg_pool:
            await self.pg_pool.close()
        if self.qdrant:
            await self.qdrant.close()

    # ── Embedding ─────────────────────────────────────────────────────────────

    async def embed(self, text: str) -> list[float]:
        """
        Embed text using OpenAI text-embedding-3-small.
        Returns a 1536-dimensional float vector.
        """
        # Truncate to avoid token limit (8192 tokens ~ ~32k chars)
        truncated = text[:32000]
        response = await self.openai.embeddings.create(
            model=settings.embedding_model,
            input=truncated,
        )
        return response.data[0].embedding

    # ── Qdrant Operations ─────────────────────────────────────────────────────

    async def store_vector(
        self,
        vector: list[float],
        payload: dict[str, Any],
        point_id: str,
    ) -> None:
        """Store embedding vector in Qdrant with metadata payload."""
        point = PointStruct(
            id=point_id,
            vector=vector,
            payload=payload,
        )
        await self.qdrant.upsert(
            collection_name=settings.qdrant_collection,
            points=[point],
        )

    async def get_similar_responses(
        self,
        vector: list[float],
        pipeline_name: str,
        limit: int = 10,
        score_threshold: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Search Qdrant for semantically similar past responses in the same pipeline."""
        results = await self.qdrant.search(
            collection_name=settings.qdrant_collection,
            query_vector=vector,
            query_filter={
                "must": [
                    {"key": "pipeline_name", "match": {"value": pipeline_name}}
                ]
            },
            limit=limit,
            score_threshold=score_threshold,
        )
        return [
            {
                "score": r.score,
                "payload": r.payload,
                "id": str(r.id),
            }
            for r in results
        ]

    # ── TimescaleDB Operations ────────────────────────────────────────────────

    async def write_metric(
        self,
        event_id: str,
        pipeline_name: str,
        model_id: str,
        prompt_version: str,
        user_segment: str,
        response_content: str,
        total_tokens: int,
        latency_seconds: float,
        finish_reason: str,
        embedding_norm: float | None,
    ) -> None:
        """Write per-inference behavioral metric row to TimescaleDB."""
        response_hash = hashlib.sha256(response_content.encode()).hexdigest()

        async with self.pg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO behavioral_metrics (
                    event_id, pipeline_name, model_id, prompt_version,
                    user_segment, response_hash, response_length,
                    total_tokens, latency_seconds, finish_reason, embedding_norm
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (time, event_id) DO NOTHING
                """,
                event_id,
                pipeline_name,
                model_id,
                prompt_version,
                user_segment,
                response_hash,
                len(response_content),
                total_tokens,
                latency_seconds,
                finish_reason,
                embedding_norm,
            )

    # ── Main Processing Pipeline ──────────────────────────────────────────────

    async def process_response(
        self,
        event_id: str,
        redis_event_id: str,
        pipeline_name: str,
        model_id: str,
        prompt_version: str,
        user_segment: str,
        response_content: str,
        request_messages: list[dict[str, Any]],
        latency_seconds: float,
        total_tokens: int,
        finish_reason: str,
    ) -> None:
        """
        Full fingerprinting pipeline for one LLM response:
          1. Embed response text
          2. Compute embedding norm
          3. Store vector in Qdrant
          4. Write metric to TimescaleDB
        """
        try:
            # 1. Embed
            vector = await self.embed(response_content)
            vec_array = np.array(vector, dtype=np.float32)
            embedding_norm = float(np.linalg.norm(vec_array))

            # 2. Build Qdrant payload
            # Use deterministic UUID from event_id for idempotency
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, event_id))

            payload = {
                "event_id": event_id,
                "pipeline_name": pipeline_name,
                "model_id": model_id,
                "prompt_version": prompt_version,
                "user_segment": user_segment,
                "response_hash": hashlib.sha256(response_content.encode()).hexdigest(),
                "response_preview": response_content[:200],
                "latency_seconds": latency_seconds,
                "total_tokens": total_tokens,
                "finish_reason": finish_reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # 3. Store in Qdrant
            await self.store_vector(
                vector=vector,
                payload=payload,
                point_id=point_id,
            )

            # 4. Write metric to TimescaleDB
            await self.write_metric(
                event_id=event_id,
                pipeline_name=pipeline_name,
                model_id=model_id,
                prompt_version=prompt_version,
                user_segment=user_segment,
                response_content=response_content,
                total_tokens=total_tokens,
                latency_seconds=latency_seconds,
                finish_reason=finish_reason,
                embedding_norm=embedding_norm,
            )

            logger.debug(
                "Fingerprint stored",
                event_id=event_id,
                pipeline=pipeline_name,
                qdrant_point=point_id,
                embedding_norm=round(embedding_norm, 4),
            )

        except Exception as exc:
            logger.error(
                f"Fingerprinting failed for event {event_id}: {exc}",
                exc_info=True,
            )
            raise


# ── Schema initialization (called at app startup) ─────────────────────────────

async def init_timescale_schema() -> None:
    """Create TimescaleDB tables + hypertables if they don't exist."""
    conn = await asyncpg.connect(settings.timescaledb_url)
    try:
        await conn.execute(_SCHEMA_SQL)
        logger.info("TimescaleDB schema applied")
    finally:
        await conn.close()


async def ensure_qdrant_collection() -> None:
    """Create the Qdrant collection if it doesn't exist."""
    qc = AsyncQdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
    )
    try:
        collections = await qc.get_collections()
        existing = {c.name for c in collections.collections}

        if settings.qdrant_collection not in existing:
            await qc.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=VectorParams(
                    size=settings.qdrant_vector_size,
                    distance=Distance.COSINE,
                ),
            )
            logger.info(
                f"Created Qdrant collection '{settings.qdrant_collection}'",
                vector_size=settings.qdrant_vector_size,
            )
        else:
            logger.debug(f"Qdrant collection '{settings.qdrant_collection}' already exists")
    finally:
        await qc.close()
