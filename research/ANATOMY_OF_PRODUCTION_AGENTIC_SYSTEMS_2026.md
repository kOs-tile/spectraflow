# The Anatomy of a Production-Grade Agentic System
## Layers, Loops, Graphs, Memory, Authority, and Verifiable Execution

**KAVI Engineering Research Note 01 — October 12, 2026**  
**Status:** Open engineering synthesis and reference architecture. **Not** peer-reviewed original experimental research; not a claim that the whole diagram is deployed at KAVI.  
**Keywords:** agent systems, orchestration, durable execution, context engineering, memory, knowledge graphs, multi-agent coordination, capability security, evaluation, reliability.

> **Thesis:** The model is one component in an agentic system. The larger engineering problem is coordinating decisions, state, authority, evidence, and recovery across time and tools. More agents or a larger context window do not remove this responsibility.

### Abstract

An LLM can propose tool calls. It cannot, by itself, guarantee that a task belongs to the current actor; that the user's authorization is still valid; that a previous call has not already committed a payment; that retrieved knowledge is current; or that an apparently completed process actually achieved the objective. Long-running agent systems therefore need multiple independently governed state and control boundaries.

This reference architecture organizes the system into **14 layers** and **seven feedback loops**, distinguishes **five different graphs**, and defines memory as a provenance- and policy-governed subsystem rather than a vector database. Its central proposal is **shared state, not shared transcripts** for coordinating heterogeneous harnesses. The design uses deterministic enforcement where correctness is required and model-driven reasoning where flexible planning is valuable.

The approach synthesizes [Anthropic's workflow/agent patterns][anthropic-agents], [CoALA's cognitive architecture][coala], [Microsoft GraphRAG][graphrag], [OpenAI's Agents SDK][openai-sdk], [MCP's 2026-07-28 specification][mcp-2026], and [durable workflow engineering][temporal-arch]. It then identifies runnable public KAVI/KCC/SPECTRAFLOW artifacts that exercise **narrow** aspects of the design. No universal success-rate, production deployment, or independent security guarantee is inferred from these examples.

---

## 1. When should software be an agent?

Start with a decision rule rather than a framework. If a deterministic function or conventional workflow can accomplish a task at lower risk and cost, use it. A dynamic agent is warranted when the next action depends on observations not known at design time, and when tooling, evaluation, and bounded autonomy justify the additional uncertainty.

| Approach | Planner | Good fit | Primary failure mode |
| --- | --- | --- | --- |
| Function or script | Code | Fixed transformations and clear APIs | Incorrect code |
| Deterministic workflow | Explicit DAG/state machine | Stable multi-step processes | Broken transition or integration |
| Agent inside a workflow | Model within fenced states | Dynamic research/extraction, bounded tools | Bad decisions at boundaries |
| Autonomous agent loop | Model selects evolving actions | Open-ended discovery and implementation | Loops, cost, unauthorized effects |
| Multi-agent / multi-harness | Multiple specialized decision-makers | Separable domains with explicit ownership | Duplicate work, state divergence, role confusion |

The often-cited patterns **prompt chaining, routing, parallelization, orchestrator–workers, and evaluator–optimizer** are useful *components*, not mandates to create an orchestration hierarchy [1]. Design from task contracts and stopping conditions backward.

## 2. The complete reference stack — 14 layers

~~~mermaid
flowchart TD
    A["L01 Intent, identity & consent"] --> B["L02 Task contract & acceptance criteria"]
    B --> C["L03 Workflow graph & durable control plane"]
    C --> D["L04 Context compiler / retrieval"]
    D --> E["L05 Planner / model routing"]
    E --> F["L06 Capability discovery & registry"]
    F --> G["L07 Authority compiler / approval boundary"]
    G --> H["L08 Tool dispatcher + idempotency"]
    H --> I["L09 Environment / services / external APIs"]
    I --> J["L10 Observation and semantic verification"]
    J --> K["L11 Recovery / compensation / continuation"]
    K --> C
    D <--> M["L12 Memory & knowledge graph"]
    C <--> N["L13 Multi-actor shared task state"]
    C --> O["L14 Traces, evaluation, economics & governance"]
    H --> O
    J --> O
    M --> O
