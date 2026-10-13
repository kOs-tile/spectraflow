# Reproduce, Break, Improve — KAVI Engineering Review Guide

**Status:** Open request for independent engineering scrutiny, October 2026. No external review results are claimed.

A practical challenge for engineers reviewing agent execution systems, designed to take **15–30 minutes** for a first pass. The diagrams and research may be persuasive, but the source-backed invariants below are what a reviewer should try to falsify.

## 1. Reproduce the two narrow demos

### Capability security — KCC

```bash
git clone https://github.com/kOs-tile/kavi-capability-compiler.git
cd kavi-capability-compiler
python -m pip install https://github.com/kOs-tile/kavi-capability-compiler/releases/download/v0.1.1/kavi_capability_compiler-0.1.1-py3-none-any.whl
python examples/recruiter_demo.py
```

[Exact runbook and evidence](https://github.com/kOs-tile/kavi-capability-compiler/blob/main/docs/DEMO_5_MINUTES.md) · [Source](https://github.com/kOs-tile/kavi-capability-compiler/blob/main/examples/recruiter_demo.py).

**Try to break:** an unsupported parameter, different customer ID, expired or stale grant, approval-required call, host bypass, tool metadata/schema drift. A blocked operation must trigger **zero actual host dispatches**. This demo uses fake customer records and a deliberately controlled host callback; it does **not** establish a system-wide production security guarantee.

### Recovery and evidence — SPECTRAFLOW

```bash
git clone https://github.com/kOs-tile/spectraflow.git
cd spectraflow
python examples/reliability_interview_demo.py
python -m pytest -q tests/test_reliability_interview_demo.py
```

[Five-minute runbook](../docs/RELIABILITY_INTERVIEW_DEMO.md) · [Synthetic execution trace evaluator](../benchmark/execution_traces.py) · [Sanitized historical metadata evaluator](../benchmark/kavi_runtime_traces.py).

**Try to break:** a completion incorrectly classified as PASS, a concurrent actor taking another owner's task, retry after post-commit timeout that duplicates an external effect, mutation of terminal failure history, a continuation that broadens authority, or missing error path where a receipt may be unknown.

The evidence sources here are **separate**. An illustrative SQLite control plane runs 12 assertions; the synthetic callback/ledger benchmark demonstrates duplicate post-commit effects; sanitized historical KAVI metadata contains **8 records** (2 verified PASS, 2 completed-unverified, 4 explicit failures). The historical fixture contains **no actual committed-side-effect counts**. Do not infer them.

## 2. Inspect the complete reference architecture

[The Anatomy of a Production-Grade Agentic System](../research/ANATOMY_OF_PRODUCTION_AGENTIC_SYSTEMS_2026.md)

Its 14 layers, seven loops and five graph types are an **engineering proposal and synthesis**, not evidence that every component is deployed in one system. Reviewers can focus on:

- **Authorization:** does a task-scoped grant survive all handoffs or can an alternate tool route bypass it?
- **State and recovery:** what happens across the crash windows before dispatch, after external commit, and before receipt persistence?
- **Graph and memory:** who can write facts, which claims require provenance, and how are stale or conflicting facts superseded?
- **Evaluation:** can the system separate process exit, completion, independent acceptance and unknown external effect?
- **Multi-harness coordination:** can agents share task IDs and bounded state without transcript dumping or two independent writers?

## 3. Submit a falsifiable finding

**Security / KCC:** [Open issue #103](https://github.com/kOs-tile/kavi-capability-compiler/issues/103)

**Reliability / SPECTRAFLOW:** [Open issue #51](https://github.com/kOs-tile/spectraflow/issues/51)

A useful report includes: repository commit or release, synthetic input/fixture, reproduction command, expected result, observed result, and **the exact trust or correctness invariant violated**. If you propose a fix, include a failing test first. Please do not upload credentials, customer data, raw transcripts, or live side-effect credentials.

## 4. Limits, publication and status

- No guarantee of external collaboration, bounty or security-advisory response times.
- Neither project should claim an independent audit simply because a review issue is open.
- SPECTRAFLOW's proposed prospective 80-run experiment remains **uncompleted evidence** until verifiable trace/receipt data supports results.
- Engineering research and interview-ready demos are not replacements for real customer adoption, production uptime metrics or independent red-team work.
- The [public portfolio](https://kavi-engineering-evidence.vercel.app/) is a visual entry point; code and tests remain authoritative.

**The most useful feedback isn't a compliment. It's one counterexample that changes the implementation or its claims.**
