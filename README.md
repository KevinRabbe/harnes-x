# Harness X

Harness X is an architecture-first experimental cognitive system for autonomous AI work and system-level self-improvement.

The project starts from a deliberately different assumption than a conventional LLM agent:

> The intelligence of the complete system is not identical to the intelligence of the model inside it.

A reasoning model is treated as one replaceable component inside a larger cognitive architecture containing explicit state, specialized memories, gates, routines, telemetry, tools, evaluators, and controlled improvement loops.

The initial goal is **not** to train a new foundation model. The goal is to build the surrounding system first, define its interfaces and operating rules, and only then plug in a capable reasoning model and give it enough self-model training to understand the machinery it operates inside.

## Project status

**Milestone 0 — contract foundation implemented.**

The first implementation establishes the Python package, typed IDs and contracts, provenance, external compute budgets, explicit clock abstraction, configuration loading, CLI, tests, and clean-checkout CI. There is deliberately **no model runtime dependency yet**.

The next planned milestone is the event spine, trace ledger, and replay foundation.

## Getting started

```bash
python -m pip install -e ".[dev]"
pytest
harness-x --help
harness-x validate-config configs/default.yaml
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

The surrounding system should be testable before a real LLM is required. Early development therefore uses deterministic or scripted reasoning-core stubs so that memory, scheduling, gates, telemetry, replay, and state transitions can be validated independently from model quality.

Only after the architecture can operate coherently is a real model plugged into the `ReasoningCore` interface.

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
