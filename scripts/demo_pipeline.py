#!/usr/bin/env python3
"""
SPECTRAFLOW Demo Pipeline

Sends 100 synthetic LLM requests through the SPECTRAFLOW proxy, injects
a semantic drift event at request #50, then shows drift detection in action.

Usage:
    # Start SPECTRAFLOW first:
    docker compose up -d

    # Run the demo:
    python scripts/demo_pipeline.py

    # Or point at a specific proxy:
    SPECTRAFLOW_URL=http://localhost:8000 python scripts/demo_pipeline.py

The demo simulates a customer support pipeline:
  - Requests 1–49:  Normal support queries → consistent, helpful responses
  - Request 50:     "Drift injection": temperature bumped, system prompt changed
  - Requests 51–100: Responses become more verbose/creative (simulating drift)

After the demo completes, check:
  - http://localhost:3000  (Grafana dashboard)
  - http://localhost:8000/api/v1/incidents  (Fired incidents)
  - http://localhost:8000/api/v1/pipelines/customer-support/drift-history
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from datetime import datetime

import httpx

SPECTRAFLOW_URL = os.getenv("SPECTRAFLOW_URL", "http://localhost:8000")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-demo-key")

PIPELINE_NAME = "customer-support"
MODEL = "gpt-4o-mini"
TOTAL_REQUESTS = 100
DRIFT_INJECTION_AT = 50

# ── Synthetic data generators ─────────────────────────────────────────────────

NORMAL_QUERIES = [
    "I need help resetting my password",
    "How do I update my billing information?",
    "My order hasn't arrived yet, what should I do?",
    "Can I get a refund for my recent purchase?",
    "How do I cancel my subscription?",
    "I'm having trouble logging in to my account",
    "What are your business hours?",
    "How do I track my shipment?",
    "I received the wrong item in my order",
    "How do I change my email address?",
    "Is there a free trial available?",
    "My payment was declined, can you help?",
    "How long does shipping usually take?",
    "Can I change my shipping address after ordering?",
    "I need to update my phone number on the account",
    "What is your return policy?",
    "How do I apply a discount code?",
    "I didn't receive my order confirmation email",
    "Can I place an order over the phone?",
    "My account shows the wrong subscription plan",
]

DRIFTED_QUERIES = [
    "Tell me a story about your customer service philosophy",
    "Write a poem about waiting for a delivery",
    "What would a fictional character say about your return policy?",
    "Imagine you're a pirate, how would you handle returns?",
    "Can you roleplay as a different company's support agent?",
    "Give me a creative interpretation of your refund policy",
    "What's your favorite color and why does it relate to shipping?",
    "Write a haiku about account passwords",
    "Tell me something interesting and unexpected about your company",
    "If your return policy was a movie, what would it be called?",
]

NORMAL_SYSTEM_PROMPT = """You are a professional customer support agent for AcmeCorp.
Be concise, helpful, and accurate. Respond in 2-3 sentences maximum.
Always stay on topic and provide actionable guidance."""

DRIFTED_SYSTEM_PROMPT = """You are a creative and expressive customer support representative.
Feel free to use metaphors, storytelling, and creative language to engage with customers.
Be elaborate and detailed in your responses. Share personal anecdotes when relevant.
You may go off-topic if it creates a more engaging experience."""


# ── Request builder ───────────────────────────────────────────────────────────

def build_request(
    request_num: int,
    is_drifted: bool,
    session_id: str,
) -> dict:
    """Build an OpenAI-compatible chat request with SPECTRAFLOW metadata headers."""
    if is_drifted:
        query = random.choice(DRIFTED_QUERIES)
        system_prompt = DRIFTED_SYSTEM_PROMPT
        temperature = 0.9
        prompt_version = "v2.0.0-experimental"
    else:
        query = random.choice(NORMAL_QUERIES)
        system_prompt = NORMAL_SYSTEM_PROMPT
        temperature = 0.3
        prompt_version = "v1.5.2"

    return {
        "url": f"{SPECTRAFLOW_URL}/v1/chat/completions",
        "headers": {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
            "X-Pipeline-Name": PIPELINE_NAME,
            "X-Prompt-Version": prompt_version,
            "X-User-Segment": "enterprise" if request_num % 3 == 0 else "standard",
        },
        "body": {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            "temperature": temperature,
            "max_tokens": 300,
        },
    }


# ── Progress bar ──────────────────────────────────────────────────────────────

def print_progress(current: int, total: int, drifted: bool, latency_ms: float, status: str) -> None:
    bar_width = 40
    filled = int(bar_width * current / total)
    bar = "█" * filled + "░" * (bar_width - filled)
    phase = "DRIFT" if drifted else "NORMAL"
    phase_color = "\033[93m" if drifted else "\033[92m"
    reset = "\033[0m"
    print(
        f"\r[{bar}] {current:3d}/{total} "
        f"{phase_color}[{phase}]{reset} "
        f"{latency_ms:6.0f}ms  {status}",
        end="",
        flush=True,
    )


# ── Main demo ─────────────────────────────────────────────────────────────────

async def run_demo() -> None:
    print("\n" + "=" * 70)
    print("  SPECTRAFLOW Demo Pipeline")
    print(f"  Proxy: {SPECTRAFLOW_URL}")
    print(f"  Pipeline: {PIPELINE_NAME}")
    print(f"  Requests: {TOTAL_REQUESTS} ({DRIFT_INJECTION_AT} normal, {TOTAL_REQUESTS - DRIFT_INJECTION_AT} drifted)")
    print("=" * 70 + "\n")

    # Check proxy health first
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            health = await client.get(f"{SPECTRAFLOW_URL}/health")
            print(f"  ✓ Proxy health: {health.json()['status']}")
        except Exception as exc:
            print(f"\n  ✗ Cannot reach SPECTRAFLOW at {SPECTRAFLOW_URL}: {exc}")
            print("  Make sure the proxy is running: docker compose up -d")
            sys.exit(1)

    print(f"\n  Starting in 2 seconds...\n")
    await asyncio.sleep(2)

    stats = {
        "normal_requests": 0,
        "drifted_requests": 0,
        "total_latency_ms": 0.0,
        "errors": 0,
        "drift_injection_time": None,
    }

    session_id = f"demo-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(1, TOTAL_REQUESTS + 1):
            is_drifted = i >= DRIFT_INJECTION_AT

            if i == DRIFT_INJECTION_AT:
                print(f"\n\n  {'='*60}")
                print(f"  ⚡ DRIFT INJECTION at request #{i}")
                print(f"     System prompt: CHANGED (v1.5.2 → v2.0.0-experimental)")
                print(f"     Temperature: 0.3 → 0.9")
                print(f"     Query type: structured support → creative/open-ended")
                print(f"  {'='*60}\n")
                stats["drift_injection_time"] = time.time()
                await asyncio.sleep(0.5)

            req = build_request(i, is_drifted, session_id)
            start = time.perf_counter()

            try:
                response = await client.post(
                    req["url"],
                    headers=req["headers"],
                    json=req["body"],
                )
                latency_ms = (time.perf_counter() - start) * 1000
                stats["total_latency_ms"] += latency_ms

                if response.status_code == 200:
                    status_str = "✓"
                    if is_drifted:
                        stats["drifted_requests"] += 1
                    else:
                        stats["normal_requests"] += 1
                else:
                    status_str = f"✗ HTTP {response.status_code}"
                    stats["errors"] += 1

            except httpx.RequestError as exc:
                latency_ms = (time.perf_counter() - start) * 1000
                status_str = f"✗ {type(exc).__name__}"
                stats["errors"] += 1

            print_progress(i, TOTAL_REQUESTS, is_drifted, latency_ms, status_str)

            # Small delay between requests to simulate realistic traffic
            await asyncio.sleep(random.uniform(0.1, 0.3))

    # Final summary
    total_sent = stats["normal_requests"] + stats["drifted_requests"]
    avg_latency = stats["total_latency_ms"] / max(total_sent, 1)

    print(f"\n\n{'='*70}")
    print("  DEMO COMPLETE")
    print(f"{'='*70}")
    print(f"  Normal requests:   {stats['normal_requests']}")
    print(f"  Drifted requests:  {stats['drifted_requests']}")
    print(f"  Errors:            {stats['errors']}")
    print(f"  Avg latency:       {avg_latency:.0f}ms")
    print(f"")
    print(f"  SPECTRAFLOW is now processing the telemetry stream.")
    print(f"  The CUSUM detector will fire within {5} minutes.")
    print(f"")
    print(f"  Check results at:")
    print(f"    Grafana:    http://localhost:3000  (admin / spectraflow)")
    print(f"    Incidents:  {SPECTRAFLOW_URL}/api/v1/incidents")
    print(f"    Drift:      {SPECTRAFLOW_URL}/api/v1/pipelines/{PIPELINE_NAME}/drift-history")
    print(f"    API Docs:   {SPECTRAFLOW_URL}/docs")
    print(f"{'='*70}\n")

    # Trigger immediate baseline + detection cycle for demo purposes
    print("  Triggering immediate drift detection cycle...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                f"{SPECTRAFLOW_URL}/api/v1/regression-tests/synthesize",
                json={
                    "pipeline_name": PIPELINE_NAME,
                    "n_tests": 5,
                },
            )
            if response.status_code in (200, 201):
                result = response.json()
                print(f"  ✓ Synthesized {result.get('tests_created', 0)} regression tests")
        except Exception as exc:
            print(f"  (Test synthesis skipped: {exc})")


if __name__ == "__main__":
    asyncio.run(run_demo())