~~~

The downward path is a simplified teaching projection. Real implementations include asynchronous queues, multiple feedback edges, and trust boundaries. A diagram is **not** proof that all layers are implemented.

### L01 — Intent, identity and consent

Capture the *requester*, *tenant*, *principal*, requested outcome, data classification, and authorization source. Separate an instruction from permission. Prompt text arriving from a web page or repository is lower-trust than system policy and user approvals. Bind every later tool request to task and actor identities, not merely a conversation.

### L02 — Task contract

Normalize the goal into an immutable task specification: inputs, outputs, measurable acceptance predicates, risk category, required approvals, deadline, cost ceiling, tool budgets, data scope, and cancellation policy. A human-visible contract matters more than a verbose agent persona. Store a version/hash so retries cannot silently reinterpret the job.

### L03 — Workflow graph and control plane

Use explicit states such as **READY → CLAIMED → RUNNING → VERIFYING → PASS / FAILED / NEEDS_APPROVAL**. State transitions require ownership checks, optimistic concurrency or leases, and an append-only history. A scheduler or watcher claims ready tasks; workers do not decide their own eligibility. A workflow engine or a carefully implemented database queue can provide this layer. Replay rules matter: nondeterministic I/O and LLM calls should be isolated from deterministically replayable workflow logic [7].

### L04 — Context compiler

Construct a *bounded task-specific packet*, not a concatenation of all chat histories. Inputs may include task contract, recent observations, compacted working notes, relevant memory, verified documents, permitted tool signatures, and capability constraints. Rank sources by relevance, freshness, trust and token cost, with explicit provenance. Context assembly is a cyclic function of state; it is **not** memory itself [2].

### L05 — Decision and model routing

Use a **plan–act–observe** policy with uncertainty and budget controls. Route deterministic operations away from an LLM. For model steps, select provider and reasoning effort by task difficulty, latency, cost and risk; do not treat a large model as an authorization layer. The planner proposes *intent and actions*. It cannot authorize its own side effects. Routing decisions and model versions become trace data.

### L06 — Capability discovery

Adapters normalize tools from MCP, API schemas, framework function definitions or internal systems into capabilities: identity, schema, side effect, environment, tenant and trust class. Metadata is evidence, not security policy. Tool lists may change; use inventory/version locks to detect drift. MCP's 2026-07-28 release adds a stateless protocol core and authorization refinements, but transport-level authentication is distinct from application-level least authority [5,6].

### L07 — Authority compiler and approval boundary

Reduce broad tool inventories to **task-scoped, operation-scoped and parameter-scoped** rights. Deny unknown or unsupported authority. At dispatch, verify capsule validity, inventory drift, expiry, current approval/revocation status and the *actual arguments*. For sensitive actions, stop for a host-controlled human decision. An agent's statement "approved" is never itself approval.

