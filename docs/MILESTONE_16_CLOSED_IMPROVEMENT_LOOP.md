# Milestone 16 — First closed improvement loop

Milestone 16 demonstrates the smallest complete Harness X system-level self-improvement cycle while keeping foundation-model weights unchanged.

## Closed loop

```text
recorded gate behavior
        ↓
grounded self-analysis
        ↓
evidence-linked bounded proposal
        ↓
static candidate qualification
        ↓
matched sandbox experiment
        ↓
configured live-promotion policy
        ↓
atomic active-config version swap
        ↓
post-activation verification
        ↓
future self-analysis on promoted version
```

The first target is the deterministic maintenance-pressure gate. With the canonical pressure profile `0.84, 0.86, 0.88, 0.92`, the baseline trigger `0.85` causes three maintenance cycles. Grounded analysis reads the actual `gate.maintenance` trace events, diagnoses excess moderate-pressure intervention, and proposes `0.90` as a low-risk numeric threshold candidate.

The proposal carries the exact gate-decision event IDs as evidence. It is then processed through the normal Milestone 14 candidate registry and Milestone 15 sandbox. Across three paired runs, the expected result is `maintenance_cycles: 3 -> 1` while trace replay/design invariants remain green and the high-pressure intervention is retained.

## Live authority

Milestone 16 adds a separate `PromotionAuthority`. Sandbox recommendation alone is not permission to activate a candidate.

Immediately before promotion, deterministic policy rechecks:

- exact candidate ID and proposal fingerprint;
- exact current baseline system version;
- exact active baseline snapshot fingerprint;
- valid sandbox experiment and `PROMOTION_RECOMMENDED` disposition;
- configured live-promotable change class;
- configured maximum risk level;
- minimum number of paired runs;
- regression, failure-mode, budget, baseline-integrity, and teardown requirements;
- explicit operator approval unless `allow_auto_promotion` is enabled in configuration.

The default configuration keeps `allow_auto_promotion: false`.

## Versioned active configuration

Live state is represented by immutable config artifacts plus one active pointer:

```text
active-system/
  versions/<sha256>.json
  active-config.json
  promotions/<promotion-id>/rollback-baseline-artifact.json
```

A promoted config receives a new whole-system version such as:

```text
<baseline>+improvement.<candidate-prefix>.1
```

The version artifact is written before activation. The active pointer is replaced atomically. Repository source, model weights, and the immutable baseline artifact are not modified.

## Post-activation verification and rollback

Immediately after activation, a trusted verifier runs the candidate's required tests. Operator closed-loop runs can use the permanent scripted-autonomy verifier; acceptance tests use a fast maintenance-profile verifier to exercise failure modes without duplicating the expensive long-horizon suite many times.

If post-activation verification fails:

1. the exact baseline artifact is reactivated;
2. the candidate is invalidated with experiment/verification/rollback evidence;
3. the promotion record becomes `ROLLED_BACK`;
4. the failed candidate does not remain the active system.

The rollback artifact is SHA-256 verified and remains available after successful promotion as well.

## Candidate lifecycle

System-improvement candidates now support the evidence-backed lifecycle:

```text
PROPOSED
   ↓
SANDBOX_ELIGIBLE
   ↓
PROMOTED
   ↓ optional later rollback/invalidation
INVALIDATED
```

`ImprovementCandidateRegistry.record_promotion()` records an outcome already performed by the separate promotion authority. The registry itself still cannot mutate live configuration.

## First recursive measurement

After successful promotion, Harness X reloads the active config store and reruns the same grounded self-analysis.

Acceptance requires:

- the second analysis reports the promoted whole-system version;
- maintenance cycles fall from three to one;
- the original excessive-maintenance problem is no longer detected;
- the same threshold proposal is not generated again;
- the candidate lifecycle replays to `promoted`;
- the exact rollback artifact remains verified.

`next_improvement_readiness_score` is intentionally only an initial proxy: it records whether the next self-analysis is actually running on the improved version without wasting another candidate on the already-resolved problem. It is not claimed to measure general recursive-improvement capability.

## Scope boundary

Milestone 16 does not permit arbitrary source mutation, tool implementation changes, or model-weight promotion. The live authority is narrower than the sandbox and is restricted by configuration. Initial defaults permit only `config_threshold` and `retrieval_scoring_policy` candidates, with maximum risk `low` and operator approval required.

The next milestone begins collecting grounded gate-decision/outcome data for learned peripheral controllers while preserving every deterministic predecessor as a permanent baseline.
