# Harness X

Harness X is an architecture-first experimental cognitive system for autonomous AI work and system-level self-improvement.

The project starts from a deliberately different assumption than a conventional LLM agent:

> The intelligence of the complete system is not identical to the intelligence of the model inside it.

A reasoning model is treated as one replaceable component inside a larger cognitive architecture containing explicit state, specialized memories, gates, routines, telemetry, tools, evaluators, and controlled improvement loops.

The initial goal is **not** to train a new foundation model. The goal is to build the surrounding system first, define its interfaces and operating rules, and only then plug in a capable reasoning model and give it enough self-model training to understand the machinery it operates inside.

## Project status

**Milestones 0–16 implemented on the current development stack.**

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
- train/eval JSONL datasets with scenario/state/generator fingerprints, explicit label provenance, split-isolation validation, held-out diagnostic fault families, and a manifest-level dataset integrity hash;
- architecture-family-aware training cohorts that can hold out an entire Harness X configuration without rewriting the signed source curriculum records;
- deterministic prompt/completion formatting plus balanced architecture/family sampling for the initial roughly-1k-example self-model training stage;
- an optional LoRA/QLoRA training backend using Transformers, PEFT, TRL, and 4-bit loading while leaving the normal Harness X install free of ML-training dependencies;
- exact base-vs-adapter held-out evaluation for structured accuracy, diagnosis, safe experiments, uncertainty, authority violations, parsing, calibration, and per-family performance;
- SHA-256-bound evaluation reports plus a conservative adapter-promotion policy that treats successful training and permission to use an adapter as separate states;
- immutable first-class system-improvement proposals/candidates with software-owned candidate identity, stable proposal fingerprints, exact baseline version, explicit scope/patches, falsifiable hypotheses, predicted metrics, required tests, finite experiment budgets, risk levels, rollback requirements, lineage, and optional causal evidence refs;
- a conservative Milestone 14 qualification policy that permits only configuration thresholds, retrieval scoring, routine ordering, context-builder policy, verification frequency, and memory retention/compaction changes while rejecting code/tool/adapter mutation under the initial policy;
- strict JSON-only candidate schemas, mandatory replay/design-invariant tests, exact rollback-baseline checks, evidence-backed invalidation, immutable revision history, causal candidate tracing, and the hard rule that `SANDBOX_ELIGIBLE` is **not** live execution or promotion;
- immutable Milestone 15 experiment snapshots with distinct source/variant system versions and SHA-256 state fingerprints;
- matched baseline/candidate trials that use identical deterministic seeds, the same trusted benchmark runner, and the candidate's declared run/resource envelope while applying declarative patches only to a deep-copied candidate snapshot;
- disposable per-run working directories, durable evidence copies with per-file SHA-256 digests, automatic teardown on success or runner failure, and byte-for-byte baseline-snapshot integrity checks;
- empirical comparison reports containing metric means/deltas, repeated-run variance, reasoning/tool/wall-time cost deltas, new failure modes, budget violations, regressions, and explicit `PROMOTION_RECOMMENDED`, `REJECTION_RECOMMENDED`, or `INCONCLUSIVE` dispositions;
- a concrete `ScriptedAutonomyExperimentRunner` backed by the permanent long-horizon suite plus `harness-x run-improvement-sandbox` for operator experiments, while keeping the sandbox itself unable to promote or commit a change to the live system;
- a configured Milestone 16 `PromotionAuthority` that independently rechecks exact candidate/report/baseline fingerprints, live change class, risk, run count, regressions, failure modes, budgets, baseline integrity, teardown, and explicit operator approval unless low-risk auto-promotion is deliberately enabled;
- immutable versioned live-config artifacts plus one atomically replaced active pointer, a SHA-256-verified rollback artifact, immediate post-activation verification, automatic exact-baseline rollback on verification failure, and evidence-backed `PROMOTED` candidate lifecycle records;
- the first complete closed system-improvement demonstration: real maintenance-gate traces reveal excess moderate-pressure maintenance, grounded self-analysis proposes `working_pressure_trigger: 0.85 -> 0.90`, three matched sandbox runs measure maintenance cycles `3 -> 1`, the versioned config is promoted after independent verification, and the next self-analysis loads the promoted whole-system version and does not repropose the resolved issue.

