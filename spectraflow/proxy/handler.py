"""
OpenAI-compatible proxy handler.

Intercepts LLM API calls, forwards them to the real upstream provider,
and asynchronously emits telemetry events to the Redis Streams bus.
The telemetry emission is fire-and-forget — it never blocks the response.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import Any, AsyncGenerator

import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse
from loguru import logger

from spectraflow.config import get_settings
from spectraflow.monitoring.metrics import (
    REQUEST_COUNTER,
    REQUEST_LATENCY,
    UPSTREAM_LATENCY,
)

settings = get_settings()


def _content_telemetry(
    request_messages: list[dict[str, Any]],
    response_content: str,
) -> dict[str, Any]:
    """Apply the configured content-retention policy before telemetry leaves the proxy."""
    request_json = json.dumps(request_messages, sort_keys=True, separators=(",", ":"))
    payload: dict[str, Any] = {
        "telemetry_content_mode": settings.telemetry_content_mode,
        "request_messages_sha256": hashlib.sha256(request_json.encode()).hexdigest(),
        "response_content_sha256": hashlib.sha256(response_content.encode()).hexdigest(),
        "request_messages": [],
        "response_content": "",
    }
    if settings.telemetry_content_mode == "response":
        payload["response_content"] = response_content
    elif settings.telemetry_content_mode == "full":
        payload["request_messages"] = request_messages
        payload["response_content"] = response_content
    return payload


# ── Redis client (lazy-initialized) ──────────────────────────────────────────

_redis_client: Any | None = None


async def _get_redis() -> Any:
    """Lazy-initialize async Redis client."""
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis

        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


# ── Telemetry emission ────────────────────────────────────────────────────────


async def _emit_telemetry(event: dict[str, Any]) -> None:
    """
    Fire-and-forget telemetry event into Redis Stream.
    Errors are logged but never propagate to the caller.
    """
    try:
        r = await _get_redis()
        # Serialize nested dicts to JSON strings for Redis XADD compatibility
        flat_event = {
            k: json.dumps(v) if isinstance(v, (dict, list)) else str(v)
            for k, v in event.items()
        }
        await r.xadd(
            settings.redis_stream_key,
            flat_event,
            maxlen=settings.redis_stream_max_len,
            approximate=True,
        )
        logger.debug(
            "Telemetry emitted",
            event_id=event.get("event_id"),
            pipeline=event.get("pipeline_name"),
        )
    except Exception as exc:
        logger.error(f"Failed to emit telemetry: {exc}")


# ── Provider routing ──────────────────────────────────────────────────────────


def _resolve_upstream(request_model: str | None) -> tuple[str, str]:
    """
    Determine upstream base URL and API key from the model name.
    Returns (base_url, api_key).
    """
    if request_model:
        if request_model.startswith("claude-"):
            # Claude requires an explicitly configured OpenAI-compatible gateway.
            # Do not silently send an Anthropic credential to an unrelated provider.
            if not settings.anthropic_compatible_base_url:
                raise ValueError(
                    "Claude routing is disabled until ANTHROPIC_COMPATIBLE_BASE_URL is configured"
                )
            return settings.anthropic_compatible_base_url, settings.anthropic_api_key
        if request_model.startswith("deepseek"):
            return settings.deepseek_base_url, settings.deepseek_api_key

    return settings.upstream_base_url, settings.openai_api_key


# ── Pipeline metadata extraction ─────────────────────────────────────────────


def _extract_metadata(
    request: Request, body: dict[str, Any]
) -> dict[str, str]:
    """
    Extract pipeline metadata from request headers and body.

    Clients can tag requests with custom headers:
      X-Pipeline-Name: my-customer-support-bot
      X-Prompt-Version: v2.3.1
      X-User-Segment: enterprise
    """
    return {
        "pipeline_name": request.headers.get(
            "x-pipeline-name",
            request.headers.get("x-spectraflow-pipeline", "default"),
        ),
        "prompt_version": request.headers.get("x-prompt-version", "unknown"),
        "user_segment": request.headers.get("x-user-segment", "unknown"),
        "model_id": body.get("model", "unknown"),
        "kcc_capsule_id": request.headers.get("x-kcc-capsule-id", ""),
        "kcc_capability_id": request.headers.get("x-kcc-capability-id", ""),
        "kcc_operation": request.headers.get("x-kcc-operation", ""),
    }


# ── Streaming proxy ───────────────────────────────────────────────────────────


async def _stream_upstream(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    event_id: str,
    metadata: dict[str, str],
    start_time: float,
) -> AsyncGenerator[bytes, None]:
    """
    Async generator that streams SSE chunks from upstream,
    reassembles the full response for telemetry, and yields each chunk.
    """
    full_content_parts: list[str] = []
    total_tokens = 0
    finish_reason = "unknown"
    upstream_start = time.perf_counter()

    async with client.stream(
        method,
        url,
        headers=headers,
        json=body,
        timeout=settings.upstream_timeout,
    ) as response:
        upstream_latency = time.perf_counter() - upstream_start
        UPSTREAM_LATENCY.labels(
            pipeline=metadata["pipeline_name"],
            model=metadata["model_id"],
        ).observe(upstream_latency)

        async for chunk in response.aiter_bytes():
            yield chunk
            # Parse SSE chunks to reconstruct content
            try:
                text = chunk.decode("utf-8", errors="ignore")
                for line in text.splitlines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        data = json.loads(line[6:])
                        delta = (
                            data.get("choices", [{}])[0]
                            .get("delta", {})
                            .get("content", "")
                        )
                        if delta:
                            full_content_parts.append(delta)
                        finish_reason = (
                            data.get("choices", [{}])[0].get("finish_reason")
                            or finish_reason
                        )
                        usage = data.get("usage", {})
                        if usage:
                            total_tokens = usage.get("total_tokens", 0)
            except Exception:
                pass  # Non-JSON SSE chunks are fine

    # After streaming completes, emit telemetry
    full_content = "".join(full_content_parts)
    total_latency = time.perf_counter() - start_time

    REQUEST_LATENCY.labels(
        pipeline=metadata["pipeline_name"],
        model=metadata["model_id"],
    ).observe(total_latency)

    asyncio.create_task(
        _emit_telemetry(
            {
                "event_id": event_id,
                "event_type": "chat_completion",
                "pipeline_name": metadata["pipeline_name"],
                "prompt_version": metadata["prompt_version"],
                "user_segment": metadata["user_segment"],
                "model_id": metadata["model_id"],
                "kcc_capsule_id": metadata["kcc_capsule_id"],
                "kcc_capability_id": metadata["kcc_capability_id"],
                "kcc_operation": metadata["kcc_operation"],
                **_content_telemetry(body.get("messages", []), full_content),
                "finish_reason": finish_reason,
                "total_tokens": total_tokens,
                "latency_seconds": total_latency,
                "upstream_latency_seconds": upstream_latency,
                "stream": True,
            }
        )
    )


# ── Main proxy handler ────────────────────────────────────────────────────────


async def proxy_chat_completions(request: Request) -> Response:
    """
    Proxy handler for POST /v1/chat/completions.

    Flow:
      1. Parse incoming request body
      2. Resolve upstream URL + API key
      3. Forward request to upstream
      4. Intercept response
      5. Fire-and-forget telemetry event to Redis stream
      6. Return response to caller
    """
    event_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    # 1. Parse request body
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning(f"Failed to parse request body: {exc}")
        return Response(
            content=json.dumps({"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}}),
            status_code=400,
            media_type="application/json",
        )

    # 2. Resolve upstream. Unsupported/unconfigured provider routes fail closed.
    try:
        upstream_base_url, api_key = _resolve_upstream(body.get("model"))
    except ValueError as exc:
        return Response(
            content=json.dumps({
                "error": {
                    "message": str(exc),
                    "type": "provider_configuration_error",
                }
            }),
            status_code=400,
            media_type="application/json",
        )
    metadata = _extract_metadata(request, body)

    # Prometheus counter
    REQUEST_COUNTER.labels(
        pipeline=metadata["pipeline_name"],
        model=metadata["model_id"],
        endpoint="chat_completions",
    ).inc()

    logger.info(
        "Proxying chat completion",
        event_id=event_id,
        pipeline=metadata["pipeline_name"],
        model=metadata["model_id"],
        stream=body.get("stream", False),
    )

    # 3. Build upstream headers
    upstream_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "SPECTRAFLOW/0.1.0",
    }
    # Preserve any extra headers the client sent (except auth overrides)
    for key, value in request.headers.items():
        if key.lower() not in {
            "authorization", "content-length", "host",
            "x-pipeline-name", "x-prompt-version", "x-user-segment",
            "x-spectraflow-pipeline",
            "x-kcc-capsule-id", "x-kcc-capability-id", "x-kcc-operation",
        }:
            upstream_headers[key] = value

    upstream_url = f"{upstream_base_url.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient() as client:
        # ── Streaming response ────────────────────────────────────────────────
        if body.get("stream", False):
            return StreamingResponse(
                _stream_upstream(
                    client=client,
                    method="POST",
                    url=upstream_url,
                    headers=upstream_headers,
                    body=body,
                    event_id=event_id,
                    metadata=metadata,
                    start_time=start_time,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        # ── Non-streaming response ────────────────────────────────────────────
        upstream_start = time.perf_counter()
        try:
            upstream_response = await client.post(
                upstream_url,
                headers=upstream_headers,
                json=body,
                timeout=settings.upstream_timeout,
            )
        except httpx.TimeoutException:
            logger.error(
                "Upstream timeout",
                event_id=event_id,
                url=upstream_url,
                timeout=settings.upstream_timeout,
            )
            return Response(
                content=json.dumps({
                    "error": {
                        "message": "Upstream LLM timed out",
                        "type": "timeout_error",
                        "code": "upstream_timeout",
                    }
                }),
                status_code=504,
                media_type="application/json",
            )
        except httpx.RequestError as exc:
            logger.error(f"Upstream connection error: {exc}", event_id=event_id)
            return Response(
                content=json.dumps({
                    "error": {
                        "message": f"Upstream connection failed: {exc}",
                        "type": "connection_error",
                    }
                }),
                status_code=502,
                media_type="application/json",
            )

        upstream_latency = time.perf_counter() - upstream_start
        total_latency = time.perf_counter() - start_time

        UPSTREAM_LATENCY.labels(
            pipeline=metadata["pipeline_name"],
            model=metadata["model_id"],
        ).observe(upstream_latency)
        REQUEST_LATENCY.labels(
            pipeline=metadata["pipeline_name"],
            model=metadata["model_id"],
        ).observe(total_latency)

        # 4. Parse upstream response
        response_body = upstream_response.content
        try:
            response_json = upstream_response.json()
        except Exception:
            response_json = {}

        # 5. Emit telemetry (fire-and-forget)
        if upstream_response.status_code == 200:
            content = (
                response_json.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            finish_reason = (
                response_json.get("choices", [{}])[0].get("finish_reason", "unknown")
            )
            usage = response_json.get("usage", {})

            asyncio.create_task(
                _emit_telemetry(
                    {
                        "event_id": event_id,
                        "event_type": "chat_completion",
                        "pipeline_name": metadata["pipeline_name"],
                        "prompt_version": metadata["prompt_version"],
                        "user_segment": metadata["user_segment"],
                        "model_id": metadata["model_id"],
                        "kcc_capsule_id": metadata["kcc_capsule_id"],
                        "kcc_capability_id": metadata["kcc_capability_id"],
                        "kcc_operation": metadata["kcc_operation"],
                        **_content_telemetry(body.get("messages", []), content),
                        "finish_reason": finish_reason,
                        "total_tokens": usage.get("total_tokens", 0),
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "latency_seconds": total_latency,
                        "upstream_latency_seconds": upstream_latency,
                        "stream": False,
                    }
                )
            )

        # 6. Return response to caller with upstream headers
        return Response(
            content=response_body,
            status_code=upstream_response.status_code,
            media_type=upstream_response.headers.get("content-type", "application/json"),
        )


async def proxy_embeddings(request: Request) -> Response:
    """
    Proxy handler for POST /v1/embeddings.
    Pass-through only — embeddings are not fingerprinted (they're used for fingerprinting).
    """
    event_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    try:
        body = await request.json()
    except Exception:
        return Response(
            content=json.dumps({"error": {"message": "Invalid JSON body"}}),
            status_code=400,
            media_type="application/json",
        )

    upstream_base_url, api_key = _resolve_upstream(body.get("model"))

    REQUEST_COUNTER.labels(
        pipeline="system",
        model=body.get("model", "unknown"),
        endpoint="embeddings",
    ).inc()

    upstream_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        try:
            upstream_response = await client.post(
                f"{upstream_base_url.rstrip('/')}/embeddings",
                headers=upstream_headers,
                json=body,
                timeout=settings.upstream_timeout,
            )
        except httpx.RequestError as exc:
            return Response(
                content=json.dumps({"error": {"message": str(exc)}}),
                status_code=502,
                media_type="application/json",
            )

    REQUEST_LATENCY.labels(
        pipeline="system",
        model=body.get("model", "unknown"),
    ).observe(time.perf_counter() - start_time)

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type", "application/json"),
    )


async def proxy_passthrough(request: Request) -> Response:
    """
    Generic pass-through for any other /v1/* endpoint (models list, etc.)
    """
    _, api_key = _resolve_upstream(None)
    path = request.url.path

    body_bytes = await request.body()

    upstream_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": request.headers.get("content-type", "application/json"),
    }

    async with httpx.AsyncClient() as client:
        try:
            upstream_response = await client.request(
                method=request.method,
                url=f"{settings.upstream_base_url.rstrip('/')}{path}",
                headers=upstream_headers,
                content=body_bytes,
                params=dict(request.query_params),
                timeout=60.0,
            )
        except httpx.RequestError as exc:
            return Response(
                content=json.dumps({"error": {"message": str(exc)}}),
                status_code=502,
                media_type="application/json",
            )

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type", "application/json"),
    )
