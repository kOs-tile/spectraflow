# SPECTRAFLOW

> **Status — Research-active agent reliability / observability subsystem.** SPECTRAFLOW combines semantic/authority-drift observation with executable recovery evaluation. Reliability Lab v1 now includes a sanitized real KAVI corpus and an 80-run controlled live experiment plan. SPECTRAFLOW observes and evaluates; KCC remains the authority/enforcement plane.


[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com)
[![OpenAI Compatible](https://img.shields.io/badge/OpenAI-compatible-412991.svg?logo=openai)](https://platform.openai.com/docs/api-reference)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg?logo=docker)](https://www.docker.com/)
[![Celery](https://img.shields.io/badge/Celery-5.x-37814A.svg)](https://docs.celeryq.dev/)

> **Behavior and authority-drift observability for agent systems.**

---

## The Silent Degradation Problem

You deployed a prompt in January. It worked beautifully. By March, something changed.

Not your model. Not your prompt. But the *behavior* is different. Responses are shorter. Edge cases fail. The tone shifted. Support tickets are rising. You have no idea when it started or why.

**This is semantic drift** — and it's invisible to traditional monitoring.

SPECTRAFLOW is a drop-in OpenAI-compatible proxy that sits between your application and any LLM provider. Every response is semantically fingerprinted, embedded into a behavioral baseline, and continuously monitored for drift using CUSUM control charts. When behavioral change is detected, a multi-step AI agent diagnoses the root cause and generates regression tests to prevent recurrence.

```
Your App  ──►  SPECTRAFLOW Proxy  ──►  OpenAI / Claude / DeepSeek
                      │
                      ▼
              Semantic Fingerprint
                      │
              ┌───────┴────────┐
              ▼                ▼
        Baseline Engine   Drift Detector
              │                │
              └───────┬────────┘
                      ▼
              Root Cause Agent
                      │
                      ▼
           Regression Test Synthesizer
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         SPECTRAFLOW Platform                        │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    FastAPI Proxy Layer                       │   │
│  │  POST /v1/chat/completions  │  POST /v1/embeddings           │   │
│  │  GET  /health               │  GET  /metrics                 │   │
│  └────────────────────┬─────────────────────────────────────────┘   │
│                       │ async emit                                  │
│                       ▼                                             │
│  ┌──────────────────────────────┐                                   │
│  │      Redis Streams Bus       │  ← telemetry events              │
│  │   (spectraflow:telemetry)    │                                   │
│  └──────────────┬───────────────┘                                   │
│                 │ consumer group                                     │
│                 ▼                                                   │
│  ┌──────────────────────────────┐   ┌────────────────────────────┐  │
│  │   Telemetry Ingestion Worker │   │   Prompt Registry          │  │
│  │   • tag pipeline/version     │──►│   • version hashes         │  │
│  │   • route to fingerprinting  │   │   • attribution map        │  │
│  └──────────────┬───────────────┘   └────────────────────────────┘  │
│                 │                                                   │
│        ┌────────┴────────┐                                          │
│        ▼                 ▼                                          │
│  ┌──────────────┐  ┌──────────────────────────────────────────────┐ │
│  │   Qdrant     │  │            TimescaleDB                       │ │
│  │  (vectors)   │  │  behavioral_metrics │ baselines │ incidents  │ │
│  └──────┬───────┘  └────────────────┬─────────────────────────────┘ │
│         │                           │                               │
│         └─────────────┬─────────────┘                               │
│                       ▼                                             │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │              Behavioral Baseline Engine                        │ │
│  │  • Rolling 7-day centroid per pipeline                         │ │
│  │  • Semantic variance (cosine distance distribution)            │ │
│  │  • Schema compliance rate                                      │ │
│  └────────────────────┬───────────────────────────────────────────┘ │
│                       │                                             │
│                       ▼ every 5 min (Celery beat)                   │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │              CUSUM Drift Detector                              │ │
│  │  • Tabular CUSUM on centroid distance time-series              │ │
│  │  • Configurable threshold h, slack k                           │ │
│  │  • Returns (drift_detected, magnitude, first_signal_index)     │ │
│  └────────────────────┬───────────────────────────────────────────┘ │
│                       │ drift_detected                              │
│                       ▼                                             │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │              Root Cause Agent  (multi-step LLM)                │ │
│  │  Step 1: Compare drifted vs baseline response samples          │ │
│  │  Step 2: Analyze prompt version history                        │ │
│  │  Step 3: Inspect input distribution shift                      │ │
│  │  Step 4: Generate structured IncidentReport                    │ │
│  └────────────────────┬───────────────────────────────────────────┘ │
│                       │                                             │
│                       ▼                                             │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │           Regression Test Synthesizer                          │ │
│  │  • Sample representative production inputs                     │ │
│  │  • LLM generates expected_behavior_description                 │ │
│  │  • Store golden test cases                                     │ │
│  │  • Run against shadow model on each deploy                     │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌────────────────────┐   ┌────────────────────────────────────────┐ │
│  │  Grafana Dashboard │   │     Prometheus Metrics Endpoint        │ │
│  │  :3000             │   │     /metrics                           │ │
│  └────────────────────┘   └────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2-Line Integration

```python
# Before
client = openai.OpenAI(api_key="sk-...")

# After — that's it
client = openai.OpenAI(api_key="sk-...", base_url="http://localhost:8000/v1")
```

All API calls are proxied transparently. Telemetry dispatch is asynchronous so Redis emission does not block the completed upstream response path, but the proxy still adds normal network/parsing overhead.

---

## KCC authority correlation

When a caller is executing under a KAVI Capability Compiler capsule, it can attach internal correlation headers:

```text
X-KCC-Capsule-Id: <capsule id>
X-KCC-Capability-Id: <canonical capability id>
X-KCC-Operation: <bounded operation>
```

SPECTRAFLOW records these values with telemetry so drift/incidents can be traced back to the authority context that was active. These headers are stripped before forwarding the request upstream.

For explicit verification, `POST /api/v1/authority/evaluate` compares a supplied `kcc.capsule.v1` with an observed capability call. It reports capsule tampering, expiry, ungranted capabilities, operation escape, and supported parameter-boundary violations, then seals the result with an `evaluation_fingerprint`.

This evaluator is **observational only**. It does not grant, expand, or dispatch authority; KCC remains the authority/enforcement plane.

## Telemetry privacy

SPECTRAFLOW now applies a content-retention policy **before telemetry leaves the proxy**:

- `response` (default): stores model output for semantic drift analysis, but does not store raw user prompt messages.
- `metadata`: stores neither prompt nor response text; hashes, model/usage metadata, and latency remain available. Semantic fingerprinting is skipped because no response text is retained.
- `full`: explicit opt-in for storing both prompt messages and model output.

Request and response SHA-256 fingerprints are emitted in every mode so repeated payloads can be correlated without keeping raw prompt text. This is still not a complete PII/compliance solution; production deployments should add field-level masking and retention controls appropriate to their data.

## Features

| Feature | Description |
|---|---|
| **OpenAI-Compatible Proxy** | Drop-in replacement. Works with any SDK targeting OpenAI's API (LangChain, LlamaIndex, etc.) |
| **Multi-Provider Support** | Route to OpenAI, Anthropic Claude, DeepSeek, or any OpenAI-compatible endpoint |
| **Semantic Fingerprinting** | Embed every response with `text-embedding-3-small`, store in Qdrant for similarity search |
| **Behavioral Baselines** | Rolling 7-day per-pipeline centroid, semantic variance, schema compliance rate |
| **CUSUM Drift Detection** | Tabular CUSUM control charts with configurable h/k thresholds. Detects sustained shifts, not noise spikes |
| **Authority Drift Observation** | Compares runtime calls with KCC capsule grants, operation bounds, expiry, integrity, and supported parameter constraints |
| **Bounded Recovery Evaluation** | Deterministic synthetic timeout, provider-failure, malformed-result, retry-exhaustion, and non-retryable-failure scenarios with per-attempt traces |
| **Execution-Trace Assessment** | Measures dispatcher calls, committed side effects, verification, duplicate execution, safe completion, and intervention requirement |
| **Runtime-Derived Trace Evidence** | Classifies sanitized real KAVI task metadata without upgrading `completed` to verified PASS unless explicit acceptance/final-status evidence exists |
| **Reliability Lab v1** | 85 real historical agent sessions + 26 execution-control tasks, metric-coverage reporting, controlled side-effect/idempotency evidence, and an 80-run live policy matrix |
| **Root Cause Analysis** | Multi-step LLM agent compares drifted samples, analyzes prompt history, identifies likely cause |
| **Regression Test Synthesis** | Learns golden test cases from production traffic. Auto-runs on shadow model |
| **Prompt Registry** | Versioned prompt templates with SHA-256 hashes. Every inference attributed to a prompt version |
| **Incident Management** | Structured incident reports with drift magnitude, affected pipeline, contributing factors |
| **Prometheus + Grafana** | Full metrics stack. Latency, throughput, drift scores, incident rates |

---

## Dashboard

```
┌─────────────────────────────────────────────────────────────┐
│  SPECTRAFLOW  │  Pipeline: customer-support  │  Last 7 days  │
├──────────────────┬──────────────────┬────────────────────────┤
│  Requests/min    │  Semantic Drift   │  Active Incidents      │
│  ▁▂▄▆▅▆▇▆▅▄▂▁  │  ████░░░░  0.34  │  ⚠  2 open            │
│  142 avg         │  baseline: 0.12   │  ✓  8 resolved         │
├──────────────────┴──────────────────┴────────────────────────┤
│  Centroid Distance (7d)                                       │
│  1.0 ┤                                    ╭──────             │
│  0.8 ┤                               ╭───╯                   │
│  0.6 ┤         ╭─────────────────────╯                       │
│  0.4 ┤─────────╯                           CUSUM threshold   │
│  0.2 ┤ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ baseline    │
│      └──────────────────────────────────────────────────→ t  │
│      Jun 22          Jun 25              Jun 29               │
└─────────────────────────────────────────────────────────────┘
```

---

## Metrics Reference

| Metric | Type | Description |
|---|---|---|
| `spectraflow_requests_total` | Counter | Total proxied requests, labeled by pipeline, model |
| `spectraflow_request_latency_seconds` | Histogram | End-to-end proxy latency |
| `spectraflow_upstream_latency_seconds` | Histogram | Upstream LLM latency |
| `spectraflow_drift_score` | Gauge | Current CUSUM statistic per pipeline |
| `spectraflow_baseline_centroid_distance` | Gauge | Distance from rolling centroid |
| `spectraflow_incidents_total` | Counter | Fired incidents, labeled by pipeline, severity |
| `spectraflow_regression_tests_total` | Gauge | Golden test cases in registry |
| `spectraflow_regression_pass_rate` | Gauge | Pass rate of last regression run |
| `spectraflow_schema_compliance_rate` | Gauge | % responses matching expected schema |

---

## Product boundary

SPECTRAFLOW is not positioned as a replacement for general-purpose tracing,
prompt analytics, or OpenTelemetry-compatible observability stacks. Those are
useful adjacent systems.

The active differentiator is narrower: **bind observed agent behavior to the
authority context that was supposed to govern it**. Semantic drift answers
"did behavior change?"; authority drift asks "did execution move outside the
capability, operation, expiry, or parameter bounds represented by the KCC
capsule?"

Authority validation details are in
[`docs/AUTHORITY_DRIFT_VALIDATION.md`](docs/AUTHORITY_DRIFT_VALIDATION.md).

The reliability evidence now has four layers: deterministic fault injection, a six-case side-effect/idempotency corpus, runtime-derived KAVI traces, and **Reliability Lab v1**. The v1 real corpus contains **85 sanitized historical agent sessions + 26 Shared Execution Control tasks = 111 real records**. It reports metric coverage before headline rates: execution status/model-invocation coverage is complete on the 26-task control corpus, while task success is evaluable for 21/26, explicit verification for 8/26, and latency for 12/26. Actual token usage, cost, human intervention, real committed-side-effect counts, and normalized dispatch-authority decisions are currently reported as unavailable rather than inferred.

The next live layer is already specified and regression-locked: **10 bounded code-repair tasks × 2 deterministic fault profiles × 4 policy arms = 80 planned isolated executions**. The policy arms are baseline, bounded recovery, idempotent recovery, and authority-aware execution. The two controlled fault profiles are post-commit timeout and authority revocation immediately before dispatch.

**Important boundary:** the 80 live agent executions have **not** yet been collected. The current repository proves the corpus, task contracts, policy matrix, fail-closed KAVI payload mapping, and instrumentation contract; it does not publish live comparative success/recovery rates before those executions occur.

The live analysis path is also implemented: once sanitized records exist, `benchmark.reliability_live_report` computes evidence-aware policy/fault breakdowns with Wilson 95% confidence intervals while excluding unknown telemetry from affected denominators.

Evidence and implementation contracts:
- [Reliability Lab v1 methodology + real-corpus checkpoint](docs/RELIABILITY_LAB_V1.md)
- [Reliability validation layers](docs/RELIABILITY_VALIDATION.md)
- [KAVI live adapter contract](docs/KAVI_LIVE_ADAPTER_CONTRACT.md)
- [Local KAVI/Hermes runtime implementation contract](docs/HERMES_LOCAL_RELIABILITY_ADAPTER.md)
- [Golden v1 summary](benchmark/results/reliability_lab_v1_summary.json)

---

## Reliability Lab v1 checkpoint

| Evidence | Current checkpoint |
|---|---:|
| Sanitized historical KAVI agent sessions | **85** |
| Sanitized Shared Execution Control tasks | **26** |
| Total real records | **111** |
| Recorded historical tool calls | **4,937** |
| Recorded historical messages | **1,991** |
| Verified PASS in 26-task control corpus | **5** |
| Completed but not explicitly verified | **3** |
| Explicit failed tasks | **15** |
| Timeout failures | **8** |
| Model invocations | **17** |
| Real attempts > 1 recovery events | **0** |
| Planned controlled live executions | **80** |

Because the real execution-control snapshot contains no attempts > 1 recovery event, SPECTRAFLOW does **not** manufacture a production recovery-rate percentage from that dataset.

The controlled side-effect corpus separately demonstrates the core retry boundary:

```text
post-commit timeout + blind bounded retry
  -> 2 attempts
  -> 2 committed effects
  -> duplicate side effect

post-commit timeout + stable idempotency
  -> 2 attempts
  -> 1 committed effect
  -> no duplicate
```

The 80-run live matrix will measure actual agent task completion and recovery after the local KAVI/Hermes benchmark adapter passes its 12 preflight/acceptance checks and an initial 8-run canary.

---

## Quick Start

### Prerequisites
- Docker + Docker Compose
- OpenAI API key (or compatible provider)

### Run

```bash
git clone https://github.com/kOs-tile/spectraflow
cd spectraflow
cp .env.example .env
# Edit .env with your API keys
docker compose up -d
```

The proxy starts on `http://localhost:8000`. Grafana at `http://localhost:3000` (admin/spectraflow).

### Configuration

See `.env.example` for all configuration options. Key settings:

```env
OPENAI_API_KEY=sk-...           # Your OpenAI key
UPSTREAM_BASE_URL=https://api.openai.com/v1
CUSUM_THRESHOLD_H=5.0           # CUSUM alarm threshold
CUSUM_SLACK_K=0.5               # CUSUM reference value (allowable slack)
DRIFT_MONITOR_INTERVAL=300      # Detection interval in seconds
BASELINE_WINDOW_DAYS=7          # Rolling baseline window
```

### Run Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

### Demo Pipeline

Send 100 synthetic requests with injected drift:

```bash
python scripts/demo_pipeline.py
```

---

## Project Structure

```
spectraflow/
├── spectraflow/
│   ├── config.py              # Pydantic Settings
│   ├── proxy/
│   │   ├── handler.py         # Request intercept, upstream forward, telemetry emit
│   │   └── router.py          # FastAPI routes /v1/*
│   ├── telemetry/
│   │   ├── ingestion.py       # Redis Stream consumer, event tagging
│   │   └── fingerprinting.py  # Embedding + Qdrant + TimescaleDB
│   ├── authority/
│   │   └── drift.py           # Observational KCC authority-drift evaluator
│   ├── reliability/
│   │   ├── recovery.py        # Framework-neutral bounded recovery evaluation harness
│   │   ├── execution.py       # Side-effect execution-trace assessment
│   │   ├── runtime.py         # Sanitized runtime-task evidence classification
│   │   ├── corpus.py          # Real-corpus normalization + metric coverage
│   │   ├── live.py            # 80-run live policy matrix + canonical verifier
│   │   └── kavi_adapter.py    # Fail-closed KAVI Dispatch Bridge payload adapter
│   ├── detection/
│   │   ├── baseline.py        # Rolling centroid, variance, schema compliance
│   │   ├── cusum.py           # CUSUM control chart algorithm
│   │   └── drift_monitor.py   # Celery scheduled task
│   ├── agents/
│   │   ├── root_cause.py      # Multi-step root cause LLM agent
│   │   └── test_synthesizer.py # Regression test generation
│   ├── registry/
│   │   └── prompt_store.py    # Versioned prompt registry
│   ├── api/
│   │   ├── authority.py       # /api/v1/authority/evaluate
│   │   ├── incidents.py       # Incident management routes
│   │   └── tests.py           # Regression test routes
│   └── monitoring/
│       └── metrics.py         # Prometheus metrics
├── benchmark/
│   ├── authority_drift.py       # KCC authority-drift adversarial benchmark
│   ├── recovery_faults.py       # Deterministic bounded-recovery fault benchmark
│   ├── execution_traces.py      # Side-effect/idempotency execution corpus
│   ├── kavi_runtime_traces.py   # Sanitized runtime-derived task corpus
│   ├── reliability_lab_v1.py    # 111-record Reliability Lab aggregate
│   ├── reliability_live_plan.py # 80-run task/fault/policy plan
│   ├── kavi_dispatch_payloads.py# Render-only KAVI bridge payload mapping
│   ├── live_tasks_v1.json       # Ten bounded live task contracts
│   ├── live_fixture/            # Buggy candidate + canonical reference
│   ├── results/                 # Golden versioned benchmark summaries
│   └── fixtures/                # Sanitized runtime/history corpora
├── scripts/
│   └── demo_pipeline.py       # End-to-end demo with injected drift
├── tests/
│   ├── test_authority_drift.py
│   ├── test_recovery.py
│   ├── test_recovery_benchmark.py
│   ├── test_execution_trace_benchmark.py
│   ├── test_kavi_runtime_trace_benchmark.py
│   ├── test_reliability_lab_v1.py
│   ├── test_reliability_live_plan.py
│   ├── test_kavi_live_adapter.py
│   ├── test_cusum.py
│   ├── test_proxy.py
│   └── test_fingerprinting.py
├── grafana/
│   └── dashboards/
│       └── spectraflow.json
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## Author

Built by **Onur Kavi** — AI Systems Engineer

[GitHub](https://github.com/kOs-tile) · [LinkedIn](https://linkedin.com/in/onurcankavi)

---

## License

MIT License — see [LICENSE](LICENSE) for details.
