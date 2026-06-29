"""
Versioned Prompt Registry.

Stores prompt templates with SHA-256 version hashes.
Maps inference requests to prompt versions for attribution in drift analysis.

This allows SPECTRAFLOW to answer: "Did the prompt change when drift started?"

Usage in your application:

    from spectraflow.registry.prompt_store import PromptStore

    store = PromptStore()
    await store.setup()

    # Register a prompt template
    version = await store.register_prompt(
        name="customer-support-system",
        pipeline_name="customer-support",
        content="You are a helpful customer support agent for Acme Corp...",
    )
    print(version.version_hash)  # sha256:3a4b5c...

    # In your API call, pass the hash as a header:
    headers = {"X-Prompt-Version": version.version_hash}

    # SPECTRAFLOW automatically attributes the inference to this version.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import asyncpg
from loguru import logger
from pydantic import BaseModel, Field

from spectraflow.config import get_settings

settings = get_settings()

_PROMPT_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    name            TEXT NOT NULL,
    pipeline_name   TEXT NOT NULL,
    version_hash    TEXT NOT NULL UNIQUE,
    content         TEXT NOT NULL,
    content_length  INTEGER NOT NULL,
    description     TEXT DEFAULT '',
    tags            JSONB NOT NULL DEFAULT '[]',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    deployed_at     TIMESTAMPTZ,
    deprecated_at   TIMESTAMPTZ,
    created_by      TEXT DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_prompt_versions_pipeline
    ON prompt_versions (pipeline_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_prompt_versions_hash
    ON prompt_versions (version_hash);

CREATE INDEX IF NOT EXISTS idx_prompt_versions_active
    ON prompt_versions (pipeline_name, is_active);
"""


class PromptVersion(BaseModel):
    """A registered prompt template version."""

    id: str = ""
    name: str
    pipeline_name: str
    version_hash: str
    content: str
    content_length: int
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deployed_at: datetime | None = None
    deprecated_at: datetime | None = None
    created_by: str = "system"


def compute_version_hash(content: str) -> str:
    """
    Compute a deterministic version hash for a prompt template.
    Uses SHA-256 of the normalized (stripped, consistent line endings) content.
    """
    normalized = content.strip().replace("\r\n", "\n")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"  # short prefix for readability


