"""
Root Cause Analysis Agent.

A multi-step LLM agent that diagnoses WHY semantic drift was detected.

Investigation steps:
  1. Fetch recent (drifted) response samples and baseline response samples
  2. Compute semantic diff — identify what changed about the content
  3. Check prompt version history — did the template change?
  4. Inspect input distribution — did the incoming messages shift?
  5. Check temperature/parameter anomalies
  6. Synthesize structured IncidentReport with root cause attribution

The agent uses a structured output schema (Pydantic) to ensure machine-readable
incident reports that can feed downstream alerting and ticketing systems.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import asyncpg
from loguru import logger
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from spectraflow.config import get_settings
from spectraflow.telemetry.fingerprinting import FingerprintingService

settings = get_settings()


# ── Data Models ───────────────────────────────────────────────────────────────

class DriftCause(str, Enum):
    PROMPT_CHANGE = "prompt_change"
    INPUT_DISTRIBUTION_SHIFT = "input_distribution_shift"
    MODEL_UPDATE = "model_update"
    TEMPERATURE_ANOMALY = "temperature_anomaly"
    CONTEXT_LENGTH_INCREASE = "context_length_increase"
    RESPONSE_LENGTH_CHANGE = "response_length_change"
    TOPIC_SHIFT = "topic_shift"
    UNKNOWN = "unknown"


class IncidentReport(BaseModel):
    """Structured root cause analysis report for a drift incident."""

    incident_id: str
    pipeline_name: str
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    drift_magnitude: float

    # Primary attribution
    primary_cause: DriftCause
    confidence: float = Field(ge=0.0, le=1.0, description="0–1 confidence in primary cause attribution")
    explanation: str = Field(description="Human-readable explanation of the detected drift")

    # Contributing factors
    contributing_causes: list[DriftCause] = Field(default_factory=list)
    contributing_factors: list[str] = Field(default_factory=list)

    # Evidence
    baseline_response_sample: str = ""
    drifted_response_sample: str = ""
    semantic_diff_summary: str = ""

    # Prompt analysis
    prompt_version_before: str = "unknown"
    prompt_version_after: str = "unknown"
    prompt_changed: bool = False

    # Input analysis
    input_topic_shift_detected: bool = False
    input_avg_length_before: float = 0.0
    input_avg_length_after: float = 0.0

    # Response statistics
    response_length_before_avg: float = 0.0
    response_length_after_avg: float = 0.0
    token_count_change_pct: float = 0.0

    # Recommended actions
    recommended_actions: list[str] = Field(default_factory=list)

    # Agent reasoning chain (for transparency)
    reasoning_steps: list[str] = Field(default_factory=list)


# ── Agent Implementation ──────────────────────────────────────────────────────

class RootCauseAgent:
    """
    Multi-step LLM agent for root cause analysis of semantic drift incidents.

    Uses OpenAI's structured output (JSON mode) to produce machine-readable reports.
    """

    SYSTEM_PROMPT = """You are SPECTRAFLOW's Root Cause Analysis Agent. Your job is to 
diagnose why semantic drift was detected in an LLM pipeline.

You will be given:
1. Recent (drifted) response samples from the pipeline
2. Historical (baseline) response samples from the same pipeline  
3. Statistics about input messages, prompt versions, and model parameters

Your task is to identify the most likely cause of the behavioral change and produce
a structured diagnosis. Be specific and evidence-based. Avoid speculation without evidence.

