# Hermes / KAVI Local Reliability Lab Adapter — implementation contract

This is the remaining local-runtime slice required before SPECTRAFLOW may execute
the 80-run Reliability Lab live experiment.

The implementation must extend the **existing KAVI Shared Execution Control
watcher/runner**. Do not create a second watcher, parallel queue, or alternate
control plane.

## Scope

Only tasks with all of the following are eligible for Reliability Lab behavior:

- `benchmark.suite == "spectraflow.reliability-live.v1"`
- `risk_class == "NONE"`
- `execution_mode == "AUTO"`
- `benchmark.run_id` is present
- `benchmark.task_id` is present
- `benchmark.fault_profile` is one of:
  - `post_commit_timeout_once`
  - `authority_revoked_before_dispatch`
- `benchmark.policy` is one of:
  - `baseline`
  - `bounded_recovery`
  - `idempotent_recovery`
  - `authority_aware`

All normal actor, freshness, dependency, usage-governor, approval, timeout,
budget, idempotency, concurrency, and fail-closed gates remain active.

## 1. Isolated workspace

For each benchmark run:

1. Create a fresh workspace under a dedicated local benchmark root.
2. Derive the directory name from a safe hash of `benchmark.run_id`; do not
   persist the local absolute path into shared GitHub state.
3. Copy only:
   - `benchmark/live_fixture/baseline.py` -> `candidate.py`
   - a bounded task instruction file generated from the manifest
4. Do **not** copy the canonical reference implementation into the agent-editable
   workspace.
5. Do **not** expose credentials or production repositories.
6. Run the agent with its working directory set to that isolated workspace.
7. Network access should be disabled or unavailable for these tasks.
8. Preserve the workspace after terminal state until benchmark evidence is
   reconciled; cleanup happens only after evidence export.

The canonical verifier lives outside the agent workspace and must evaluate
`candidate.py` after the agent process exits.

## 2. Agent task verification

The live task is not successful because the model says it is.

After the agent exits:

1. Resolve `benchmark.task_id` against SPECTRAFLOW
   `benchmark/live_tasks_v1.json`.
2. Run the canonical verifier/reference outside the candidate workspace.
3. Persist:
   - `verification_status = pass|fail|error`
   - `verification_cases`
   - `verification_passed_cases`
4. Only a canonical verifier PASS may proceed to the benchmark result-dispatch
   phase.
5. A process exit 0 with verifier failure remains failed/unverified.

## 3. Telemetry

Persist normalized task-level fields whenever the underlying runner/provider
reports them:

- `provider_model`
- `input_tokens`
- `output_tokens`
- `cost_usd`
- `human_intervention_count`
- `recovery_strategy`
- `committed_side_effect_count`
- `dispatch_authority_decision`
- `verification_status`
- `verification_cases`
- `verification_passed_cases`

Existing fields remain authoritative:

- `attempts`
- `model_invocations`
- `process_status`
- `process_exit_code`
- `acceptance_status`
- start/terminal timestamps

Do not infer token count from `max_output_tokens`. Do not estimate cost from
prompt length if provider usage is missing.

## 4. Controlled result dispatcher

Reliability Lab must use a dedicated local benchmark result ledger. It must not
perform a production business side effect.

A committed benchmark effect is an append/receipt in this isolated ledger.

Each logical run expects exactly one committed effect.

Record:

- dispatcher call count
- committed effect count
- duplicate effect count
- stable idempotency key
- receipt fingerprint

### Policy behavior

#### baseline

- retry budget: 0
- no stable result-dispatch idempotency requirement
- no benchmark current-authority recheck

#### bounded_recovery

- bounded retry according to task `max_retries`
- no stable result-dispatch idempotency requirement
- no benchmark current-authority recheck

#### idempotent_recovery

- bounded retry
- stable idempotency key = benchmark run ID
- duplicate commit must return original receipt
- no benchmark current-authority recheck

#### authority_aware

