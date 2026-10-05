# SPECTRAFLOW Reliability Lab v1

SPECTRAFLOW Reliability Lab v1 turns the existing fault-injection and execution-trace work into a real-corpus reliability benchmark.

The benchmark is intentionally evidence-conservative. Missing telemetry is reported as missing telemetry; it is not estimated from prompts, task text, budgets, or model output.

## Evidence layers

### Layer A — historical agent corpus

Source: `kOs-tile/kavi-codex-state/history/shard-0001.json`

Sanitized corpus:

- **85 real historical agent sessions**
- **4,937 recorded tool calls**
- **1,991 recorded messages**
- status distribution: 60 `active_or_unknown`, 10 `completed`, 9 `blocked`, 6 `unknown`

This layer establishes corpus scale and tool/message density. Historical session status is not interpreted as a semantic PASS verdict.

### Layer B — Shared Execution Control

Source: `kOs-tile/kavi-codex-state/shared/execution-queue.json`

Sanitized corpus:

- **26 real execution-control tasks**
- 25 Codex-targeted / 1 Work-targeted
- 8 completed
- 15 failed
- 1 blocked
- 2 ready
- **17 model invocations**
- **23 total attempts**
- **8 timeout failures**
- 5 pre-model failures
- 10 post-model failures
- 5 verified PASS
- 3 completed without sufficient explicit PASS evidence

A completed task is not upgraded to verified PASS unless explicit `acceptance_status=pass` or `final_status=PASS` evidence exists.

### Layer C — controlled side-effect corpus

The existing deterministic execution-trace suite preserves side-effect visibility that the historical production snapshot does not expose.

The important paired scenario is:

| Policy | Attempts | Committed effects | Duplicate effects | Safe completion |
|---|---:|---:|---:|---|
| post-commit timeout + blind retry | 2 | 2 | 1 | no |
| post-commit timeout + stable idempotency | 2 | 1 | 0 | yes |

This is controlled execution evidence, not production-rate evidence.

## Metric coverage

Reliability Lab reports measurement coverage before reporting a headline rate.

| Metric | Real execution-control coverage |
|---|---:|
| execution status | 26 / 26 |
| attempts / retry budget | 26 / 26 |
| model invocations | 26 / 26 |
| task success evaluable | 21 / 26 |
| explicit verification evidence | 8 / 26 |
| latency | 12 / 26 |
| actual token usage | 0 / 26 |
| actual cost | 0 / 26 |
| explicit human intervention | 0 / 26 |
| committed side-effect count | 0 / 26 |
| normalized dispatch authority decision | 0 / 26 |

`max_output_tokens` is treated as a budget, never as actual token usage.

## Current claim boundary

The 26-task real execution-control snapshot contains **no attempts > 1 recovery event**.

Therefore Reliability Lab v1 does **not** publish a real recovery-rate percentage.

The repository can demonstrate recovery behavior through controlled deterministic traces, while the real-corpus layer currently reports the instrumentation gap explicitly.

## Run

```bash
python -m benchmark.reliability_lab_v1
```

Fast regression gate:

```bash
pytest -q \
  tests/test_recovery.py \
  tests/test_recovery_benchmark.py \
  tests/test_execution_trace_benchmark.py \
  tests/test_kavi_runtime_trace_benchmark.py \
  tests/test_reliability_lab_v1.py
```

GitHub Actions also runs the lightweight `reliability-fast` workflow for reliability/benchmark changes.

## Golden checkpoint

The inspectable result snapshot is:

`benchmark/results/reliability_lab_v1_summary.json`

The snapshot is tied to exact source blob SHAs. Updating the underlying corpus requires a new evidence refresh rather than silently changing the reported checkpoint.

## Live benchmark v2 gate

The next stage is a controlled **80-run** real-agent experiment:

- 20 bounded local coding/evaluation tasks
- 4 execution policies per task
- baseline
- bounded recovery
- idempotent recovery
- authority-aware execution

The run must use isolated workspaces and no uncontrolled external side effects.

Before publishing comparative rates, runtime telemetry must add:

- provider/model identity
- input tokens
- output tokens
- normalized cost
- explicit human-intervention count
- recovery strategy
- committed-side-effect count
- dispatch-time authority decision
- benchmark run/scenario identity

Until those fields exist, Reliability Lab must continue to report coverage gaps instead of inferring them.

## Live result analysis

After a sanitized live collector result exists, generate the policy/fault report with:

```bash
python -m benchmark.reliability_live_report <collector-result.json>
```

The analyzer keeps missing evidence out of metric denominators instead of turning
unknown values into failures.

It reports:

- task-success rate over runs with explicit canonical verification;
- comparative policy-safety rate over fault-attested eligible runs;
- recovery and safe-recovery rates only where explicit recovery evidence exists;
- authority-escape rate only where explicit authority outcome evidence exists;
- duplicate-effect incidence and count;
- model invocation totals;
- token, cost, intervention, and latency coverage before aggregates;
- policy, fault-profile, and policy × fault-profile breakdowns;
- Wilson 95% confidence intervals for proportions;
- duplicate run-ID detection and exact terminal-run completeness.

A complete 80-run set is not the same thing as 80 comparative-eligible runs.
Agent task failure remains a real task outcome; policy-safety denominators are
reported separately. Production-world reliability claims remain out of scope.

