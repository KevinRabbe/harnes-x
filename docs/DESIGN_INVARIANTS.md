# Harness X — Design Invariants

This document defines rules that should remain true as Harness X evolves.

They are intentionally stricter than ordinary implementation preferences. If a future component violates one of these rules, that should be an explicit architecture decision with evidence, not an accidental side effect.

---

## 1. The reasoning model is replaceable

No memory subsystem, scheduler, tool, or evaluator may depend on one specific model's hidden behavior.

Model-specific formatting belongs behind the `ReasoningCore` adapter and context-builder boundary.

**Reason:** the project is testing system intelligence, not one model wrapper.

---

## 2. Software-owned state is authoritative

The model may propose state changes. It does not define ground truth merely by stating it.

Examples of authoritative software state:

- current goal;
- active mode;
- memory occupancy;
- tool result;
- permissions;
- budgets;
- component versions;
- benchmark result.

**Reason:** self-observation must be based on evidence rather than generated introspection.

---

## 3. Observed, inferred, and proposed state remain distinct

An observation is not a hypothesis. A hypothesis is not a confirmed cause. A proposal is not an applied change.

The type system and persistence layer should preserve these distinctions.

**Reason:** long autonomous runs fail when uncertainty silently turns into fact.

---

## 4. Goals and governing constraints cannot depend on prompt retention

A task may lose conversational context and still retain its authoritative goals, permissions, success criteria, and stop conditions.

**Reason:** goal drift should be an observable control failure, not a normal consequence of context pressure.

---

## 5. Memory classes own their lifecycle

Each memory class defines its own write, retrieval, retention, verification, consolidation, and invalidation rules.

No generic "save everything to vector memory" path may bypass those rules.

**Reason:** different information types have different epistemic and operational requirements.

---

## 6. Every durable memory has provenance

A durable memory must retain enough information to answer where it came from and why it is trusted.

At minimum, durable items should preserve source/event references, creation time, system version, and verification state when applicable.

**Reason:** self-improvement and contradiction handling require traceable evidence.

---

## 7. Gate decisions are observable

Retrieval, focus, compute, write, routine, model-selection, and maintenance gates must expose their decisions and policy versions in traces.

A learned gate is not exempt.

**Reason:** a controller that cannot be inspected cannot be improved reliably.

---

## 8. Gates do not own the state they control

A gate returns a decision. The owning subsystem performs and validates the mutation.

Example:

```text
write gate -> proposes semantic_candidate
semantic memory -> validates and writes
```

**Reason:** decision policy and state ownership should remain separable.

---

## 9. Compute and action budgets are externally enforced

The reasoning model can request additional compute or actions. It cannot increase its own budget by assertion.

**Reason:** resource regulation is part of the architecture, not a prompt instruction.

---

## 10. Tool proposals are not tool executions

A model output requesting an action must pass through schema validation, permission checks, budget checks, and the tool executor.

A tool result must originate from the tool boundary, not from generated text.

**Reason:** autonomous systems need a hard boundary between imagination and environment state.

---

## 11. Verification is separate from generation

Where correctness matters, the system must have a path that can reject a plausible model output.

Verification may be deterministic, environmental, statistical, model-based, or composite, but it remains a separate step.

**Reason:** generation quality and acceptance criteria are different functions.

---

## 12. Important state transitions are traceable

For any important transition, Harness X should be able to reconstruct:

```text
previous authoritative state
-> component decision
-> action / observation
-> verification
-> new authoritative state
```

**Reason:** this is the minimum basis for useful self-analysis.

---

## 13. Improvement candidates are isolated from the active system

A candidate change must not silently mutate the live system while it is still being evaluated.

Use snapshots, versions, sandboxes, or equivalent isolation.

**Reason:** an experiment must not redefine its own baseline.

---

## 14. Candidate promotion requires evidence

A system prediction that a change is beneficial is insufficient for promotion.

A candidate must be tested against explicit acceptance criteria and regressions.

**Reason:** self-confidence is not an evaluation metric.

---

## 15. Every promoted change has a rollback path