class PromptStore:
    """
    Manages versioned prompt templates with registration, retrieval, and
    inference-to-version attribution.
    """

    def __init__(self) -> None:
        self.pg_pool: asyncpg.Pool | None = None

    async def setup(self) -> None:
        self.pg_pool = await asyncpg.create_pool(
            settings.timescaledb_url, min_size=1, max_size=5
        )
        await self._ensure_schema()
        logger.info("PromptStore initialized")

    async def teardown(self) -> None:
        if self.pg_pool:
            await self.pg_pool.close()

    async def _ensure_schema(self) -> None:
        async with self.pg_pool.acquire() as conn:
            await conn.execute(_PROMPT_STORE_SCHEMA)

    # ── Registration ──────────────────────────────────────────────────────────

    async def register_prompt(
        self,
        name: str,
        pipeline_name: str,
        content: str,
        description: str = "",
        tags: list[str] | None = None,
        created_by: str = "system",
        deploy: bool = True,
    ) -> PromptVersion:
        """
        Register a prompt template. If an identical version already exists
        (same hash), returns the existing version without creating a duplicate.

        Args:
            name:          Human-readable name (e.g., "customer-support-system")
            pipeline_name: Pipeline this prompt belongs to
            content:       Full prompt template text
            description:   Optional description of changes
            tags:          Optional tags for categorization
            created_by:    Who registered this version
            deploy:        If True, marks this as the active version for the pipeline

        Returns:
            PromptVersion with computed version_hash
        """
        version_hash = compute_version_hash(content)
        tags = tags or []

        async with self.pg_pool.acquire() as conn:
            # Check if this exact version already exists
            existing = await conn.fetchrow(
                "SELECT * FROM prompt_versions WHERE version_hash = $1",
                version_hash,
            )

            if existing:
                logger.debug(
                    f"Prompt version already exists: {version_hash}",
                    name=name,
                    pipeline=pipeline_name,
                )
                return PromptVersion(
                    id=str(existing["id"]),
                    name=existing["name"],
                    pipeline_name=existing["pipeline_name"],
                    version_hash=existing["version_hash"],
                    content=existing["content"],
                    content_length=existing["content_length"],
                    description=existing["description"] or "",
                    tags=json.loads(existing["tags"]),
                    is_active=existing["is_active"],
                    created_at=existing["created_at"],
                )

            async with conn.transaction():
                # Deprecate old active version if deploying
                if deploy:
                    await conn.execute(
                        """
                        UPDATE prompt_versions
                        SET is_active = FALSE, deprecated_at = NOW()
                        WHERE pipeline_name = $1 AND is_active = TRUE
                        """,
                        pipeline_name,
                    )

                version_id = await conn.fetchval(
                    """
                    INSERT INTO prompt_versions (
                        name, pipeline_name, version_hash, content,
                        content_length, description, tags, is_active,
                        deployed_at, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    RETURNING id
                    """,
                    name,
                    pipeline_name,
                    version_hash,
                    content,
                    len(content),
                    description,
                    json.dumps(tags),
                    deploy,
                    datetime.now(timezone.utc) if deploy else None,
                    created_by,
                )

        logger.info(
            f"Registered prompt version",
            name=name,
            pipeline=pipeline_name,
            hash=version_hash,
            active=deploy,
        )

        return PromptVersion(
            id=str(version_id),
            name=name,
            pipeline_name=pipeline_name,
            version_hash=version_hash,
            content=content,
            content_length=len(content),
            description=description,
            tags=tags,
            is_active=deploy,
            deployed_at=datetime.now(timezone.utc) if deploy else None,
            created_by=created_by,
        )

    # ── Retrieval ─────────────────────────────────────────────────────────────

    async def get_active_version(
        self, pipeline_name: str
    ) -> PromptVersion | None:
        """Get the currently active prompt version for a pipeline."""
        async with self.pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM prompt_versions
                WHERE pipeline_name = $1 AND is_active = TRUE
                ORDER BY created_at DESC
                LIMIT 1
                """,
                pipeline_name,
            )

        if not row:
            return None

        return PromptVersion(
            id=str(row["id"]),
            name=row["name"],
            pipeline_name=row["pipeline_name"],
            version_hash=row["version_hash"],
            content=row["content"],
            content_length=row["content_length"],
            description=row["description"] or "",
            tags=json.loads(row["tags"]),
            is_active=row["is_active"],
            created_at=row["created_at"],
            deployed_at=row["deployed_at"],
            deprecated_at=row["deprecated_at"],
            created_by=row["created_by"] or "system",
        )

    async def get_version_by_hash(self, version_hash: str) -> PromptVersion | None:
        """Look up a prompt version by its hash."""
        async with self.pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM prompt_versions WHERE version_hash = $1",
                version_hash,
            )

        if not row:
            return None

        return PromptVersion(
            id=str(row["id"]),
            name=row["name"],
            pipeline_name=row["pipeline_name"],
            version_hash=row["version_hash"],
            content=row["content"],
            content_length=row["content_length"],
            description=row["description"] or "",
            tags=json.loads(row["tags"]),
            is_active=row["is_active"],
            created_at=row["created_at"],
        )

    async def get_version_history(
        self,
        pipeline_name: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return the version history for a pipeline, newest first."""
        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, created_at, name, pipeline_name, version_hash,
                       content_length, description, tags, is_active,
                       deployed_at, deprecated_at, created_by
                FROM prompt_versions
                WHERE pipeline_name = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                pipeline_name,
                limit,
            )

        return [
            {
                "id": str(r["id"]),
                "created_at": r["created_at"].isoformat(),
                "name": r["name"],
                "pipeline_name": r["pipeline_name"],
                "version_hash": r["version_hash"],
                "content_length": r["content_length"],
                "description": r["description"] or "",
                "tags": json.loads(r["tags"]),
                "is_active": r["is_active"],
                "deployed_at": r["deployed_at"].isoformat() if r["deployed_at"] else None,
                "deprecated_at": r["deprecated_at"].isoformat() if r["deprecated_at"] else None,
                "created_by": r["created_by"] or "system",
            }
            for r in rows
        ]

    async def list_pipelines(self) -> list[str]:
        """Return all pipeline names that have registered prompts."""
        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT pipeline_name FROM prompt_versions ORDER BY pipeline_name"
            )
        return [r["pipeline_name"] for r in rows]

    async def diff_versions(
        self, hash_before: str, hash_after: str
    ) -> dict[str, Any]:
        """
        Compute a structural diff between two prompt versions.
        Returns added/removed lines and character-level change stats.
        """
        v_before = await self.get_version_by_hash(hash_before)
        v_after = await self.get_version_by_hash(hash_after)

        if not v_before or not v_after:
            return {"error": "One or both versions not found"}

        import difflib

        diff = list(
            difflib.unified_diff(
                v_before.content.splitlines(keepends=True),
                v_after.content.splitlines(keepends=True),
                fromfile=f"version:{hash_before}",
                tofile=f"version:{hash_after}",
                n=3,
            )
        )

        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        char_change = abs(v_after.content_length - v_before.content_length)

        return {
            "version_before": hash_before,
            "version_after": hash_after,
            "lines_added": added,
            "lines_removed": removed,
            "char_delta": char_change,
            "similarity": round(
                difflib.SequenceMatcher(
                    None, v_before.content, v_after.content
                ).ratio(),
                4,
            ),
            "unified_diff": "".join(diff[:100]),  # Cap output
        }
