# Reliability fault/recovery validation v0

SPECTRAFLOW now includes a small framework-neutral bounded-recovery harness for
measuring retry behavior under deterministic synthetic faults.

This is an evaluation primitive. It is not a production retry policy and it does
not dispatch agent tools, grant authority, or prove production reliability.

## v0 fault classes

The initial benchmark covers:

| Scenario | Expected behavior |
|---|---|
| clean success | one call, no retry |
| synthetic tool timeout | bounded retry, then recovery |
| synthetic provider connection failure | bounded retry, then recovery |
| malformed result | validator rejects result, bounded retry, then recovery |
| repeated retryable failure | stop at retry budget |
| non-retryable input failure | stop immediately |

## Metrics

The benchmark reports:

- exact expected/actual outcome match rate
- synthetic recovery rate for injected recoverable faults
- attempts per scenario
- retry-budget exhaustion
- terminal error type

The per-attempt trace also records outcome, error type, error message, and elapsed
milliseconds. Timing is diagnostic only and is not used as a deterministic pass
criterion.

## Safety / scope properties

The v0 harness is designed to preserve these properties:

1. no retry after the first valid success;
2. no more than the configured maximum number of attempts;
3. malformed output is not silently counted as success when a validator is supplied;
4. non-retryable exceptions terminate immediately;
5. the harness does not call KCC, alter authority, or replace KCC Guard enforcement;
6. the harness does not claim that synthetic recovery rate equals real-world agent reliability.

## Run

    python -m benchmark.recovery_faults

and run the regression tests with:

    pytest -q tests/test_recovery.py

## Execution-trace corpus v0

A second reproducible corpus executes real Python callables against an in-memory
side-effect ledger and records actual dispatcher calls and committed effects.

Current six-case checkpoint:

- clean side-effecting success -> safe completion
- pre-dispatch timeout -> bounded recovery with one committed effect
- post-commit timeout + blind retry -> recovered result but one duplicate side effect
- post-commit timeout + stable idempotency key -> recovered result with no duplicate
- completed operation + failed verification -> intervention required
- terminal provider failure -> zero dispatcher calls and intervention required

This corpus intentionally exposes the blind-retry failure rather than hiding it.
A retry is not considered safe merely because the final attempt returns success.

The execution-trace evaluator records:

- task success
- verification outcome
- dispatcher call count
- committed side-effect count
- duplicate side effects
- safe completion
- intervention requirement

This is still reproducible local execution evidence, not production-world reliability.

## Next evidence gate

Connect the same trace contract to real agent/runtime executions or an external
task corpus and add cost/latency plus human-intervention measurements. Keep both
the deterministic fault suite and execution-trace suite as regression layers.
