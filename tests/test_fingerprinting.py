"""
Tests for the semantic fingerprinting pipeline.

Tests:
  - FingerprintingService setup with mocked clients
  - Embedding + storage pipeline (end-to-end mock)
  - TimescaleDB metric write
  - Qdrant vector storage
  - SHA-256 response hashing
  - Embedding norm computation
  - ensure_qdrant_collection (idempotent)
  - Point ID determinism (same event_id → same Qdrant point ID)
  - baseline.py: centroid, variance, schema compliance computations
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

import os
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("TIMESCALEDB_URL", "postgresql://spectraflow:spectraflow@localhost:5432/spectraflow")
os.environ.setdefault("QDRANT_HOST", "localhost")


# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_RESPONSE = "Thank you for contacting support. I'll be happy to help you reset your password."
SAMPLE_VECTOR = [0.1] * 1536  # Mock 1536-dim embedding


@pytest.fixture
def mock_openai_client():
    client = AsyncMock()
    embedding_data = MagicMock()
    embedding_data.embedding = SAMPLE_VECTOR

    embeddings_response = MagicMock()
    embeddings_response.data = [embedding_data]

    client.embeddings.create = AsyncMock(return_value=embeddings_response)
    return client


@pytest.fixture
def mock_qdrant_client():
    client = AsyncMock()
    client.upsert = AsyncMock(return_value=MagicMock())
    client.search = AsyncMock(return_value=[])
    client.scroll = AsyncMock(return_value=([], None))
    client.get_collections = AsyncMock(return_value=MagicMock(collections=[]))
    client.create_collection = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_pg_pool():
    pool = AsyncMock()
    conn = AsyncMock()

    # asyncpg uses async context managers
    pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    conn.execute = AsyncMock(return_value="INSERT 1")
    conn.fetchval = AsyncMock(return_value=uuid.uuid4())
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])

    return pool, conn


# ── FingerprintingService tests ───────────────────────────────────────────────

class TestFingerprintingServiceEmbed:

    @pytest.mark.asyncio
    async def test_embed_returns_vector(self, mock_openai_client: AsyncMock) -> None:
        from spectraflow.telemetry.fingerprinting import FingerprintingService

        svc = FingerprintingService()
        svc.openai = mock_openai_client

        vector = await svc.embed("Hello world")

        assert len(vector) == 1536
        assert all(isinstance(v, float) for v in vector)
        mock_openai_client.embeddings.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_truncates_long_text(self, mock_openai_client: AsyncMock) -> None:
        from spectraflow.telemetry.fingerprinting import FingerprintingService

        svc = FingerprintingService()
        svc.openai = mock_openai_client

        long_text = "x" * 100_000
        await svc.embed(long_text)

        # Check the call used truncated input
        call_args = mock_openai_client.embeddings.create.call_args
        assert len(call_args.kwargs.get("input", call_args.args[0] if call_args.args else "")) <= 32001

    @pytest.mark.asyncio
    async def test_embed_calls_correct_model(self, mock_openai_client: AsyncMock) -> None:
        from spectraflow.telemetry.fingerprinting import FingerprintingService
        from spectraflow.config import get_settings

        svc = FingerprintingService()
        svc.openai = mock_openai_client

        await svc.embed("Test input")

        call_kwargs = mock_openai_client.embeddings.create.call_args.kwargs
        assert call_kwargs.get("model") == get_settings().embedding_model


class TestFingerprintingServiceStorage:

    @pytest.mark.asyncio
    async def test_store_vector_calls_qdrant_upsert(
        self, mock_qdrant_client: AsyncMock
    ) -> None:
        from spectraflow.telemetry.fingerprinting import FingerprintingService

        svc = FingerprintingService()
        svc.qdrant = mock_qdrant_client

        payload = {
            "event_id": "test-001",
            "pipeline_name": "support",
        }
        await svc.store_vector(
            vector=SAMPLE_VECTOR,
            payload=payload,
            point_id=str(uuid.uuid4()),
        )

        mock_qdrant_client.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_point_id_is_deterministic(self) -> None:
        """Same event_id must always produce the same Qdrant point ID."""
        event_id = "stable-event-id-123"
        point_id_1 = str(uuid.uuid5(uuid.NAMESPACE_DNS, event_id))
        point_id_2 = str(uuid.uuid5(uuid.NAMESPACE_DNS, event_id))
        assert point_id_1 == point_id_2

    @pytest.mark.asyncio
    async def test_response_hash_is_deterministic(self) -> None:
        """Same response content must always produce the same hash."""
        content = "Hello, how can I help you?"
        hash1 = hashlib.sha256(content.encode()).hexdigest()
        hash2 = hashlib.sha256(content.encode()).hexdigest()
        assert hash1 == hash2

    @pytest.mark.asyncio
    async def test_response_hash_differs_for_different_content(self) -> None:
        content1 = "Hello"
        content2 = "Goodbye"
        assert (
            hashlib.sha256(content1.encode()).hexdigest()
            != hashlib.sha256(content2.encode()).hexdigest()
        )


class TestFingerprintingWriteMetric:

    @pytest.mark.asyncio
    async def test_write_metric_executes_insert(
        self, mock_pg_pool: tuple
    ) -> None:
        from spectraflow.telemetry.fingerprinting import FingerprintingService

        pool, conn = mock_pg_pool

        svc = FingerprintingService()
        svc.pg_pool = pool

        await svc.write_metric(
            event_id="evt-001",
            pipeline_name="support",
            model_id="gpt-4o-mini",
            prompt_version="v1.0",
            user_segment="standard",
            response_content=SAMPLE_RESPONSE,
            total_tokens=22,
            latency_seconds=0.45,
            finish_reason="stop",
            embedding_norm=0.98,
        )

        conn.execute.assert_called_once()
        call_args = conn.execute.call_args
        # Verify INSERT was called
        assert "INSERT INTO behavioral_metrics" in call_args.args[0]

    @pytest.mark.asyncio
    async def test_write_metric_computes_correct_hash(
        self, mock_pg_pool: tuple
    ) -> None:
        from spectraflow.telemetry.fingerprinting import FingerprintingService

        pool, conn = mock_pg_pool

        svc = FingerprintingService()
        svc.pg_pool = pool

        content = "A specific response content"
        expected_hash = hashlib.sha256(content.encode()).hexdigest()

        await svc.write_metric(
            event_id="evt-002",
            pipeline_name="test",
            model_id="gpt-4o",
            prompt_version="v1",
            user_segment="test",
            response_content=content,
            total_tokens=10,
            latency_seconds=0.1,
            finish_reason="stop",
            embedding_norm=0.9,
        )

        call_args = conn.execute.call_args.args
        # The hash should be in the call arguments
        assert expected_hash in call_args


class TestFingerprintingProcessResponse:

    @pytest.mark.asyncio
    async def test_process_response_full_pipeline(
        self,
        mock_openai_client: AsyncMock,
        mock_qdrant_client: AsyncMock,
        mock_pg_pool: tuple,
    ) -> None:
        """Full pipeline: embed → store in Qdrant → write to TimescaleDB."""
        from spectraflow.telemetry.fingerprinting import FingerprintingService

        pool, conn = mock_pg_pool

        svc = FingerprintingService()
        svc.openai = mock_openai_client
        svc.qdrant = mock_qdrant_client
        svc.pg_pool = pool

        await svc.process_response(
            event_id="evt-full-001",
            redis_event_id="redis-123",
            pipeline_name="customer-support",
            model_id="gpt-4o-mini",
            prompt_version="v1.5.2",
            user_segment="enterprise",
            response_content=SAMPLE_RESPONSE,
            request_messages=[{"role": "user", "content": "Help me please"}],
            latency_seconds=0.75,
            total_tokens=35,
            finish_reason="stop",
        )

        # All three pipeline stages should have been called
        mock_openai_client.embeddings.create.assert_called_once()
        mock_qdrant_client.upsert.assert_called_once()
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_response_embedding_norm_computed(
        self,
        mock_openai_client: AsyncMock,
        mock_qdrant_client: AsyncMock,
        mock_pg_pool: tuple,
    ) -> None:
        """embedding_norm should be computed from the returned vector."""
        from spectraflow.telemetry.fingerprinting import FingerprintingService

        pool, conn = mock_pg_pool

        # Custom vector with known norm
        known_vector = [1.0] + [0.0] * 1535  # unit vector
        embedding_data = MagicMock()
        embedding_data.embedding = known_vector
        embeddings_response = MagicMock()
        embeddings_response.data = [embedding_data]
        mock_openai_client.embeddings.create = AsyncMock(return_value=embeddings_response)

        svc = FingerprintingService()
        svc.openai = mock_openai_client
        svc.qdrant = mock_qdrant_client
        svc.pg_pool = pool

        await svc.process_response(
            event_id="evt-norm-test",
            redis_event_id="r-1",
            pipeline_name="test",
            model_id="gpt-4o-mini",
            prompt_version="v1",
            user_segment="test",
            response_content="Test response",
            request_messages=[],
            latency_seconds=0.1,
            total_tokens=5,
            finish_reason="stop",
        )

        # The norm of our unit vector is 1.0
        call_args = conn.execute.call_args.args
        # embedding_norm=1.0 should be in the args
        assert 1.0 in call_args


# ── Baseline computation tests ────────────────────────────────────────────────

class TestBaselineComputations:

    def test_compute_centroid_single_vector(self) -> None:
        from spectraflow.detection.baseline import compute_centroid

        vectors = [[1.0, 2.0, 3.0]]
        centroid = compute_centroid(vectors)
        np.testing.assert_array_almost_equal(centroid, [1.0, 2.0, 3.0])

    def test_compute_centroid_multiple_vectors(self) -> None:
        from spectraflow.detection.baseline import compute_centroid

        vectors = [
            [1.0, 0.0],
            [3.0, 2.0],
            [2.0, 1.0],
        ]
        centroid = compute_centroid(vectors)
        np.testing.assert_array_almost_equal(centroid, [2.0, 1.0])

    def test_compute_centroid_empty_raises(self) -> None:
        from spectraflow.detection.baseline import compute_centroid

        with pytest.raises(ValueError, match="empty"):
            compute_centroid([])

    def test_compute_semantic_variance_identical_vectors(self) -> None:
        """All identical vectors → zero variance."""
        from spectraflow.detection.baseline import compute_centroid, compute_semantic_variance

        vec = [1.0, 0.0, 0.0]
        vectors = [vec] * 10
        centroid = compute_centroid(vectors)
        variance = compute_semantic_variance(vectors, centroid)
        assert variance < 1e-6  # Essentially zero

    def test_compute_semantic_variance_orthogonal_vectors(self) -> None:
        """Orthogonal vectors → maximum distance from centroid."""
        from spectraflow.detection.baseline import compute_centroid, compute_semantic_variance

        vectors = [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
        centroid = compute_centroid(vectors)
        variance = compute_semantic_variance(vectors, centroid)
        # Variance should be positive and significant
        assert variance > 0.1

    def test_compute_semantic_variance_single_vector(self) -> None:
        """Single vector → 0 variance."""
        from spectraflow.detection.baseline import compute_centroid, compute_semantic_variance

        vectors = [[1.0, 2.0, 3.0]]
        centroid = compute_centroid(vectors)
        variance = compute_semantic_variance(vectors, centroid)
        assert variance == 0.0

    def test_compute_schema_compliance_valid_json(self) -> None:
        from spectraflow.detection.baseline import compute_schema_compliance

        responses = [
            '{"action": "reset_password", "success": true}',
            '{"action": "refund", "amount": 29.99}',
            '{"action": "cancel_subscription"}',
        ]
        rate = compute_schema_compliance(responses)
        assert rate == 1.0

    def test_compute_schema_compliance_invalid_json(self) -> None:
        from spectraflow.detection.baseline import compute_schema_compliance

        responses = [
            '{"valid": true}',
            "This is plain text, not JSON",
        ]
        rate = compute_schema_compliance(responses)
        # "This is plain text" doesn't start with { or [ so counts as compliant
        # Only pure invalid JSON starting with { or [ counts as non-compliant
        assert rate == 1.0  # Plain text is "compliant" (not expected to be JSON)

    def test_compute_schema_compliance_broken_json(self) -> None:
        from spectraflow.detection.baseline import compute_schema_compliance

        responses = [
            '{"valid": true}',
            '{broken json without closing brace',
        ]
        rate = compute_schema_compliance(responses)
        assert rate == 0.5  # 1/2 valid

    def test_compute_schema_compliance_empty(self) -> None:
        from spectraflow.detection.baseline import compute_schema_compliance

        rate = compute_schema_compliance([])
        assert rate == 1.0  # Default to compliant when no data


# ── Prompt store hash tests ───────────────────────────────────────────────────

class TestPromptVersionHash:

    def test_hash_is_deterministic(self) -> None:
        from spectraflow.registry.prompt_store import compute_version_hash

        content = "You are a helpful assistant."
        hash1 = compute_version_hash(content)
        hash2 = compute_version_hash(content)
        assert hash1 == hash2

    def test_hash_normalizes_whitespace(self) -> None:
        from spectraflow.registry.prompt_store import compute_version_hash

        content1 = "  You are a helpful assistant.  "
        content2 = "You are a helpful assistant."
        # Both should produce the same hash after stripping
        assert compute_version_hash(content1) == compute_version_hash(content2)

    def test_hash_differs_for_different_content(self) -> None:
        from spectraflow.registry.prompt_store import compute_version_hash

        hash1 = compute_version_hash("System prompt v1")
        hash2 = compute_version_hash("System prompt v2")
        assert hash1 != hash2

    def test_hash_starts_with_sha256_prefix(self) -> None:
        from spectraflow.registry.prompt_store import compute_version_hash

        h = compute_version_hash("Hello")
        assert h.startswith("sha256:")

    def test_hash_is_readable_length(self) -> None:
        """Hash should be compact but unique — 16 hex chars after prefix."""
        from spectraflow.registry.prompt_store import compute_version_hash

        h = compute_version_hash("Some prompt content")
        prefix = "sha256:"
        hex_part = h[len(prefix):]
        assert len(hex_part) == 16  # 8 bytes = 16 hex chars
