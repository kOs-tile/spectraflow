"""
Regression Test Synthesizer.

Learns golden regression test cases from production traffic.

Algorithm:
  1. Sample representative production inputs (diverse, covering edge cases)
  2. For each sampled input, call the LLM to generate an expected_behavior_description
     (not an exact output match — LLMs are stochastic. We test for behavioral properties.)
  3. Store (input_messages, expected_behavior, expected_keywords) in TimescaleDB
  4. On demand (or after incidents), run stored tests against the current model
     and score using semantic similarity

This approach is fundamentally different from snapshot testing — we test that
the model still exhibits the *behavioral properties* we care about, not
that it produces identical text.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Any

import asyncpg
import numpy as np
from loguru import logger
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from spectraflow.config import get_settings
from spectraflow.telemetry.fingerprinting import FingerprintingService

settings = get_settings()


# ── Data Models ───────────────────────────────────────────────────────────────

class GoldenTest(BaseModel):
    """A synthesized regression test case derived from production traffic."""

    id: str = ""
    pipeline_name: str
    input_messages: list[dict[str, str]]
    expected_behavior: str = Field(
        description="Natural language description of expected LLM behavior for this input"
    )
    expected_keywords: list[str] = Field(
        default_factory=list,
        description="Keywords/phrases that should (or should not) appear in the response",
    )
    expected_schema: dict[str, Any] | None = Field(
        default=None,
        description="Optional JSON schema the response should match",
    )
    source_incident_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True


class TestRunResult(BaseModel):
    """Result of running a single golden test."""

    test_id: str
    pipeline_name: str
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    actual_response: str = ""
    expected_behavior: str = ""
    failure_reason: str = ""
    run_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TestSuiteResult(BaseModel):
    """Aggregate result of running a test suite."""

    pipeline_name: str
    total_tests: int
    passed: int
    failed: int
    pass_rate: float
    results: list[TestRunResult]
    run_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Synthesizer ───────────────────────────────────────────────────────────────

class TestSynthesizer:
    """
    Synthesizes regression tests from production traffic and runs them
    against current or shadow LLM deployments.
    """

    SYNTHESIS_SYSTEM_PROMPT = """You are a QA engineer for an LLM application. 
Given a user input to an LLM pipeline, your task is to write a concise behavioral 
specification for what a correct response should look like.

Do NOT write the actual LLM response. Instead, describe what properties a good
response should have. Focus on:
- What information should be included
- What tone/style is appropriate  
- What the response should NOT contain
- Any format requirements

Output JSON with:
{
  "expected_behavior": "<2-3 sentence behavioral description>",
  "expected_keywords": ["<keyword1>", "<keyword2>"],
  "must_not_contain": ["<forbidden1>", "<forbidden2>"]
}"""

    SCORING_SYSTEM_PROMPT = """You are evaluating whether an LLM response meets a behavioral specification.

Rate the response from 0.0 to 1.0:
- 1.0: Fully meets the behavioral specification
- 0.7-0.9: Mostly meets it, minor issues
- 0.4-0.6: Partially meets it, significant gaps
- 0.1-0.3: Mostly fails to meet it
- 0.0: Completely fails or violates the specification

