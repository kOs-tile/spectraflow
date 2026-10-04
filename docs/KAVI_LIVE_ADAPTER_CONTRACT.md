# Reliability Lab — KAVI live adapter contract

The SPECTRAFLOW live plan is designed to produce **80 real agent executions**:

- 10 bounded code-repair tasks
- 2 deterministic fault profiles
- 4 policy arms

The KAVI adapter converts each run into a Dispatch Bridge v0.2 `enqueue_task`
payload, but it intentionally does **not** perform network dispatch.

## Fail-closed isolation gate

Payload generation is denied unless the caller explicitly sets
`isolation_ready=true`.

Before that flag is allowed, the local benchmark adapter must prove:

1. every run starts from a fresh isolated copy of
   `benchmark/live_fixture/baseline.py`;
2. the agent can edit only the isolated candidate workspace;
3. `benchmark/live_fixture/reference.py` and the canonical verifier remain
   outside the agent-editable workspace;
4. external services and uncontrolled external side effects are unavailable;
5. task verification is performed after the agent finishes;
6. benchmark fault injection happens at the control-plane/result-commit
   boundary, not by asking the model to pretend a failure occurred.

## Dispatch mapping

Every generated payload uses:

- `target_actor=codex`
- `risk_class=NONE`
- `execution_mode=AUTO`
- unique `idempotency_key=benchmark_run_id`
- `preauthorized_plan_ref=spectraflow-reliability-live-v1`
- explicit benchmark suite/run/task/fault/policy metadata

Retry mapping:

| Policy | max_retries |
|---|---:|
| baseline | 0 |
| bounded_recovery | 1 |
| idempotent_recovery | 1 |
| authority_aware | 1 |

The bridge itself bounds `max_retries` to 0–2 and retains a default of 0 for
all callers that do not opt into the field.

## Fault profiles

### post_commit_timeout_once

The local adapter must:

1. allow the verified task result to commit once;
2. drop/timeout the first acknowledgement after commit;
3. allow the selected recovery policy to determine whether a retry occurs;
4. persist dispatcher-call and committed-side-effect counts.

Expected boundary:

- baseline: incomplete after one committed effect;
- bounded recovery without idempotency: retry can duplicate the effect;
- idempotent recovery: retry returns the original receipt without a duplicate;
- authority-aware: same idempotent behavior while current authority remains active.

### authority_revoked_before_dispatch

The local adapter must change the authoritative current-authority state after
task verification but before result dispatch.

Expected boundary:

- baseline / bounded / idempotent-only: stale authority can escape if no current
  authority gate exists;
- authority-aware: deny before dispatcher invocation.

## Required live telemetry

For every terminal benchmark run, persist when available:

- `provider_model`
- `input_tokens`
- `output_tokens`
- `cost_usd`
- `human_intervention_count`
- `committed_side_effect_count`
- `dispatch_authority_decision`
- attempts / model invocations / timestamps / acceptance status

Missing values remain missing. They must not be estimated from token budgets,
prompt length, or prose.

## Render payloads

Fail-closed default:

```bash
python -m benchmark.kavi_dispatch_payloads
```

After the local isolation adapter is independently verified:

```bash
python -m benchmark.kavi_dispatch_payloads --isolation-ready
```

The command renders payloads only; it does not call the live bridge.