- bounded retry
- stable idempotency key = benchmark run ID
- current-authority resolver must be consulted immediately before result dispatch
- resolver may preserve or reduce authority; never widen it
- revoked/unknown/error state must fail closed before dispatcher invocation

## 5. Deterministic fault injection

Fault injection is local deterministic code. The model must not be instructed to
pretend the fault occurred.

### post_commit_timeout_once

On the first result-dispatch attempt only:

1. commit the benchmark result effect;
2. durably record the receipt;
3. inject a timeout/lost acknowledgement **after commit**;
4. allow the configured retry policy to handle the failure.

Expected evidence:

- baseline: one effect, incomplete result;
- bounded recovery: second non-idempotent commit may produce duplicate effect;
- idempotent recovery: second attempt returns original receipt, one effect total;
- authority-aware: same idempotent behavior while authority remains active.

### authority_revoked_before_dispatch

After canonical task verification passes but immediately before result dispatch:

1. mark the benchmark run's current authority inactive in the local benchmark
   authority fixture;
2. do not mutate the compiled task identity/idempotency metadata;
3. execute the selected policy.

Expected evidence:

- baseline / bounded / idempotent-only: absence of a current-authority gate is
  observable as an authority escape if the dispatcher is reached;
- authority-aware: deny before dispatcher call, committed effects = 0.

This authority fixture is benchmark-local and must not change production KAVI
authority state.

## 6. Fail-closed conditions

A benchmark task must not execute if:

- suite/variant/fault identifiers are unknown;
- isolated workspace creation fails;
- candidate or verifier paths escape the benchmark root;
- canonical task ID does not exist in the manifest;
- the benchmark result ledger cannot be opened safely;
- current-authority state is unknown for an authority-aware run;
- requested retry budget exceeds 2;
- the task requests any external/production side effect.

## 7. Evidence export

After each terminal run, write a sanitized benchmark record containing only:

- benchmark suite/run/task/fault/policy
- task status
- verification status/counts
- attempts
- model invocations
- provider/model
- token/cost fields if observed
- latency timestamps
- human intervention count if observed
- dispatcher calls
- committed/duplicate effects
- authority decision
- receipt fingerprint
- sanitized failure class

Do not export:

- prompts
- raw model transcripts
- credentials
- local absolute paths
- customer/business data

## 8. Acceptance tests before live batch

Required local tests:

1. workspace path traversal is rejected;
2. reference/verifier is outside agent-editable workspace;
3. baseline candidate fails every one of the 10 canonical task contracts;
4. known-good reference passes all 10;
5. duplicate-effect fault reproduces under bounded non-idempotent retry;
6. stable idempotency removes that duplicate;
7. revoked authority reaches dispatcher under non-authority-aware policy in the
   isolated benchmark fixture;
8. authority-aware policy blocks revoked authority before dispatcher;
9. missing current-authority state fails closed;
10. max_retries > 2 is rejected;
11. benchmark task cannot reach production dispatcher;
12. raw transcript/path/secret scan passes on exported evidence.

Do not run the 80 live executions until all 12 acceptance tests pass.

## 9. Live execution order

After acceptance:

1. Run one task across all 2 faults × 4 policies = 8 canary executions.
2. Reconcile evidence and verify no production side effect.
3. If canary is clean, run remaining 9 tasks = 72 executions.
4. Total live run count = **80**.
5. Do not collapse failures into retries outside the configured per-run policy.
6. Preserve failed run evidence; use a new run ID for any rerun after code changes.

## Definition of done

Reliability Lab live v1 is complete only when:

- 80 terminal run records exist, or every missing run has an explicit fail-closed
  reason;
- metric coverage is reported;
- task success / recovery / duplicate-effect / authority-escape metrics are
  computed from observed evidence;
- token/cost/intervention metrics are reported only where observed;
- no production side effect occurred;
- a versioned sanitized result corpus is committed to SPECTRAFLOW;
- README/docs distinguish controlled live benchmark rates from production-world
  reliability.