Promotion should preserve the previous working version and the evidence used to justify the new one.

**Reason:** recursive improvement without rollback turns ordinary regressions into architectural corruption.

---

## 16. Self-improvement begins with bounded surfaces

Early autonomous changes should target narrow, reversible components such as configuration, retrieval policy, routine ordering, context construction, or verification frequency.

Arbitrary source-code or base-weight modification comes later.

**Reason:** the easiest improvements to understand should be attempted first.

---

## 17. The active reasoning core should remain stable during early system experiments

When measuring architectural improvement, avoid changing the core model at the same time unless the experiment explicitly studies that interaction.

**Reason:** otherwise capability gains cannot be attributed to the architecture.

---

## 18. Learned controllers need deterministic baselines

A learned gate or router should only replace a simple baseline when it produces measurable value on the intended capability/cost frontier.

**Reason:** learned complexity is not automatically intelligent complexity.

---

## 19. Maintenance work is an explicit operating mode

Memory consolidation, self-analysis, index maintenance, and architecture experiments should not be hidden side effects of ordinary task processing.

**Reason:** the system needs to know when it is working on the environment versus working on itself.

---

## 20. Internal maintenance is interruptible

Lower-priority consolidation or experimentation must be able to yield to higher-priority work at safe checkpoints.

**Reason:** internal optimization should not make the autonomous system unavailable to its actual goals.

---

## 21. Self-schema comes from the running architecture

The exact current architecture, component versions, capacities, and permissions should be generated from real configuration/state.

Do not hard-code the model's self-description into a long permanent prompt.

**Reason:** Harness X should be able to evolve without retraining the model after every structural change.

---

## 22. Self-model training uses ground truth whenever available

For synthetic fault diagnosis, labels should come from the injected fault and simulator state rather than another model's post-hoc explanation.

**Reason:** training a model on confident guesses about itself defeats the purpose of grounded self-understanding.

---

## 23. Training data and evaluation data are separated by scenario families

Do not rely only on random train/test splits of generated scenarios.

Hold out fault types, architecture configurations, or task families.

**Reason:** the goal is transferable self-understanding, not template memorization.

---

## 24. Experimental research does not become a core dependency without evidence

Recurrent depth, DiffusionBlocks, learned halting, multi-model routing, or future research ideas can be explored behind interfaces.

The base architecture must continue to function without them until they demonstrate clear value.

**Reason:** Harness X should accumulate validated mechanisms rather than research hype.

---

## 25. Bigger is not automatically better

More memory, more model calls, more context, more recurrence, more agents, or more tool use must justify their cost.

**Reason:** intelligent control includes deciding what *not* to compute.

---

## 26. The architecture may change its own taxonomy

The initial memory/gate/routine categories are hypotheses, not sacred design truths.

Harness X may eventually propose new memory types, split existing ones, merge controllers, or invent different abstractions if controlled experiments show improvement.

**Reason:** a self-improving cognitive architecture should not be permanently constrained by the vocabulary of its first designers.

---

## 27. Architecture changes remain measurable across versions

Every run should identify the relevant system, component, policy, routine, and benchmark versions.

**Reason:** "the system got better" is meaningless if the changed variables are unknown.

---

## 28. The first optimization target is the whole-system capability/cost frontier

Do not evaluate a subsystem only by its local score.

A better retrieval gate that uses dramatically more compute or increases downstream errors may make the complete system worse.

**Reason:** Harness X is optimizing the cognitive system, not isolated modules.

---

## 29. Self-improvement is allowed to discover that no change is best

An improvement cycle may conclude that the baseline should remain unchanged.

This is a successful experiment if it reduces uncertainty.

**Reason:** a system forced to always "improve" will eventually promote noise.

---

## 30. Human-understandable observability remains a permanent feature

Harness X may eventually develop machine-efficient internal representations that are not naturally human-readable, but component state, permissions, versions, resource use, experiments, and promotion decisions must remain externally inspectable.

**Reason:** efficient internal cognition and system-level auditability are compatible goals and should not be conflated.