"""
SPECTRAFLOW configuration via Pydantic Settings.

All settings can be overridden via environment variables or .env file.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_name: str = "SPECTRAFLOW"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "text"] = "json"

    # ── Upstream LLM Provider ─────────────────────────────────────────────────
    openai_api_key: str = Field(default="", description="OpenAI API key")
    upstream_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    upstream_timeout: float = 120.0
    upstream_max_retries: int = 2
    proxy_passthrough_unknown: bool = True

    # ── Redis / Streams ───────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    redis_stream_key: str = "spectraflow:telemetry"
    redis_stream_group: str = "spectraflow-consumers"
    redis_stream_consumer: str = "consumer-1"
    redis_stream_batch_size: int = 100
    # Max length of the Redis stream before trimming (approx)
    redis_stream_max_len: int = 100_000

    # ── TimescaleDB ───────────────────────────────────────────────────────────
    timescaledb_url: str = "postgresql://spectraflow:spectraflow@localhost:5432/spectraflow"

    # ── Qdrant ────────────────────────────────────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "spectraflow_fingerprints"
    # Dimensionality of text-embedding-3-small
    qdrant_vector_size: int = 1536

    # ── Celery ────────────────────────────────────────────────────────────────
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ── CUSUM Drift Detection ─────────────────────────────────────────────────
    cusum_threshold_h: float = Field(
        default=5.0,
        description="CUSUM alarm threshold. Lower = more sensitive.",
        ge=0.1,
    )
    cusum_slack_k: float = Field(
        default=0.5,
        description="CUSUM allowable slack (reference value). Typically 0.5 * expected shift.",
        ge=0.0,
    )
    drift_monitor_interval: int = Field(
        default=300,
        description="Seconds between drift monitor Celery task runs.",
    )

    # ── Behavioral Baseline ───────────────────────────────────────────────────
    baseline_window_days: int = Field(
        default=7,
        description="Rolling window (days) for centroid/variance computation.",
        ge=1,
    )
    baseline_min_samples: int = Field(
        default=50,
        description="Minimum samples required before baseline is considered stable.",
        ge=1,
    )

    # ── Semantic Fingerprinting ───────────────────────────────────────────────
    embedding_model: str = "text-embedding-3-small"

    # ── Root Cause Agent ──────────────────────────────────────────────────────
    root_cause_model: str = "gpt-4o"
    root_cause_sample_size: int = 20

    # ── Regression Test Synthesizer ───────────────────────────────────────────
    test_synthesis_model: str = "gpt-4o"
    test_synthesis_sample_size: int = 50
    test_synthesis_min_score: float = 0.85

    # ── API Security ──────────────────────────────────────────────────────────
    spectraflow_api_key: str = ""

    @field_validator("log_level", mode="before")
    @classmethod
    def uppercase_log_level(cls, v: str) -> str:
        return v.upper()

    @property
    def timescaledb_async_url(self) -> str:
        """Convert postgresql:// to postgresql+asyncpg:// for async SQLAlchemy."""
        return self.timescaledb_url.replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )

    @property
    def has_openai_key(self) -> bool:
        return bool(self.openai_api_key and self.openai_api_key.startswith("sk-"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings singleton."""
    return Settings()
