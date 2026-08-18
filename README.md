# Harness X

Harness X is an architecture-first experimental cognitive system for autonomous AI work and system-level self-improvement.

The project starts from a deliberately different assumption than a conventional LLM agent:

> The intelligence of the complete system is not identical to the intelligence of the model inside it.

A reasoning model is treated as one replaceable component inside a larger cognitive architecture containing explicit state, specialized memories, gates, routines, telemetry, tools, evaluators, and controlled improvement loops.

The initial goal is **not** to train a new foundation model. The goal is to build the surrounding system first, define its interfaces and operating rules, and only then plug in a capable reasoning model and give it enough self-model training to understand the machinery it operates inside.

## Project status

**Milestones 0–12 implemented on the current development stack.**

The implementation now contains:

- the typed Python package, IDs/contracts, provenance, external compute budgets, explicit clock abstraction, configuration, CLI, tests, and CI;
- a structured causal event spine, append-only hash-chained trace ledger, portable fixtures, deterministic replay, and trace/replay CLI validation;
- an explicit software-owned orchestrator with immutable task sessions, legal operating-mode transitions, budget-triggered suspension, exact-mode resume, hash-verified checkpoints, stale-checkpoint refusal, parent/child task relationships, and scheduler observation hooks;
- explicit goal, working, episodic, error/anomaly, semantic, and procedural memory surfaces with separate lifecycles and invariants;
- authoritative goal history, pinned governing constraints, bounded working-state pressure, deterministic eviction, trace-backed episodic records, baseline non-embedding retrieval, and evidence-gated error resolution;
- versioned deterministic retrieval, write, focus, compute, and maintenance gates whose decisions are traced with canonical input-state fingerprints;
- config-owned gate thresholds and the hard boundary that gates propose flow decisions while memory/orchestrator owners remain solely responsible for mutation;
- a versioned routine engine with explicit preconditions, required state views, authority envelopes, step policies, verification requirements, request fingerprints, and nested routine tracing;
- deterministic task, verification, recovery, and consolidation routines that exercise the real orchestrator/memory/gate/trace architecture while a deterministic reasoning core remains available as the baseline;
- a declared tool registry and permission boundary with versioned input/output schemas, routine authority checks, explicit permissions, side-effect classes, tool-action budget enforcement, normalized failures/timeouts, and schema-valid tool-origin observations;
- a tool-backed task routine that enforces `ActionProposal != ActionExecution` and routes proposals through registry, permission, validation, budget, execution, observation, verification, and memory;
- evidence-gated semantic memory with candidate claims, confidence, preserved source provenance, explicit evaluation/promotion, symmetric contradiction links, invalidation, and revision history;
- versioned procedural memory with repeated-success candidate formation, explicit evaluation/promotion, independent coexisting versions, usage/success/failure/cost statistics, known failure modes, invalidation, and history;
- explicit consolidation pipelines in which episodes create candidates but never directly create semantic truth or active procedures;
- a long-horizon scripted autonomy benchmark spanning dependency ordering, interruption/checkpoint resume, repeated working-memory pressure, tool/verifier failure recovery, and contradictory verified observations;
- machine-readable benchmark reports whose pressure, retrieval, recovery, action, verification, lifecycle, and trace-integrity metrics are derived from authoritative state/events wherever practical, with the suite required to survive at least 300 authoritative transitions before passing;
- a grounded `SystemSelfSchema` generated from authoritative runtime owners, traces, configuration, declared component metadata, tools, permissions, budgets, memory state, errors, installed reasoning-core metadata, and explicit limitations rather than model introspection;
- a canonical self-state fingerprint plus side-effect-free rolling metrics for working pressure, retrieval usefulness, routine success, recovery success, verifier rejection, unresolved-error age, contradictions, maintenance, and tool actions;
- append-only metrics samples for dashboard/history use while causal traces remain the source of truth;
- a stable replaceable `ReasoningCore` boundary, deterministic bounded context construction, structured proposal-only model output, software-assigned candidate identity/provenance, and reasoning request/result trace events that never record private chain-of-thought;
- a deterministic `StubReasoningCore` baseline plus a local-first OpenAI-compatible HTTP adapter suitable for locally served llama.cpp/vLLM/Ollama-style runtimes, with loopback-only endpoints by default;
- a fake-to-real reasoning swap probe that runs stub and model-runtime proposals through the same surrounding architecture, tool permission/execution boundary, verifier, and replay checks without changing memory or orchestrator ownership;
- seven recommendation-only model-assisted routine families for planning, retrieval-query formulation, hypothesis generation, recovery proposals, semantic candidate extraction, routine-selection recommendations, and experiment proposals;
- a permanent deterministic shadow baseline plus deterministic evaluator for every assisted decision, with the model selected only when it meets the minimum score and **strictly beats** the baseline;
- shadow-only behavior when no external evaluation reference exists, deterministic fallback on reasoning-runtime failure, and explicit rejection of authority-shaped recommendation payloads;
- causal `assisted_decision_compared` trace events plus a `model-assisted-routines-v1` benchmark that separates harness containment from model quality and verifies reasoning-budget use without granting model mutation/tool authority;
- a grounded self-model curriculum generator covering structural, operational, diagnostic, and causal/counterfactual scenarios without using a teacher LLM for labels;
- deterministic fault injection for memory saturation, semantic conflicts, blocked goals, repeated tool failures, verifier rejection, budget exhaustion, and maintenance pressure, with structured evidence, uncertainty, and safe next experiments;
- train/eval JSONL datasets with scenario/state/generator fingerprints, explicit label provenance, split-isolation validation, held-out diagnostic fault families, and a manifest-level dataset integrity hash.

