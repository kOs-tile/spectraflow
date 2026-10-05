# Real Agent Benchmark v1

SPECTRAFLOW Real Agent Benchmark v1 is the next reliability evidence layer above
the deterministic fault suite, side-effect trace corpus, and sanitized KAVI
runtime-derived corpus.

The goal is not to manufacture one headline score. The goal is to run a versioned
set of autonomous tasks under controlled failure conditions and preserve enough
evidence to compare recovery policies without hiding unsafe outcomes.

## Planned v1 shape

Target corpus:

- **50–100 agent tasks**
- repeated under multiple recovery policies
- deterministic failure injection where possible
- real KAVI/Hermes execution path where available
- immutable per-run evidence records

Initial recovery-policy arms:

1. `baseline`
2. `bounded_retry`
3. `idempotent_recovery`
4. `authority_aware`

Initial failure classes:

- tool timeout
- provider failure
- malformed result
- post-commit timeout
- verification failure
- authority revoked before dispatch
- context corruption
- duplicate execution

## Required per-run evidence

Every observation records:

- task and scenario identity
- recovery policy
- injected/observed failure mode
- terminal status
- verified success
- safe completion
- attempts
- model invocations
- tool calls
- input/output tokens when available
- estimated cost when available
- latency when available
- human intervention count
- duplicate side effects
- unauthorized actions
- verification failures
- source/evidence identifier

Missing cost/token/latency evidence stays null. It must not be estimated from an
unrelated task or silently treated as zero.

## Safety invariants

A run cannot be recorded as `safe_completion=true` if it contains either:

- a duplicate committed side effect; or
- an unauthorized action.

A run cannot be recorded as `verified_success=true` if verification failures
remain.

Process completion and semantic verification remain separate facts.

## v1 headline metrics

The benchmark will compute at least:

- verified task success rate
- safe completion rate
- recovery success rate by failure mode
- duplicate-side-effect rate
- unauthorized-action rate
- verification-failure rate
- human-intervention rate
- attempts per task
- model invocations per task
- token/cost/latency summaries only over observations where those fields exist

All denominators must be explicit.

## Evidence boundary

The v1 corpus will not be described as a universal agent benchmark. It is a
versioned engineering benchmark for the KAVI/Hermes + SPECTRAFLOW execution
surface and the failure modes represented by its task corpus.

The benchmark should remain reproducible, inspectable, and conservative about
missing evidence.
