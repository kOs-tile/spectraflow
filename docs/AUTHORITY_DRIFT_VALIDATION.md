# Authority drift validation

SPECTRAFLOW's active differentiation is not generic LLM tracing. The authority
wedge asks a narrower production question:

> Did the runtime call stay inside the authority represented by the KCC capsule
> that the caller claimed was active?

This layer is observational. It does not replace KCC enforcement and it never
grants authority. Current observations are evaluated through KCC's public
`authorize_call()` primitive from the immutable v0.1.1 release; SPECTRAFLOW does
not reimplement the KCC authorization rules and never supplies a dispatcher.

## Failure taxonomy

| Code | Meaning | Severity |
|---|---|---|
| `AUTH-CAPSULE-INTEGRITY` | Capsule content no longer matches its canonical ID | critical |
| `AUTH-CAPSULE-ID-MISMATCH` | Runtime correlation ID differs from supplied capsule | critical |
| `AUTH-CAPSULE-EXPIRED` | Call occurred after capsule expiry | critical |
| `AUTH-CAPABILITY-NOT-GRANTED` | Observed capability is absent from grants | critical |
| `AUTH-OPERATION-NOT-GRANTED` | Observed operation exceeds the grant | critical |
| `AUTH-PARAM-*` | Observed parameter violates a bound encoded in the capsule | high |
| `AUTH-CAPSULE-VERSION` | Capsule contract is not the supported `kcc.capsule.v1` schema | high |

## Primary metrics

- authority-drift recall on labeled violating observations
- false-positive rate on labeled within-authority observations
- capsule-tamper detection recall
- expiry detection recall
- capability/operation/parameter boundary detection recall
- observation fingerprint determinism

The synthetic adversarial benchmark is intentionally small and contract-focused.
It is not evidence of production incident detection accuracy across arbitrary
agent systems.

## Exit gate

The authority-drift slice is ready for KAVI integration when:

1. every current KCC v1 violation class represented in the benchmark is detected;
2. within-authority calls remain clean;
3. tampered capsules fail closed;
4. the evaluator remains observational and cannot grant or dispatch authority;
5. integration tests bind real KCC-produced `kcc.capsule.v1` artifacts to SPECTRAFLOW observations;
6. SPECTRAFLOW delegates authority decisions to KCC's public primitive and performs zero host dispatches.