A **real model-runtime adapter, model-assisted decision layer, grounded self-model curriculum generator, optional PEFT training/evaluation path, bounded improvement-candidate layer, isolated experiment sandbox, and first evidence-gated live improvement loop now exist**. Harness X still deliberately does **not** bundle or download model weights, and GitHub Actions does not perform a GPU training run. The untuned base model remains a permanent comparison control. Live promotion remains deliberately narrow: operator approval is required by default, source-code/tool/model-weight candidates remain excluded, and the promoted config can be restored from an immutable rollback artifact. The next planned milestone is **Milestone 17 — collect gate training data** for small learned peripheral controllers that must continue competing against deterministic predecessors.

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
harness-x run-improvement-sandbox path/to/sandbox-eligible-candidate.json configs/default.yaml --output .harness-x/improvement-sandbox
```

Optional adapter training is installed separately:

```bash
python -m pip install -e ".[training]"
harness-x prepare-self-model-training path/to/curriculum --base-model <model> --method qlora
harness-x train-self-model-adapter .harness-x/self-model-training
harness-x evaluate-self-model-adapter .harness-x/self-model-training/cohort --base-model <model> --adapter .harness-x/self-model-adapter/adapter
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
- **Improvement sandbox** — isolated candidate changes, replay, benchmarks, regression tests, rollback evidence, and bounded live promotion through a separate authority.

## Architecture-first development

The surrounding system is testable without a real LLM. Deterministic reasoning-core stubs and deterministic routine policies remain permanent baselines so memory, scheduling, gates, telemetry, replay, and state transitions can be validated independently from model quality.

Milestone 10 adds the first model-runtime adapter behind the same `ReasoningCore` boundary. Swapping the reasoning core does not grant it memory ownership, tool authority, permission authority, or direct state mutation.

Milestone 11 begins using the core for selected reasoning-heavy recommendations. Each assisted behavior remains paired with an old deterministic baseline and a deterministic evaluator. Model output is only selected when measurable evidence says it improves on the baseline; a tie, missing evaluation evidence, runtime failure, or authority-policy violation leaves the deterministic path in control.

Milestone 12 generates the first self-model curriculum from known Harness X state, active deterministic policies, deliberately injected faults, and declared interventions. Training labels therefore come from the system/simulator rather than from another model guessing what Harness X contains or why it failed. Evaluation seeds and selected fault families are held out instead of relying on a random row split.

Milestone 13 adds the first real parameter-efficient training and qualification path. Multiple architecture configurations can contribute data while one configuration is held out entirely; LoRA/QLoRA remains optional; and the trained adapter must beat the untouched base model on the exact same held-out examples without introducing authority, structural, calibration, parsing, or unacceptable general-capability regressions.

Milestone 14 turns proposed system modifications into immutable, versioned candidate objects. Static qualification is deliberately weaker than empirical promotion: it checks bounded scope, current baseline, allowed change class, required regression tests, finite resources, risk, and rollback, then grants at most `SANDBOX_ELIGIBLE`.

Milestone 15 introduces the empirical boundary. A sandbox-eligible candidate is applied only to a deep-copied snapshot with an experiment-specific variant version, then baseline and candidate are run under matched seeds/runner conditions. Benchmark outputs are copied into evidence directories and hashed before disposable working trees are torn down. Comparison separates candidate quality from experiment validity: a valid run can recommend promotion or rejection, while a broken baseline, unsupported declared metric, or damaged isolation produces `INCONCLUSIVE`.

Milestone 16 closes the first loop. A separate promotion authority revalidates sandbox evidence against the exact currently active config, writes a new immutable whole-system config version, switches the active pointer atomically, and immediately runs required verification. Failure restores the exact baseline artifact automatically. Successful promotion is recorded causally, and the next grounded self-analysis is required to execute on the promoted version without regenerating the same resolved improvement candidate.

The initial neural strategy is intentionally modest:

1. start with an existing capable small/medium reasoning model;
2. keep the base core stable while the architecture matures;
3. provide a small self-model curriculum so the model understands Harness X concepts, interfaces, telemetry, and failure modes;
4. let most early improvement happen in memories, routines, routing, verification, tools, and controllers;
5. train adapters or learned peripheral components only when evidence justifies them;
6. consider deeper model/weight changes much later.

## System-level self-improvement

Harness X treats recursive improvement as a property of the **whole system**, not only the neural weights.

The first implemented loop is now:

```text
observe gate behavior
    -> diagnose from causal traces
    -> propose an evidence-linked bounded threshold change
    -> statically qualify candidate
    -> run matched sandbox experiment
    -> recheck configured promotion criteria
    -> atomically activate versioned config
    -> independently verify active version
    -> keep rollback artifact
    -> run next self-analysis on improved system
```

Possible later improvement targets include retrieval policies, memory schemas, consolidation routines, gate thresholds, planning/debugging routines, context construction, tool selection, verification strategies, scheduling policies, experiment design, and controller models.

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
- [Milestone 16 closed improvement loop](docs/MILESTONE_16_CLOSED_IMPROVEMENT_LOOP.md)
- [Grounded self-model training](src/harness_x/training/README.md)

## Guiding question

The project is ultimately testing a broader hypothesis:

> How much capability can come from the organization, memory, control, learning, and self-improvement mechanisms around a reasoning model rather than from simply making the model larger?

Harness X is intended to make that question experimentally answerable.
