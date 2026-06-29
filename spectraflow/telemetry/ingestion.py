"""
Redis Stream consumer for SPECTRAFLOW telemetry events.

Reads events from the Redis Stream, enriches them with pipeline metadata,
and routes them to the fingerprinting service for embedding + storage.

Run as a standalone process:
    python -m spectraflow.telemetry.ingestion
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from typing import Any

import redis.asyncio as aioredis
from loguru import logger

from spectraflow.config import get_settings
from spectraflow.telemetry.fingerprinting import FingerprintingService

settings = get_settings()


class TelemetryIngestionWorker:
    """
    Consumes telemetry events from a Redis Stream and processes them
    through the FingerprintingService.

    Uses Redis consumer groups for at-least-once delivery semantics.
    Pending messages (unacknowledged) are reclaimed after a timeout.
    """

    PENDING_RECLAIM_TIMEOUT_MS: int = 30_000  # 30 seconds
    PENDING_RECLAIM_INTERVAL_S: float = 60.0  # Check every 60 seconds

    def __init__(self) -> None:
        self.redis: aioredis.Redis | None = None
        self.fingerprinter: FingerprintingService | None = None
        self._running = False
        self._last_pending_check: float = 0.0

    async def setup(self) -> None:
        """Initialize Redis connection and ensure stream + consumer group exist."""
        self.redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        self.fingerprinter = FingerprintingService()
        await self.fingerprinter.setup()

        # Ensure consumer group exists (MKSTREAM creates stream if missing)
        try:
            await self.redis.xgroup_create(
                settings.redis_stream_key,
                settings.redis_stream_group,
                id="0",
                mkstream=True,
            )
            logger.info(
                f"Created consumer group '{settings.redis_stream_group}'",
                stream=settings.redis_stream_key,
            )
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                logger.debug("Consumer group already exists")
            else:
                raise

    async def _process_event(self, event_id: str, raw_data: dict[str, str]) -> None:
        """
        Deserialize and enrich a single telemetry event, then route to fingerprinting.
        """
        try:
            # Deserialize JSON fields that were flattened for Redis XADD
            data: dict[str, Any] = {}
            for key, value in raw_data.items():
                try:
                    # Try to parse as JSON (handles lists/dicts)
                    parsed = json.loads(value)
                    data[key] = parsed
                except (json.JSONDecodeError, TypeError):
                    data[key] = value

            # Enrich with processing metadata
            data["redis_event_id"] = event_id
            data["ingested_at"] = asyncio.get_event_loop().time()

            # Only fingerprint chat completions with non-empty responses
            event_type = data.get("event_type", "")
            response_content = data.get("response_content", "")

            if event_type != "chat_completion" or not response_content:
                logger.debug(
                    f"Skipping event: type={event_type}, has_content={bool(response_content)}",
                    event_id=event_id,
                )
                return

            pipeline_name = data.get("pipeline_name", "default")
            model_id = data.get("model_id", "unknown")
            prompt_version = data.get("prompt_version", "unknown")
            user_segment = data.get("user_segment", "unknown")

            logger.debug(
                "Processing telemetry event",
                pipeline=pipeline_name,
                model=model_id,
                content_length=len(response_content),
            )

            # Route to fingerprinting service
            await self.fingerprinter.process_response(
                event_id=data.get("event_id", event_id),
                redis_event_id=event_id,
                pipeline_name=pipeline_name,
                model_id=model_id,
                prompt_version=prompt_version,
                user_segment=user_segment,
                response_content=response_content,
                request_messages=data.get("request_messages", []),
                latency_seconds=float(data.get("latency_seconds", 0.0)),
                total_tokens=int(data.get("total_tokens", 0)),
                finish_reason=data.get("finish_reason", "unknown"),
            )

        except Exception as exc:
            logger.error(
                f"Failed to process event {event_id}: {exc}",
                exc_info=True,
            )

    async def _reclaim_pending(self) -> None:
        """
        Reclaim messages that were delivered but not acknowledged (e.g. after a crash).
        Uses XAUTOCLAIM for Redis 7+ efficiency.
        """
        import time
        now = time.monotonic()
        if now - self._last_pending_check < self.PENDING_RECLAIM_INTERVAL_S:
            return
        self._last_pending_check = now

        try:
            # XAUTOCLAIM: take ownership of pending messages older than timeout
            results = await self.redis.xautoclaim(
                settings.redis_stream_key,
                settings.redis_stream_group,
                settings.redis_stream_consumer,
                min_idle_time=self.PENDING_RECLAIM_TIMEOUT_MS,
                start_id="0-0",
                count=settings.redis_stream_batch_size,
            )
            # results = (next_start_id, [(id, data), ...], [deleted_ids])
            reclaimed = results[1] if isinstance(results, (list, tuple)) and len(results) > 1 else []
            if reclaimed:
                logger.info(f"Reclaimed {len(reclaimed)} pending messages")
                for msg_id, data in reclaimed:
                    await self._process_event(msg_id, data)
                    await self.redis.xack(
                        settings.redis_stream_key,
                        settings.redis_stream_group,
                        msg_id,
                    )
        except Exception as exc:
            logger.warning(f"Pending reclaim failed: {exc}")

    async def run(self) -> None:
        """
        Main consumer loop.
        Continuously reads from the stream and processes events.
        """
        self._running = True
        logger.info(
            "Telemetry ingestion worker started",
            stream=settings.redis_stream_key,
            group=settings.redis_stream_group,
            consumer=settings.redis_stream_consumer,
        )

        last_id = ">"  # ">" means: only new, undelivered messages

        while self._running:
            try:
                # Periodically reclaim pending messages
                await self._reclaim_pending()

                # Block-read up to batch_size new messages, timeout 2s
                results = await self.redis.xreadgroup(
                    groupname=settings.redis_stream_group,
                    consumername=settings.redis_stream_consumer,
                    streams={settings.redis_stream_key: last_id},
                    count=settings.redis_stream_batch_size,
                    block=2000,  # 2 second timeout
                )

                if not results:
                    continue

                # results: [(stream_key, [(msg_id, data), ...])]
                for _stream_key, messages in results:
                    tasks = []
                    message_ids = []

                    for msg_id, data in messages:
                        message_ids.append(msg_id)
                        tasks.append(self._process_event(msg_id, data))

                    # Process batch concurrently
                    await asyncio.gather(*tasks, return_exceptions=True)

                    # Acknowledge all processed messages
                    if message_ids:
                        await self.redis.xack(
                            settings.redis_stream_key,
                            settings.redis_stream_group,
                            *message_ids,
                        )
                        logger.debug(f"Acknowledged {len(message_ids)} messages")

            except aioredis.ConnectionError as exc:
                logger.error(f"Redis connection lost: {exc}. Retrying in 5s...")
                await asyncio.sleep(5)
            except Exception as exc:
                logger.error(f"Consumer loop error: {exc}", exc_info=True)
                await asyncio.sleep(1)

    async def stop(self) -> None:
        """Graceful shutdown."""
        logger.info("Stopping telemetry ingestion worker...")
        self._running = False
        if self.redis:
            await self.redis.aclose()


# ── Standalone entry point ────────────────────────────────────────────────────

async def _main() -> None:
    worker = TelemetryIngestionWorker()
    await worker.setup()

    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        logger.info("Received shutdown signal")
        asyncio.create_task(worker.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(0)