A **real model-runtime adapter, model-assisted decision layer, and grounded self-model curriculum generator now exist**, but Harness X deliberately does **not** bundle, download, or require any specific model weights. CI still validates the runtime and assisted-decision boundaries with deterministic fixtures while the deterministic core/rules remain permanent comparison baselines. The next planned milestone is **initial self-model adapter training**: start with a small high-quality generated curriculum, use LoRA/QLoRA-style parameter-efficient tuning, and evaluate on held-out fault families/configurations before expanding the dataset.

## Getting started

```bash
python -m pip install -e ".[dev]"
pytest
harness-x --help
harness-x validate-config configs/default.yaml
harness-x verify-trace path/to/trace.jsonl
harness-x replay-fixture path/to/fixture.json
harness-x benchmark-scripted configs/default.yaml --output .harness-x/benchmark-scripted
harness-x benchmark-reasoning-swap configs/default.yaml --base-url http://127.0.0.1:8080/v1 --model local-model
harness-x benchmark-model-assisted configs/default.yaml --base-url http://127.0.0.1:8080/v1 --model local-model
harness-x generate-self-model-curriculum configs/default.yaml path/to/system-self-schema.json --output .harness-x/self-model-curriculum
```

## Core idea

A conventional agent often looks roughly like:

```text
prompt -> model -> tools -> more prompt -> model -> answer
```

Harness X instead aims for something closer to:

```text
                           Goals / Constraints
                                  |
                           Priority Control
                                  |
                                  v
Memories <---- gates ----> Working State ----> Focus / Compute Gates
   ^                              |                     |
   |                              v                     v
   |                         Reasoning Core <------ Routine Router
   |                              |
   |                     +--------+--------+
   |                     |        |        |
   +------------------ Memory    Action   Evaluate
                                  |        |
                                  v        v
                             Environment  Error / Self State
                                             |
                                      Maintenance / Improvement
```

Important consequences:

- the model does not have to pretend that prompt context is memory;
- internal state is represented by real system objects and telemetry;
- different memory classes have different write, decay, retrieval, verification, and consolidation routines;
- gates explicitly control attention, retrieval, compute depth, routine selection, writes, verification, and potentially model selection;
- autonomous work and internal maintenance are separate operating modes;
- system improvements can happen without changing foundation-model weights;
- self-improvement is staged, observable, benchmarked, reversible, and sandboxed before promotion.

## Planned components

Harness X is planned around the following major subsystems:

- **Orchestrator / scheduler** — owns lifecycle, task state, routine scheduling, and budgets.
- **Working state** — small, explicit, high-priority state for the current task.
- **Goal / constraint memory** — durable task objectives, invariants, permissions, and stop conditions.
- **Episodic memory** — records events, attempts, outcomes, and causal traces.
- **Semantic memory** — consolidated reusable knowledge with provenance and confidence.
- **Procedural / routine memory** — reusable strategies and operating procedures.
- **Error / anomaly buffer** — unresolved failures, contradictions, uncertainty, and verification conflicts.
- **Experiment / hypothesis buffer** — candidate explanations, experiments, and system changes awaiting evidence.
- **Self-model / telemetry** — ground-truth description of the current architecture, versions, capacities, utilization, and observed failure patterns.
- **Gate layer** — learned or deterministic controllers for retrieval, writes, focus, compute depth, routine selection, verification, and model routing.
- **Reasoning-core adapter** — stable interface behind which different LLMs or recurrent reasoning cores can be swapped.
- **Tool environment** — external actions with explicit permissions, budgets, and structured results.
- **Evaluator / verifier layer** — independent checks used to distinguish plausible model output from accepted system state.
- **Improvement sandbox** — isolated candidate changes, replay, benchmarks, regression tests, and rollback.

## Architecture-first development

The surrounding system is testable without a real LLM. Deterministic reasoning-core stubs and deterministic routine policies remain permanent baselines so memory, scheduling, gates, telemetry, replay, and state transitions can be validated independently from model quality.

Milestone 10 adds the first model-runtime adapter behind the same `ReasoningCore` boundary. Swapping the reasoning core does not grant it memory ownership, tool authority, permission authority, or direct state mutation.

Milestone 11 begins using the core for selected reasoning-heavy recommendations. Each assisted behavior remains paired with an old deterministic baseline and a deterministic evaluator. Model output is only selected when measurable evidence says it improves on the baseline; a tie, missing evaluation evidence, runtime failure, or authority-policy violation leaves the deterministic path in control.

Milestone 12 generates the first self-model curriculum from known Harness X state, active deterministic policies, deliberately injected faults, and declared interventions. Training labels therefore come from the system/simulator rather than from another model guessing what Harness X contains or why it failed. Evaluation seeds and selected fault families are held out instead of relying on a random row split.

The initial neural strategy is intentionally modest:

1. start with an existing capable small/medium reasoning model;
2. keep the base core stable while the architecture matures;
3. provide a small self-model curriculum so the model understands Harness X concepts, interfaces, telemetry, and failure modes;
4. let most early improvement happen in memories, routines, routing, verification, tools, and controllers;
5. train adapters or learned peripheral components only when evidence justifies them;
6. consider deeper model/weight changes much later.

## System-level self-improvement

Harness X treats recursive improvement as a property of the **whole system**, not only the neural weights.

A valid early improvement loop can therefore be:

```text
observe failures
    -> diagnose from telemetry and traces
    -> propose a bounded change
    -> create isolated candidate
    -> replay / benchmark / regression-test
    -> compare against current version
    -> accept or reject
    -> retain rollback path
```

Possible early improvement targets include retrieval policies, memory schemas, consolidation routines, gate thresholds, planning/debugging routines, context construction, tool selection, verification strategies, scheduling policies, experiment design, and controller models.

Changing the main reasoning weights is deliberately **not** required for these loops.

## Research branches

Several current research directions fit Harness X well, but they are treated as optional experiments rather than architectural dependencies:

- **Recurrent / looped depth** — reuse a smaller reasoning block for variable test-time computation.
- **Learned depth / halting gates** — allocate more computation only when a state requires it.
- **DiffusionBlocks-style block-wise training** — investigate whether independently trainable denoising-style blocks can reduce training memory and communication costs.
- **Learned memory / routing controllers** — train small peripheral models while keeping the main reasoning core stable.

These ideas are useful because they potentially decouple parameter count, active memory, and effective reasoning depth, but Harness X must remain functional if any one of them fails to scale.

## Documentation

- [Planned architecture](docs/ARCHITECTURE.md)
- [Detailed implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Design invariants](docs/DESIGN_INVARIANTS.md)

## Guiding question

The project is ultimately testing a broader hypothesis:

> How much capability can come from the organization, memory, control, learning, and self-improvement mechanisms around a reasoning model rather than from simply making the model larger?

Harness X is intended to make that question experimentally answerable.