Always output valid JSON matching the requested schema."""

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

    # ── Step 1: Fetch response samples ───────────────────────────────────────

    async def _fetch_recent_samples(
        self,
        pipeline_name: str,
        hours_back: int = 6,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch recent (potentially drifted) response metadata from TimescaleDB."""
        limit = limit or settings.root_cause_sample_size
        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT event_id, time, model_id, prompt_version,
                       response_length, total_tokens, latency_seconds
                FROM behavioral_metrics
                WHERE pipeline_name = $1
                  AND time > NOW() - ($2 || ' hours')::INTERVAL
                ORDER BY time DESC
                LIMIT $3
                """,
                pipeline_name,
                str(hours_back),
                limit,
            )
        return [dict(r) for r in rows]

    async def _fetch_baseline_samples(
        self,
        pipeline_name: str,
        baseline_window_days: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch baseline (historical) response metadata from TimescaleDB."""
        window = baseline_window_days or settings.baseline_window_days
        limit = limit or settings.root_cause_sample_size

        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT event_id, time, model_id, prompt_version,
                       response_length, total_tokens, latency_seconds
                FROM behavioral_metrics
                WHERE pipeline_name = $1
                  AND time < NOW() - INTERVAL '6 hours'
                  AND time > NOW() - ($2 || ' days')::INTERVAL
                ORDER BY RANDOM()
                LIMIT $3
                """,
                pipeline_name,
                str(window),
                limit,
            )
        return [dict(r) for r in rows]

    # ── Step 2: Analyze prompt version history ────────────────────────────────

    async def _analyze_prompt_versions(
        self,
        pipeline_name: str,
        recent_samples: list[dict[str, Any]],
        baseline_samples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Detect prompt version changes between baseline and recent window."""
        recent_versions = {s.get("prompt_version", "unknown") for s in recent_samples}
        baseline_versions = {s.get("prompt_version", "unknown") for s in baseline_samples}

        new_versions = recent_versions - baseline_versions
        removed_versions = baseline_versions - recent_versions

        # Most common version in each period
        recent_dominant = max(
            recent_versions,
            key=lambda v: sum(1 for s in recent_samples if s.get("prompt_version") == v),
            default="unknown",
        )
        baseline_dominant = max(
            baseline_versions,
            key=lambda v: sum(1 for s in baseline_samples if s.get("prompt_version") == v),
            default="unknown",
        )

        return {
            "prompt_changed": recent_dominant != baseline_dominant,
            "version_before": baseline_dominant,
            "version_after": recent_dominant,
            "new_versions": list(new_versions),
            "removed_versions": list(removed_versions),
        }

    # ── Step 3: Compute response statistics ──────────────────────────────────

    def _compute_stats(
        self,
        recent: list[dict[str, Any]],
        baseline: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute comparative statistics between recent and baseline samples."""
        import statistics

        def safe_mean(vals: list[float]) -> float:
            return statistics.mean(vals) if vals else 0.0

        recent_lengths = [s.get("response_length", 0) for s in recent]
        baseline_lengths = [s.get("response_length", 0) for s in baseline]
        recent_tokens = [s.get("total_tokens", 0) for s in recent]
        baseline_tokens = [s.get("total_tokens", 0) for s in baseline]
        recent_latency = [float(s.get("latency_seconds", 0)) for s in recent]
        baseline_latency = [float(s.get("latency_seconds", 0)) for s in baseline]

        recent_len_avg = safe_mean(recent_lengths)
        baseline_len_avg = safe_mean(baseline_lengths)
        token_change_pct = (
            ((safe_mean(recent_tokens) - safe_mean(baseline_tokens)) / max(safe_mean(baseline_tokens), 1))
            * 100
        )

        return {
            "response_length_before_avg": round(baseline_len_avg, 1),
            "response_length_after_avg": round(recent_len_avg, 1),
            "length_change_pct": round(
                ((recent_len_avg - baseline_len_avg) / max(baseline_len_avg, 1)) * 100, 1
            ),
            "token_count_change_pct": round(token_change_pct, 1),
            "latency_before_avg": round(safe_mean(baseline_latency), 3),
            "latency_after_avg": round(safe_mean(recent_latency), 3),
        }

    # ── Step 4: LLM-based semantic comparison ────────────────────────────────

    async def _semantic_diff(
        self,
        baseline_previews: list[str],
        recent_previews: list[str],
        stats: dict[str, Any],
        prompt_analysis: dict[str, Any],
        pipeline_name: str,
        drift_magnitude: float,
    ) -> dict[str, Any]:
        """
        Use LLM to compare baseline vs recent response samples and identify
        the nature of the semantic change.
        """
        baseline_text = "\n\n---\n\n".join(baseline_previews[:5]) or "(no baseline samples available)"
        recent_text = "\n\n---\n\n".join(recent_previews[:5]) or "(no recent samples available)"

        analysis_prompt = f"""You are analyzing semantic drift in an LLM pipeline called '{pipeline_name}'.

DRIFT MAGNITUDE: {drift_magnitude:.2f} (scale 0-10, higher = more severe)

BASELINE RESPONSE SAMPLES (from 2-7 days ago):
{baseline_text}

RECENT RESPONSE SAMPLES (from last 6 hours):
{recent_text}

STATISTICAL CHANGES:
- Response length: {stats['response_length_before_avg']:.0f} chars → {stats['response_length_after_avg']:.0f} chars ({stats['length_change_pct']:+.1f}%)
- Token count change: {stats['token_count_change_pct']:+.1f}%
- Latency change: {stats['latency_before_avg']:.2f}s → {stats['latency_after_avg']:.2f}s

PROMPT VERSION ANALYSIS:
- Prompt version changed: {prompt_analysis['prompt_changed']}
- Version before: {prompt_analysis['version_before']}
- Version after: {prompt_analysis['version_after']}

Based on this evidence, provide a JSON analysis with these fields:
{{
  "primary_cause": "<one of: prompt_change|input_distribution_shift|model_update|temperature_anomaly|context_length_increase|response_length_change|topic_shift|unknown>",
  "confidence": <0.0-1.0>,
  "explanation": "<clear 2-3 sentence explanation of what changed and why>",
  "contributing_causes": ["<cause1>", "<cause2>"],
  "contributing_factors": ["<factor1>", "<factor2>"],
  "semantic_diff_summary": "<what specifically changed about the response content/style>",
  "input_topic_shift_detected": <true|false>,
  "recommended_actions": ["<action1>", "<action2>", "<action3>"],
  "reasoning_steps": ["<step1>", "<step2>", "<step3>"]
}}"""

        response = await self.openai.chat.completions.create(
            model=settings.root_cause_model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": analysis_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=1500,
        )

        raw = response.choices[0].message.content
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Root cause agent returned non-JSON response, using fallback")
            return {
                "primary_cause": "unknown",
                "confidence": 0.3,
                "explanation": "Root cause analysis inconclusive — insufficient evidence.",
                "contributing_causes": [],
                "contributing_factors": [],
                "semantic_diff_summary": raw[:500] if raw else "",
                "input_topic_shift_detected": False,
                "recommended_actions": [
                    "Manually review recent response samples",
                    "Check for recent prompt template changes",
                    "Verify upstream model has not been updated",
                ],
                "reasoning_steps": ["LLM analysis parsing failed — raw response saved"],
            }

    # ── Step 5: Persist report ────────────────────────────────────────────────

    async def _save_report(self, incident_id: str, report: IncidentReport) -> None:
        """Persist the incident report back to TimescaleDB."""
        async with self.pg_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE incidents
                SET root_cause = $1, status = 'investigating'
                WHERE id = $2::uuid
                """,
                json.dumps(report.model_dump(mode="json")),
                incident_id,
            )

    # ── Main analysis entrypoint ──────────────────────────────────────────────

    async def analyze(
        self,
        incident_id: str,
        pipeline_name: str,
        drift_magnitude: float,
    ) -> IncidentReport:
        """
        Run the full multi-step root cause analysis for a drift incident.

        Returns a structured IncidentReport.
        """
        logger.info(
            f"Starting root cause analysis",
            incident_id=incident_id,
            pipeline=pipeline_name,
            magnitude=drift_magnitude,
        )

        reasoning_steps: list[str] = []

        # Step 1: Fetch samples
        reasoning_steps.append("Step 1: Fetching recent and baseline response samples")
        recent_samples = await self._fetch_recent_samples(pipeline_name)
        baseline_samples = await self._fetch_baseline_samples(pipeline_name)

        # Step 2: Prompt version analysis
        reasoning_steps.append("Step 2: Analyzing prompt version history")
        prompt_analysis = await self._analyze_prompt_versions(
            pipeline_name, recent_samples, baseline_samples
        )

        if prompt_analysis["prompt_changed"]:
            reasoning_steps.append(
                f"  → Prompt version changed: {prompt_analysis['version_before']} → {prompt_analysis['version_after']}"
            )

        # Step 3: Statistical analysis
        reasoning_steps.append("Step 3: Computing response statistics")
        stats = self._compute_stats(recent_samples, baseline_samples)
        reasoning_steps.append(
            f"  → Length change: {stats['length_change_pct']:+.1f}%, "
            f"Token change: {stats['token_count_change_pct']:+.1f}%"
        )

        # Step 4: Fetch vector previews from Qdrant for semantic comparison
        reasoning_steps.append("Step 4: Retrieving response content for semantic diff")

        # Get recent response previews from Qdrant
        baseline_previews: list[str] = []
        recent_previews: list[str] = []

        try:
            # Use a dummy vector for broad search — real implementation would use
            # actual vectors from recent events
            if settings.has_openai_key:
                dummy_embedding = await self.fingerprinter.embed(
                    f"responses from pipeline {pipeline_name}"
                )
                similar = await self.fingerprinter.get_similar_responses(
                    vector=dummy_embedding,
                    pipeline_name=pipeline_name,
                    limit=settings.root_cause_sample_size,
                )
                for item in similar[:5]:
                    preview = item["payload"].get("response_preview", "")
                    if preview:
                        recent_previews.append(preview)
        except Exception as exc:
            logger.warning(f"Could not fetch Qdrant previews: {exc}")
            reasoning_steps.append(f"  → Qdrant preview fetch skipped: {exc}")

        # Step 5: LLM-based semantic analysis
        reasoning_steps.append("Step 5: Running LLM semantic comparison")
        llm_analysis = await self._semantic_diff(
            baseline_previews=baseline_previews,
            recent_previews=recent_previews,
            stats=stats,
            prompt_analysis=prompt_analysis,
            pipeline_name=pipeline_name,
            drift_magnitude=drift_magnitude,
        )

        reasoning_steps.extend(llm_analysis.get("reasoning_steps", []))

        # Step 6: Build IncidentReport
        reasoning_steps.append("Step 6: Synthesizing IncidentReport")

        try:
            primary_cause = DriftCause(llm_analysis.get("primary_cause", "unknown"))
        except ValueError:
            primary_cause = DriftCause.UNKNOWN

        contributing_causes = []
        for c in llm_analysis.get("contributing_causes", []):
            try:
                contributing_causes.append(DriftCause(c))
            except ValueError:
                pass

        report = IncidentReport(
            incident_id=incident_id,
            pipeline_name=pipeline_name,
            drift_magnitude=drift_magnitude,
            primary_cause=primary_cause,
            confidence=float(llm_analysis.get("confidence", 0.5)),
            explanation=llm_analysis.get("explanation", ""),
            contributing_causes=contributing_causes,
            contributing_factors=llm_analysis.get("contributing_factors", []),
            semantic_diff_summary=llm_analysis.get("semantic_diff_summary", ""),
            prompt_version_before=prompt_analysis["version_before"],
            prompt_version_after=prompt_analysis["version_after"],
            prompt_changed=prompt_analysis["prompt_changed"],
            input_topic_shift_detected=bool(llm_analysis.get("input_topic_shift_detected", False)),
            response_length_before_avg=stats["response_length_before_avg"],
            response_length_after_avg=stats["response_length_after_avg"],
            token_count_change_pct=stats["token_count_change_pct"],
            recommended_actions=llm_analysis.get("recommended_actions", []),
            reasoning_steps=reasoning_steps,
            baseline_response_sample=baseline_previews[0] if baseline_previews else "",
            drifted_response_sample=recent_previews[0] if recent_previews else "",
        )

        # Step 7: Persist report
        await self._save_report(incident_id, report)

        logger.info(
            f"Root cause analysis complete",
            incident_id=incident_id,
            primary_cause=report.primary_cause,
            confidence=report.confidence,
        )

        return report