**KAVI reference:** [KAVI Capability Compiler](https://github.com/kOs-tile/kavi-capability-compiler), its [five-minute dispatcher demo](https://github.com/kOs-tile/kavi-capability-compiler/blob/main/examples/recruiter_demo.py) and [SDK trust-boundary documentation](https://github.com/kOs-tile/kavi-capability-compiler/blob/main/docs/SDK_COMPATIBILITY.md) exercise a subset of this boundary. The published stable release and development-branch capabilities must be distinguished.

### L08 — Dispatcher, idempotency and commit ledger

The dispatcher is host-owned. Route every protected side-effecting call through authority checks and a stable **operation identity**. Persist an idempotency key (e.g. task + action + intent-version) together with the receipt of a completed action. A request can time out *after* an external system commits; blind retry may duplicate it. The proper response may be **reconcile first**, not retry. Even durable workflow systems distinguish at-least-once behavior from exactly-once effects [8].

For integrations that cannot support idempotent operations, use an outbox and reconciliation journal, explicit compensation plans, and operator escalation. **Exactly-once intent is not the same as globally exactly-once side effects.**

### L09 — External environment

Services, browsers, databases, terminals, payment providers, filesystems and code runners are where outcomes occur. Treat the environment as an independent source of truth, not a passive text generator. For browser actions, validate the resulting page or persisted record instead of trusting the agent's narration. Record external request IDs and independently verifiable receipts where available.

### L10 — Semantic verifier

Process exit code zero, a well-formed model response, and task completion are separate facts. Run deterministic acceptance checks when possible: file checksum, passing tests, database row/state, document version, confirmation ID, user-approved content. Keep **COMPLETED_UNVERIFIED** distinct from **PASS**. The OpenAI Agents SDK traces model calls, tools, guardrails and handoffs, but traces alone do not prove the external objective was achieved [4].

### L11 — Recovery and continuation

Classify the failure *before* consuming another attempt:

- **Pre-model / pre-dispatch:** often safe to retry with a bound; no external effect occurred.
- **Post-dispatch / response unknown:** reconcile the external effect and idempotency receipt before deciding.
- **Semantic verification failure:** preserve the failed artifact and evidence; produce a new task/continuation if approved.
- **Authority expired/revoked:** stop, do not route to a more permissive actor.
- **Budget exhausted, invalid contract or irreconcilable state:** terminal failure and escalation.

Recovery is bounded by maximum attempts, elapsed time, spend and side-effect risk. Persist original failures; never change a failed historical record into a success to simplify dashboards.

### L12 — Memory and knowledge

Memory is information with a lifecycle, not a universal text buffer. Details in §4. Long-term memory must have source pointers, timestamps, uncertainty, write permissions, conflict handling, retention and deletion semantics. Neither a recalled statement nor a knowledge-graph edge grants execution authority.

### L13 — Multi-actor coordination

Different harnesses (a cloud research assistant, local coding runner, workflow engine) can operate on shared resources without sharing full conversations. Maintain a shared **task/state ledger** and scoped context packets. Assign single-writer ownership per task or resource, while making observations and approved artifacts readable to other authorized actors. Handoffs have a *contract*, not just a notification.

### L14 — Observability, evaluation and economics

Trace task → attempt → model call → tool decision → dispatch → receipt → verification. Track action-denial reason, retries, intervention, latency, token cost, external cost, and quality. Grade outcome against environment state. Evaluate both autonomous and human-assisted results, with confidence intervals and source provenance. Never report "100% reliability" from a few internal synthetic tests [9].

## 3. Seven loops with different objectives

~~~mermaid
flowchart LR
    T[Task contract] --> P[Plan]
    P --> A[Act through Guard]
    A --> O[Observe]
    O --> V[Verify]
    V -->|Incomplete / safe continuation| P
    V -->|Accepted| F[PASS]
    V -->|Risk / uncertainty| H[Human decision]
    H -->|Approved| P
    O --> X[Trace + evaluation]
    X --> Q[Offline improvement]
    Q --> C[Versioned policy / tests]
~~~

| Loop | Cycle | Owner | Stop criterion |
| --- | --- | --- | --- |
| 1. Decision | observe → reason → choose action | Agent | Next bounded action or terminate |
| 2. Execution | authorize → dispatch → receipt | Host dispatcher | Verified side effect / veto |
| 3. Task orchestration | claim → execute → verify → terminal | Control plane | PASS, FAILED or review |
| 4. Recovery | classify → reconcile → retry/continue | Reliability policy | Retry/cost/risk limit |
| 5. Context | retrieve → rank → compile → compact | Context subsystem | Token/time budget |
| 6. Memory | propose → validate → commit → retrieve → supersede | Memory governance | Provenance/admission rules |
| 7. Improvement | trace → evaluate → regression → release | Engineering team | Evaluated version promotion |

A critical anti-pattern is treating the model's inner reasoning loop as if it subsumed the scheduler, verifier, memory writer and authority manager. Those loops have distinct owners and failure modes.

## 4. Memory engineering: storage, admission, retrieval, revision

The CoALA framework organizes language agents around modular memory, actions and decision-making [10]. A useful production decomposition is:

| Memory or state | Scope | Backing store | Write policy | Read/retrieval behavior |
| --- | --- | --- | --- | --- |
| Working context | Current inference step | Model context / bounded scratchpad | Ephemeral compiler | Only high-signal task facts |
| Task/operational state | Durable run and transitions | SQL/event log/checkpoints | Control-plane-owned CAS/transaction | Exact task/attempt lookup |
| Episodic memory | What happened and when | Append-only event/event-summary store | Verified experience ingestion | Similar-task + recency + provenance |
| Semantic knowledge | Facts, entities, relationships | Relational + vector + graph index | Evidence-gated merge/supersession | Hybrid exact, semantic, graph traversal |
| Procedural memory | How to execute correctly | Version-controlled playbooks/skills | Review + tests + owner approval | Load applicable, signed/versioned procedure |
| Artifact store | Large outputs and source documents | Object storage / Git | Immutable version + content hash | Direct reference with permission checks |
| Preference/policy | User or organizational constraints | Auditable policy store | Authorized explicit change | Highest-priority applicable version |

**Memory-writing pipeline:**

~~~mermaid
flowchart LR
    I[Observed event] --> N[Normalize]
    N --> P[PII / trust / provenance]
    P --> D{Admission policy}
    D -->|Reject| X[Quarantine or discard]
    D -->|Accept| C[Conflict resolution]
    C --> S[Versioned write + source edges]
    S --> R[Retrieval index]
    R --> F[Context compiler]
~~~

**Memory invariants:** retrieved data must be filtered by tenant and permissions; memories have recorded origin, creation and observed-at timestamps; derived facts retain evidence links; lower-trust documents cannot write high-trust instructions; user correction or deletion must propagate through indexed copies subject to legal retention; conflicting statements are not resolved by embedding similarity alone. Historical "what an agent believed" can coexist with a newer authoritative fact, but neither should masquerade as timeless truth.

### Retrieval as a budgeted decision

A useful conceptual retrieval score is:

**utility = relevance × source confidence × freshness × access eligibility − token/latency cost**.

This is an *engineering objective*, not a calibrated probability formula. Filter permissions **before** ranking. Combine lexical match for exact IDs, vector similarity for semantic paraphrases, graph traversal for relations, and structured database queries for authoritative state. Require citation-quality source pointers on facts that may affect action. Use just-in-time retrieval rather than filling the entire prompt up front [2].

### Compaction without evidence loss

When the context window fills, keep task ID, contract, current terminal state, remaining budget, decisions, source pointers, approvals and open blockers. Compress verbose tool outputs, not immutable audit events. A summary is a *derived representation* and must not overwrite raw evidence. A resumed agent should use a fresh context pack from durable truth, not trust the last autogenerated summary blindly.

## 5. Graph engineering: not every graph is a knowledge graph

A robust agent stack may contain **five graphs**:

1. **Workflow graph:** directed transitions, conditional branches, parallel joins, wait states, terminal predicates. Nodes represent **work**.
2. **Knowledge graph:** entities, facts, edges, time-bounded claims, provenance and confidence. Nodes represent **things/claims**.
3. **Task dependency graph:** task-level partial order, critical path, unlock conditions, resource contention, join criteria.
4. **Authority graph:** principals, roles, capabilities, scopes, approvals, delegation and revocation edges. This graph describes possible permission relations; it is **not** a replacement for pre-dispatch enforcement.
5. **Trace/causality graph:** spans, task attempts, tool calls, handoffs, external receipts, failures and verification edges.

Confusing these graphs causes design errors. A fact that "user manages store A" should not become an authority edge permitting a charge. A dependency edge saying "analyze before publish" should not be treated as semantic knowledge.

### Knowledge graph schema example

~~~text
(:Entity {entity_id, canonical_name, tenant_id, type})
(:Claim  {claim_id, predicate, value, observed_at,
          valid_from, valid_to, confidence, status})
(:Source {source_id, uri, digest, classification, observed_at})
(:Task   {task_id, version, owner, state})

(Entity)-[:SUBJECT_OF]->(Claim)
(Claim)-[:OBJECT_ENTITY]->(Entity)
(Claim)-[:SUPPORTED_BY]->(Source)
(Task)-[:DERIVED_FROM]->(Source)
(Claim)-[:SUPERSEDES]->(Claim)
~~~

For fact extraction, identify candidate entities, normalize aliases, create *claims* rather than unsourced edges, attach source span/hash and time scope, check constraints and contradictions, then write an index. A graph query should return both an answer and its source paths. Microsoft GraphRAG uses an entity knowledge graph and community summaries for corpus-level questions; that research concerns retrieval over documents, **not** authorization or workflow execution [11].

### Graph quality and lifecycle

Track entity resolution precision, unsupported edge rate, provenance coverage, conflict detection, temporal validity, deletion propagation and retrieval utility. Prefer relational tables for transactional truth and invariants; use a graph where traversals and relationships improve the query. Do not reflexively deploy Neo4j merely because the system has agents.

## 6. Multi-harness control plane: shared state, not shared context

A practical architecture for ChatGPT/Work, a separate local agent runtime, and specialist automation harnesses:

~~~mermaid
flowchart TB
    U[User / operator] --> CP[Task & authority control plane]
    CP --> DB[(Task / event / approval ledger)]
    DB --> CC[Bounded context packs]
    CC --> W[Work harness]
    CC --> C[Code harness]
    CC --> S[Specialist harness]
    W --> B[Evidence / artifact bus]
    C --> B
    S --> B
    B --> DB
    CP --> A[Guard / human approval]
    A --> D[Host dispatcher]
    D --> EX[External systems]
    EX --> B
~~~

The agents should share **task IDs, bounded plans, scoped evidence references and resource status**, not full transcripts. A handoff contract contains:

~~~json
{
  "task_id": "task-123",
  "task_version": 4,
  "owner_actor": "code-harness",
  "requested_effects": ["read", "write:repo/demo-only"],
  "capability_scope_ref": "capsule-or-approval-id",
  "input_artifact_refs": ["sha256:..."],
  "acceptance_test_refs": ["tests/test_demo.py"],
  "retry_budget": 2,
  "deadline_at": "ISO-8601 timestamp",
  "expected_terminal_evidence": ["test_output", "commit_sha"],
  "trace_parent": "trace-id"
}
~~~

**Ownership rules:** one actor holds an active lease; other actors can observe or propose continuations; expired leases require reconciliation; resource-scoped locks prevent overlapping writes; a handoff cannot widen authority; approval comes from an independent operator interface; callbacks from disconnected external tools can change state only through authenticated update contracts. Idempotency keys are preserved within retries of *the same external operation*, while continuation tasks receive new task IDs and explicit parentage.

This is a design proposal, not a claim that a live cross-product control plane already coordinates every named harness.

## 7. Correctness around retries and external side effects

Consider this timeline:

1. Tool dispatcher sends external request with an operation key.
2. External API commits a side effect.
3. The response is lost; caller observes a timeout.
4. A naïve worker retries the operation.
5. Without a stable deduplication contract, a second effect commits.

The safe path **queries the receipt/state** or retries with a stable external idempotency key recognized by the remote service. An internal SQLite unique key alone does not guarantee safety against an unrelated external processor.

~~~python
# Illustrative pseudocode — not a production implementation.
def execute(task, action, host):
    assert task.owner == host.actor
    assert task.state == "RUNNING"
    assert task.retry_budget >= 0
    operation_id = stable_hash(task.id, task.intent_version, action.id)

    with transactional_claim(operation_id) as claim:
        if claim.has_verified_receipt:
            return claim.receipt
        authority = compile_or_load_current_authority(task, action)
        authority.require(action.capability, action.parameters)
        if claim.may_have_committed_without_receipt:
            return reconcile_or_escalate(operation_id)
        receipt = host.dispatch(action, idempotency_key=operation_id)
        persist_receipt_and_observation(operation_id, receipt)

    return verify_against_acceptance_contract(task, receipt)
~~~

This pseudocode is deliberately incomplete around locks, races, crashes, cross-service transactions and malicious hosts. A real implementation needs property tests and fault injection at every interruption point. AWS and Temporal document the retry/idempotency distinction explicitly [8,12].

## 8. Security and trust boundaries

The model, retrieved documents, MCP servers, external pages and even tool output may all contain adversarial or incorrect instructions. Threat-model these boundaries separately:

| Attack/failure | Defensive contract | Acceptance test |
| --- | --- | --- |
| Prompt injection in retrieved result | Treat content as data, prohibit privilege escalation | Injection cannot alter authorization |
| Tool metadata drift | Bind inventory and schema fingerprints | Changed schema blocks until reviewed |
| Stale approval or revoked task | Recheck host-owned current authority at dispatch | Revoked call makes zero external dispatches |
| Cross-tenant memory leakage | Filter access before search/retrieval | Other tenant's fact never appears |
| Arbitrary skill/plugin execution | Signed/versioned procedure and execution policy | Unreviewed skill cannot gain broad effect |
| Unsafe retry | Stable external idempotency / reconciliation | One intended effect despite lost acknowledgement |
| Actor spoofing | Authenticated actor and scoped claim | Wrong actor cannot take a lease |
| Model "PASS" hallucination | Independent semantic verifier | Output claim without receipt is unverified |

Least authority is a continuous **compile → verify → dispatch** boundary, not a broad "AI safety" system prompt. MCP 2026 documents tool input validation, access control, confirmation, and auditable operations as responsibilities of server/client implementations [6].

## 9. Verification and evidence: process is not outcome

An engineering-ready acceptance envelope should contain:

~~~json
{
  "task_id": "task-123",
  "contract_hash": "sha256:...",
  "actor_id": "worker-code-01",
  "attempt": 1,
  "model_invocations": 1,
  "process": {"status": "exited", "code": 0},
  "acceptance": {"status": "pass", "verifier_version": "v3"},
  "tool_receipts": [{"operation_id": "op-01", "receipt_hash": "sha256:..."}],
  "artifact_hashes": ["sha256:..."],
  "approved_authority_ref": "capsule-or-grant-id",
  "terminal_status": "PASS"
}
~~~

A process exit without an acceptance result means **COMPLETED_UNVERIFIED**. A timeout without a receipt means **EXTERNAL_EFFECT_UNKNOWN** if any side effect may have committed. An agent assertion is not substitute evidence. The distinction mirrors rigorous multi-turn agent evaluations that inspect environment outcomes rather than narrative responses [9].

## 10. Evaluation, experiments and cost

Publish **three separate evidence classes** rather than blending them:

- **Synthetic deterministic fault tests:** intercept calls, inject pre-/post-commit timeouts, assert exact effect and dispatcher counts.
- **Historical runtime-derived records:** sanitized metadata with source revision and available fields. Do not infer missing commits, token cost or intervention events.
- **Prospective live trials:** pre-registered tasks, fault profiles, policy variants and graders; report attempted/valid/excluded runs, confidence intervals, costs and any failed safety gates.

### Proposed 80-trial experiment — not completed evidence

A proposed experiment might cross **10 tasks × 2 fault profiles × 4 retry/verification policies = 80 runs**. Record random seed, model/version, authority constraints, worker health, fault timing, trace IDs and true external receipts. Promotion requires runner-hook attestation, safety canary, fail-closed controls and independent audit. Until the runs and receipts exist, do **not** publish a success percentage.

### Evaluation scorecard

| Dimension | Minimal metrics |
| --- | --- |
| Semantic correctness | Verified PASS / eligible tasks; unverified completion rate |
| Side-effect safety | Duplicate committed effects; unauthorized dispatches |
| Reliability | Pre-/post-model failures, retry exhaustion, time to reconcile |
| Autonomy | Human intervention count and type, successful unattended completion |
| Economic efficiency | Token usage, model cost, tool cost, cost per verified task |
| Memory quality | Provenance coverage, conflicting-claim resolution, stale retrieval |
| Security | False-safe authorization decisions, drift detection, approval bypass |
| Performance | p50/p95 latency, queue wait, tool-round-trip time, tail retries |

For every metric, report denominators, exclusions, confidence intervals when appropriate, the difference between synthetic and live distributions, and the source commit/fixture version.

## 11. How the KAVI public evidence maps to this design

**These are scoped observations from repositories and their documented benchmarks, not proof of the entire target architecture.**

| Evidence | What it exercises | What it does not prove |
| --- | --- | --- |
| [KCC five-minute demo](https://github.com/kOs-tile/kavi-capability-compiler/blob/main/examples/recruiter_demo.py) | Granted read, denied calls before host dispatch, approval-required handoff | Autonomous production security across unmediated paths |
| [SPECTRAFLOW reliability demo](../examples/reliability_interview_demo.py) | Local synthetic actor ownership, immutable failure, continuation and bounded retries | The actual live KAVI control-plane implementation |
| [Synthetic execution traces](../benchmark/execution_traces.py) | Blind post-commit retry duplicates effect; stable idempotency avoids the duplicate within the local ledger | External service idempotency or live business results |
| [Sanitized KAVI task fixture](../benchmark/fixtures/kavi_runtime_trace_v0.json) | Historical outcome classification: 8 records, 2 verified PASS, 2 completed unverified, 4 failures | Real dispatcher call count or committed side-effect totals |
| [SPECTRAFLOW validation](../docs/RELIABILITY_VALIDATION.md) | Reproducible methods, known benchmark limitations | Completed 80-run live comparison |

Do not treat *design direction*, *merged executable regression* and *observed production task run* as interchangeable statuses. In particular, the 8 historical records support bounded classification claims only; they do not establish the operational uptime of the wider Hermes system.

## 12. A concrete implementation sequence

1. **Single-task baseline.** Define one task contract, one actor, one host-owned tool dispatcher and a deterministic verifier.
2. **Persistence.** Add append-only events, task/attempt identity, atomic claims, leases and explicit terminal classes.
3. **Authority.** Normalize registry and enforce a task-scoped pre-dispatch capability guard; approval and revocation are host-owned.
4. **Recovery.** Test every crash window, especially commit-before-acknowledgement; bound retries and reconcile external receipts.
5. **Context.** Add a small retrieved context packet with source lineage; resist adding memory until a measured case warrants it.
6. **Memory.** Introduce separate task state, episodes, facts and procedures; enforce tenant access and revision provenance.
7. **Graphs.** Use a workflow DAG for branching and an optional knowledge graph only where relationships improve retrieval.
8. **Multiple actors.** Add scoped handoffs, task ownership and shared state without transcript replication.
9. **Evaluation.** Run deterministic fixtures, adversarial traces, negative controls and longitudinal live comparisons.
10. **Promotion.** Require independent review, operator rollback, measurable costs and versioned evidence before wider authority.

### Operational release gates

A release should fail if any of these are true: unbounded tool authority; non-idempotent external retries without reconciliation; missing persisted terminal verifier; inability to distinguish completed from accepted; no ownership check; unrecoverable task state; missing provenance on sensitive memory; unexplained CI failures; leakage of secrets/raw private transcripts; or undocumented human intervention.

## 13. Open research questions and falsifiable hypotheses

These are proposals for future research, not results:

- **H1:** task-scoped context packets lower cost and contradiction rate versus transcript sharing at equal task success, on pre-registered multi-harness tasks.
- **H2:** a hybrid SQL + vector + knowledge-graph retrieval policy improves provenance-correct multi-hop answers over vector-only retrieval, after accounting for extraction errors and cost.
- **H3:** external receipt reconciliation cuts duplicated committed effects after post-commit timeouts relative to bounded blind retry.
- **H4:** semantic terminal verification reduces false-PASS reporting without an unacceptable increase in human escalations.
- **H5:** current-authority rechecking materially reduces stale approval use in long-running workflows.
- **H6:** separate memory admission, provenance and supersession reduce high-impact stale or contradictory recalls over a shared unstructured vector store.

Each hypothesis needs a source-bound corpus, ablation baseline, explicit negative controls, uncertainty reporting and reproducible runner. A concept diagram is not experimental evidence.

## 14. Conclusions

The most useful architectural shift is to stop treating the agent's prompt as the location of all system responsibility. A resilient stack keeps **decisions flexible**, but makes **authority, state, side effects, memory admission and acceptance evidence explicit and testable**.

A knowledge graph can explain relationships; a workflow graph can explain execution order; a memory store can recall information; an LLM can propose actions. **None of them independently proves authority to act or that a requested outcome occurred.** Those guarantees come from carefully constructed boundaries and independently observable evidence.

If a system cannot answer *who owned this action, under which grant, using what source information, with which external receipt, and how the result was verified*, then the next engineering priority is not another agent. It is a stronger contract.

---

## Sources and further reading

1. [Anthropic, *Building effective agents* (Dec 2024)][anthropic-agents] — workflows vs autonomous agents; routing, parallelism and evaluator–optimizer.
2. [Anthropic, *Effective context engineering for AI agents* (Sep 2025)][context].
3. [Anthropic, *Managing context on the Claude Developer Platform* (Sep 2025)][context-memory].
4. [OpenAI, *Agents SDK and tracing documentation* (2026)][openai-sdk] and [tracing details][openai-tracing].
5. [MCP maintainers, *2026-07-28 Specification announcement*][mcp-release].
6. [Model Context Protocol, *2026-07-28 Tools*][mcp-2026] and [Authorization][mcp-auth].
7. [Temporal, *AI agent reference architecture*][temporal-arch] and [AI cookbook][temporal-cookbook].
8. [AWS, *Idempotency and retries*][aws-idempotency].
9. [Anthropic, *Demystifying evals for AI agents* (Jan 2026)][evals].
10. [Sumers et al., *Cognitive Architectures for Language Agents* (CoALA)][coala].
11. [Edge et al., *From Local to Global: A Graph RAG Approach* (2024)][graphrag].
12. [Temporal, *Nexus operations: execution semantics and idempotency*][temporal-idempotency].
13. [LangChain, *LangGraph learning guides*][langgraph].

**Methodology note:** This is a narrative engineering synthesis based on documentation, research papers, public code and bounded KAVI artifacts; it is **not** a systematic literature review. Claims about KAVI demonstrations are grounded in the linked source/test contracts, not on presumed production adoption.

[anthropic-agents]: https://www.anthropic.com/engineering/building-effective-agents
[context]: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
[context-memory]: https://claude.com/blog/context-management
[openai-sdk]: https://developers.openai.com/api/docs/guides/agents/sdk
[openai-tracing]: https://openai.github.io/openai-agents-js/guides/tracing/
[mcp-release]: https://blog.modelcontextprotocol.io/posts/2026-07-28/
[mcp-2026]: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx
[mcp-auth]: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/authorization/index.mdx
[temporal-arch]: https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture
[temporal-cookbook]: https://docs.temporal.io/ai/cookbook
[aws-idempotency]: https://docs.aws.amazon.com/durable-execution/patterns/best-practices/idempotency/
[evals]: https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
[coala]: https://arxiv.org/abs/2309.02427
[graphrag]: https://www.microsoft.com/en-us/research/publication/from-local-to-global-a-graph-rag-approach-to-query-focused-summarization/
[temporal-idempotency]: https://docs.temporal.io/nexus/operations
[langgraph]: https://docs.langchain.com/oss/python/learn