Output JSON: {"score": <0.0-1.0>, "reason": "<brief explanation>"}"""

    def __init__(self) -> None:
        self.openai: AsyncOpenAI | None = None
        self.pg_pool: asyncpg.Pool | None = None
        self.fingerprinter: FingerprintingService | None = None

    async def setup(self) -> None:
        self.openai = AsyncOpenAI(api_key=settings.openai_api_key)
        self.pg_pool = await asyncpg.create_pool(
            settings.timescaledb_url, min_size=1, max_size=3
        )
        self.fingerprinter = FingerprintingService()
        await self.fingerprinter.setup()

    async def teardown(self) -> None:
        if self.pg_pool:
            await self.pg_pool.close()
        if self.fingerprinter:
            await self.fingerprinter.teardown()

    # ── Input sampling ────────────────────────────────────────────────────────

    async def _sample_production_inputs(
        self,
        pipeline_name: str,
        n_samples: int | None = None,
        lookback_days: int = 7,
    ) -> list[dict[str, Any]]:
        """
        Sample representative production input messages from Redis Stream history.
        Uses TimescaleDB to find event IDs, then fetches message content.

        In production, messages are stored alongside metric rows via a separate
        messages table. Here we simulate sampling using metric metadata.
        """
        n_samples = n_samples or settings.test_synthesis_sample_size

        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT event_id, prompt_version, total_tokens
                FROM behavioral_metrics
                WHERE pipeline_name = $1
                  AND time > NOW() - ($2 || ' days')::INTERVAL
                ORDER BY RANDOM()
                LIMIT $3
                """,
                pipeline_name,
                str(lookback_days),
                n_samples * 2,  # oversample for filtering
            )

        return [dict(r) for r in rows]

    # ── Test synthesis ────────────────────────────────────────────────────────

    async def _synthesize_test_for_input(
        self,
        pipeline_name: str,
        input_messages: list[dict[str, str]],
        source_incident_id: str | None = None,
    ) -> GoldenTest | None:
        """
        Call LLM to generate behavioral specification for a given input.
        """
        input_preview = json.dumps(input_messages, indent=2)[:2000]

        response = await self.openai.chat.completions.create(
            model=settings.test_synthesis_model,
            messages=[
                {"role": "system", "content": self.SYNTHESIS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Pipeline: {pipeline_name}\n\nInput messages:\n{input_preview}",
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=500,
        )

        raw = response.choices[0].message.content
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Test synthesis returned non-JSON: {raw[:200]}")
            return None

        expected_behavior = spec.get("expected_behavior", "")
        if not expected_behavior:
            return None

        keywords = spec.get("expected_keywords", [])
        must_not = spec.get("must_not_contain", [])
        # Prefix forbidden keywords for distinction
        all_keywords = keywords + [f"NOT:{kw}" for kw in must_not]

        return GoldenTest(
            pipeline_name=pipeline_name,
            input_messages=input_messages,
            expected_behavior=expected_behavior,
            expected_keywords=all_keywords[:20],  # cap at 20 keywords
            source_incident_id=source_incident_id,
        )

    async def synthesize_tests(
        self,
        pipeline_name: str,
        n_tests: int | None = None,
        source_incident_id: str | None = None,
        sample_inputs: list[list[dict[str, str]]] | None = None,
    ) -> list[GoldenTest]:
        """
        Synthesize a batch of golden tests for a pipeline.

        Args:
            pipeline_name:       Target pipeline.
            n_tests:             Number of tests to generate. Default from settings.
            source_incident_id:  Link tests to a triggering incident.
            sample_inputs:       Pre-provided inputs. If None, samples from production.

        Returns:
            List of saved GoldenTest objects.
        """
        n_tests = n_tests or min(10, settings.test_synthesis_sample_size // 5)

        if sample_inputs is None:
            # Sample representative inputs
            production_rows = await self._sample_production_inputs(pipeline_name, n_tests)
            # Generate synthetic diverse inputs since we don't store raw messages in demo
            sample_inputs = self._generate_diverse_inputs(pipeline_name, n_tests)

        # Synthesize tests concurrently (with concurrency limit)
        import asyncio
        semaphore = asyncio.Semaphore(3)

        async def _bounded_synthesize(msgs: list[dict[str, str]]) -> GoldenTest | None:
            async with semaphore:
                return await self._synthesize_test_for_input(
                    pipeline_name=pipeline_name,
                    input_messages=msgs,
                    source_incident_id=source_incident_id,
                )

        tasks = [_bounded_synthesize(msgs) for msgs in sample_inputs[:n_tests]]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        tests: list[GoldenTest] = []
        for result in results:
            if isinstance(result, GoldenTest):
                saved = await self._save_test(result)
                tests.append(saved)
            elif isinstance(result, Exception):
                logger.warning(f"Test synthesis failed: {result}")

        logger.info(
            f"Synthesized {len(tests)} golden tests for pipeline '{pipeline_name}'",
            source_incident=source_incident_id,
        )
        return tests

    def _generate_diverse_inputs(
        self,
        pipeline_name: str,
        n: int,
    ) -> list[list[dict[str, str]]]:
        """
        Generate diverse synthetic input messages for test synthesis.
        In production, this would be replaced with actual sampled inputs.
        """
        templates = [
            [{"role": "user", "content": f"Help me understand how {pipeline_name} processes requests"}],
            [{"role": "user", "content": "What are the key features of this service?"}],
            [{"role": "user", "content": "Can you provide a detailed explanation of your capabilities?"}],
            [
                {"role": "system", "content": f"You are a helpful assistant for {pipeline_name}"},
                {"role": "user", "content": "Summarize the main points"},
            ],
            [{"role": "user", "content": "What should I know about edge cases in this pipeline?"}],
        ]
        return [templates[i % len(templates)] for i in range(n)]

    # ── Persistence ───────────────────────────────────────────────────────────

    async def _save_test(self, test: GoldenTest) -> GoldenTest:
        """Save a golden test to TimescaleDB."""
        async with self.pg_pool.acquire() as conn:
            test_id = await conn.fetchval(
                """
                INSERT INTO golden_tests (
                    pipeline_name, input_messages, expected_behavior,
                    expected_keywords, source_incident_id
                ) VALUES ($1, $2, $3, $4, $5::uuid)
                RETURNING id
                """,
                test.pipeline_name,
                json.dumps(test.input_messages),
                test.expected_behavior,
                json.dumps(test.expected_keywords),
                test.source_incident_id,
            )
        test.id = str(test_id)
        return test

    async def get_tests(
        self,
        pipeline_name: str | None = None,
        limit: int = 100,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Retrieve golden tests from TimescaleDB."""
        conditions = []
        params: list[Any] = []

        if pipeline_name:
            params.append(pipeline_name)
            conditions.append(f"pipeline_name = ${len(params)}")

        if active_only:
            conditions.append("is_active = TRUE")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, created_at, pipeline_name, input_messages,
                       expected_behavior, expected_keywords, source_incident_id,
                       last_run_at, last_run_passed, last_run_score, is_active
                FROM golden_tests
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ${len(params)}
                """,
                *params,
            )

        return [
            {
                **dict(r),
                "input_messages": json.loads(r["input_messages"]),
                "expected_keywords": json.loads(r["expected_keywords"]),
            }
            for r in rows
        ]

    # ── Test execution ────────────────────────────────────────────────────────

    async def _run_single_test(
        self,
        test: dict[str, Any],
        model: str | None = None,
        base_url: str | None = None,
    ) -> TestRunResult:
        """
        Execute a single golden test against the model.

        Scoring approach:
          1. Get model response for the test input
          2. Use LLM to score response against expected_behavior (0–1)
          3. Check keyword constraints
        """
        test_id = str(test["id"])
        pipeline_name = test["pipeline_name"]
        input_messages = test["input_messages"]
        expected_behavior = test["expected_behavior"]
        expected_keywords = test.get("expected_keywords", [])

        target_model = model or settings.test_synthesis_model
        target_base_url = base_url or settings.upstream_base_url

        # Create a separate client for the shadow/test target
        test_client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=target_base_url,
        )

        try:
            # 1. Get response from target model
            response = await test_client.chat.completions.create(
                model=target_model,
                messages=input_messages,
                temperature=0.0,  # Deterministic for testing
                max_tokens=1000,
            )
            actual_response = response.choices[0].message.content or ""

            # 2. Score using LLM judge
            score_response = await self.openai.chat.completions.create(
                model="gpt-4o-mini",  # Use cheaper model for scoring
                messages=[
                    {"role": "system", "content": self.SCORING_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Expected behavior: {expected_behavior}\n\n"
                            f"Actual response:\n{actual_response[:1000]}"
                        ),
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=150,
            )

            score_data = json.loads(score_response.choices[0].message.content)
            score = float(score_data.get("score", 0.5))
            reason = score_data.get("reason", "")

            # 3. Check keyword constraints
            for keyword in expected_keywords:
                if keyword.startswith("NOT:"):
                    forbidden = keyword[4:].lower()
                    if forbidden in actual_response.lower():
                        score = min(score, 0.3)
                        reason += f" [Forbidden keyword found: '{forbidden}']"
                else:
                    if keyword.lower() not in actual_response.lower():
                        score *= 0.9  # Small penalty for missing keywords

            passed = score >= settings.test_synthesis_min_score

            result = TestRunResult(
                test_id=test_id,
                pipeline_name=pipeline_name,
                passed=passed,
                score=round(min(1.0, max(0.0, score)), 3),
                actual_response=actual_response[:500],
                expected_behavior=expected_behavior,
                failure_reason="" if passed else reason,
            )

        except Exception as exc:
            logger.error(f"Test execution failed for test {test_id}: {exc}")
            result = TestRunResult(
                test_id=test_id,
                pipeline_name=pipeline_name,
                passed=False,
                score=0.0,
                failure_reason=str(exc),
                expected_behavior=expected_behavior,
            )

        # Update test record with last run result
        try:
            async with self.pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE golden_tests
                    SET last_run_at = NOW(), last_run_passed = $1, last_run_score = $2
                    WHERE id = $3::uuid
                    """,
                    result.passed,
                    result.score,
                    test_id,
                )
        except Exception as exc:
            logger.warning(f"Failed to update test record {test_id}: {exc}")

        return result

    async def run_test_suite(
        self,
        pipeline_name: str,
        model: str | None = None,
        base_url: str | None = None,
    ) -> TestSuiteResult:
        """
        Run all active golden tests for a pipeline.

        Args:
            pipeline_name: Target pipeline.
            model:         Model to test (overrides default).
            base_url:      Base URL for the model API (can point at SPECTRAFLOW proxy
                           or directly at the upstream).

        Returns:
            TestSuiteResult with pass/fail counts and per-test results.
        """
        import asyncio

        tests = await self.get_tests(pipeline_name=pipeline_name)

        if not tests:
            logger.info(f"No golden tests found for pipeline '{pipeline_name}'")
            return TestSuiteResult(
                pipeline_name=pipeline_name,
                total_tests=0,
                passed=0,
                failed=0,
                pass_rate=1.0,
                results=[],
            )

        # Run tests concurrently with rate limiting
        semaphore = asyncio.Semaphore(3)

        async def _bounded_run(test: dict[str, Any]) -> TestRunResult:
            async with semaphore:
                return await self._run_single_test(test, model=model, base_url=base_url)

        run_tasks = [_bounded_run(test) for test in tests]
        results: list[TestRunResult] = await asyncio.gather(*run_tasks, return_exceptions=False)

        passed_count = sum(1 for r in results if r.passed)
        total = len(results)
        pass_rate = passed_count / total if total > 0 else 1.0

        logger.info(
            f"Test suite complete for '{pipeline_name}'",
            total=total,
            passed=passed_count,
            pass_rate=round(pass_rate, 3),
        )

        from spectraflow.monitoring.metrics import REGRESSION_PASS_RATE, REGRESSION_TESTS_TOTAL
        REGRESSION_TESTS_TOTAL.labels(pipeline=pipeline_name).set(total)
        REGRESSION_PASS_RATE.labels(pipeline=pipeline_name).set(pass_rate)

        return TestSuiteResult(
            pipeline_name=pipeline_name,
            total_tests=total,
            passed=passed_count,
            failed=total - passed_count,
            pass_rate=round(pass_rate, 4),
            results=results,
        )
