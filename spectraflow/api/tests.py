"""
Regression test management API routes.

Endpoints:
  GET  /regression-tests              — list golden tests with filtering
  POST /regression-tests              — create a single golden test manually
  POST /regression-tests/synthesize   — trigger LLM synthesis from production traffic
  POST /regression-tests/run          — run test suite for a pipeline
  GET  /regression-tests/{id}         — get a specific test
  DELETE /regression-tests/{id}       — deactivate a test
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from spectraflow.config import get_settings

settings = get_settings()

router = APIRouter()


# ── Request Models ────────────────────────────────────────────────────────────

class CreateTestRequest(BaseModel):
    pipeline_name: str
    input_messages: list[dict[str, str]]
    expected_behavior: str
    expected_keywords: list[str] = Field(default_factory=list)
    source_incident_id: str | None = None


class SynthesizeTestsRequest(BaseModel):
    pipeline_name: str
    n_tests: int = Field(default=10, ge=1, le=50)
    source_incident_id: str | None = None


class RunTestsRequest(BaseModel):
    pipeline_name: str
    model: str | None = None
    base_url: str | None = None
    test_ids: list[str] | None = Field(
        default=None,
        description="Optional list of specific test IDs to run. If None, runs all active tests.",
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(settings.timescaledb_url, min_size=1, max_size=3)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/regression-tests")
async def list_regression_tests(
    pipeline: str | None = Query(default=None),
    active_only: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> JSONResponse:
    """
    List golden regression tests with optional pipeline filtering.
    """
    from spectraflow.agents.test_synthesizer import TestSynthesizer

    synthesizer = TestSynthesizer()
    await synthesizer.setup()

    try:
        tests = await synthesizer.get_tests(
            pipeline_name=pipeline,
            limit=page_size * page,
            active_only=active_only,
        )

        # Manual pagination
        start = (page - 1) * page_size
        page_tests = tests[start : start + page_size]

        return JSONResponse({
            "tests": [
                {
                    **test,
                    "id": str(test["id"]),
                    "created_at": test["created_at"].isoformat() if hasattr(test["created_at"], "isoformat") else test["created_at"],
                    "last_run_at": test["last_run_at"].isoformat() if test.get("last_run_at") else None,
                    "source_incident_id": str(test["source_incident_id"]) if test.get("source_incident_id") else None,
                }
                for test in page_tests
            ],
            "total": len(tests),
            "page": page,
            "page_size": page_size,
        })
    finally:
        await synthesizer.teardown()


@router.post("/regression-tests")
async def create_test(body: CreateTestRequest) -> JSONResponse:
    """
    Manually create a golden test case.
    """
    from spectraflow.agents.test_synthesizer import GoldenTest, TestSynthesizer

    synthesizer = TestSynthesizer()
    await synthesizer.setup()

    try:
        test = GoldenTest(
            pipeline_name=body.pipeline_name,
            input_messages=body.input_messages,
            expected_behavior=body.expected_behavior,
            expected_keywords=body.expected_keywords,
            source_incident_id=body.source_incident_id,
        )
        saved = await synthesizer._save_test(test)

        return JSONResponse(
            {
                "id": saved.id,
                "pipeline_name": saved.pipeline_name,
                "expected_behavior": saved.expected_behavior,
                "created_at": saved.created_at.isoformat(),
            },
            status_code=201,
        )
    finally:
        await synthesizer.teardown()


@router.post("/regression-tests/synthesize")
async def synthesize_tests(body: SynthesizeTestsRequest) -> JSONResponse:
    """
    Trigger LLM-powered synthesis of golden tests from production traffic.

    This is an async operation — returns immediately with a job acknowledgment.
    Tests are saved to the database as they are generated.
    """
    from spectraflow.agents.test_synthesizer import TestSynthesizer

    synthesizer = TestSynthesizer()
    await synthesizer.setup()

    try:
        tests = await synthesizer.synthesize_tests(
            pipeline_name=body.pipeline_name,
            n_tests=body.n_tests,
            source_incident_id=body.source_incident_id,
        )

        return JSONResponse(
            {
                "status": "synthesized",
                "pipeline_name": body.pipeline_name,
                "tests_created": len(tests),
                "test_ids": [t.id for t in tests],
            },
            status_code=201,
        )
    except Exception as exc:
        logger.error(f"Test synthesis failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await synthesizer.teardown()


@router.post("/regression-tests/run")
async def run_regression_tests(body: RunTestsRequest) -> JSONResponse:
    """
    Run the regression test suite for a pipeline.

    Optionally specify a model and base_url to test against a shadow model
    or a specific deployment. If base_url points at the SPECTRAFLOW proxy,
    test traffic will also be fingerprinted.
    """
    from spectraflow.agents.test_synthesizer import TestSynthesizer

    synthesizer = TestSynthesizer()
    await synthesizer.setup()

    try:
        suite_result = await synthesizer.run_test_suite(
            pipeline_name=body.pipeline_name,
            model=body.model,
            base_url=body.base_url,
        )

        return JSONResponse({
            "pipeline_name": suite_result.pipeline_name,
            "total_tests": suite_result.total_tests,
            "passed": suite_result.passed,
            "failed": suite_result.failed,
            "pass_rate": suite_result.pass_rate,
            "run_at": suite_result.run_at.isoformat(),
            "results": [
                {
                    "test_id": r.test_id,
                    "passed": r.passed,
                    "score": r.score,
                    "failure_reason": r.failure_reason,
                    "expected_behavior": r.expected_behavior[:200],
                }
                for r in suite_result.results
            ],
        })
    except Exception as exc:
        logger.error(f"Test run failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await synthesizer.teardown()


@router.get("/regression-tests/{test_id}")
async def get_test(test_id: str) -> JSONResponse:
    """Get a specific golden test by ID."""
    pool = await _get_pool()
    try:
        import json

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, created_at, pipeline_name, input_messages,
                       expected_behavior, expected_keywords, expected_schema,
                       source_incident_id, last_run_at, last_run_passed,
                       last_run_score, is_active
                FROM golden_tests
                WHERE id = $1::uuid
                """,
                test_id,
            )

        if not row:
            raise HTTPException(status_code=404, detail=f"Test {test_id} not found")

        return JSONResponse({
            "id": str(row["id"]),
            "created_at": row["created_at"].isoformat(),
            "pipeline_name": row["pipeline_name"],
            "input_messages": json.loads(row["input_messages"]),
            "expected_behavior": row["expected_behavior"],
            "expected_keywords": json.loads(row["expected_keywords"]),
            "expected_schema": json.loads(row["expected_schema"]) if row["expected_schema"] else None,
            "source_incident_id": str(row["source_incident_id"]) if row["source_incident_id"] else None,
            "last_run_at": row["last_run_at"].isoformat() if row["last_run_at"] else None,
            "last_run_passed": row["last_run_passed"],
            "last_run_score": row["last_run_score"],
            "is_active": row["is_active"],
        })
    finally:
        await pool.close()


@router.delete("/regression-tests/{test_id}")
async def deactivate_test(test_id: str) -> JSONResponse:
    """Deactivate (soft-delete) a golden test."""
    pool = await _get_pool()
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE golden_tests SET is_active = FALSE WHERE id = $1::uuid",
                test_id,
            )

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail=f"Test {test_id} not found")

        return JSONResponse({"status": "deactivated", "test_id": test_id})
    finally:
        await pool.close()
