# Harness X — Detailed Implementation Plan

Status: **planned implementation sequence**  
This plan intentionally builds the cognitive architecture before integrating a real reasoning model.

---

## 1. Implementation strategy

Harness X should not begin as an LLM wrapper.

The implementation order is deliberately:

```text
contracts
-> authoritative state
-> traces / replay
-> memory
-> gates
-> scheduler / routines
-> tools / verification
-> autonomous lifecycle
-> self-model / telemetry
-> real reasoning model
-> self-model training
-> improvement sandbox
-> learned controllers
-> advanced model research
```

This order matters because a real model is extremely good at hiding architectural mistakes. If the model is integrated too early, prompt engineering can temporarily compensate for missing state, broken lifecycle rules, weak memory boundaries, and unobservable control flow.

The architecture should therefore run first with a deterministic fake reasoning core.

---

## 2. Recommended first technology stack

The exact libraries can change, but the first implementation should optimize for inspectability and iteration speed.

### Language

**Python** is the recommended first implementation language because the project will eventually integrate model runtimes, training, evaluation, vector search, and research code.

### Data / validation

Use typed schemas throughout.

Recommended:

- Pydantic or dataclasses for runtime contracts;
- JSON/JSONL for portable traces and fixtures;
- SQLite for the first persistent metadata/memory implementation;
- local filesystem/blob storage for larger artifacts;
- embeddings/vector search behind an interface rather than embedded into memory design.

Do **not** make a vector database the memory architecture. It is only one possible index.

### Tests

Recommended:

- pytest;
- deterministic fixtures;
- golden trace/replay tests;
- property tests for state-machine invariants where useful;
- benchmark scenarios stored as versioned data.

### First user interface

Start with a CLI and structured logs.

A graphical interface can come later. Early development benefits more from replayable traces than visual polish.

---

## 3. Proposed repository structure

```text
harnes-x/
├─ README.md
├─ docs/
│  ├─ ARCHITECTURE.md
│  ├─ IMPLEMENTATION_PLAN.md
│  └─ DESIGN_INVARIANTS.md
├─ pyproject.toml
├─ configs/
│  ├─ default.yaml
│  ├─ memory.yaml
│  └─ gates.yaml
├─ src/
│  └─ harness_x/
│     ├─ __init__.py
│     ├─ cli.py
│     │
│     ├─ core/
│     │  ├─ contracts.py
│     │  ├─ ids.py
│     │  ├─ budgets.py
│     │  ├─ events.py
│     │  ├─ provenance.py
│     │  └─ errors.py
│     │
│     ├─ orchestrator/
│     │  ├─ modes.py
│     │  ├─ state_machine.py
│     │  ├─ scheduler.py
│     │  └─ session.py
│     │
│     ├─ memory/
│     │  ├─ base.py
│     │  ├─ repository.py
│     │  ├─ working.py
│     │  ├─ goals.py
│     │  ├─ episodic.py
│     │  ├─ semantic.py
│     │  ├─ procedural.py
│     │  ├─ error_buffer.py
│     │  └─ experiment_buffer.py
│     │
│     ├─ gates/
│     │  ├─ base.py
│     │  ├─ retrieval.py
│     │  ├─ write.py
│     │  ├─ focus.py
│     │  ├─ compute.py
│     │  ├─ routine.py
│     │  ├─ model.py
│     │  └─ maintenance.py
│     │
│     ├─ routines/
│     │  ├─ base.py
│     │  ├─ task.py
│     │  ├─ planning.py
│     │  ├─ verification.py
│     │  ├─ recovery.py
│     │  ├─ consolidation.py
│     │  ├─ self_analysis.py
│     │  └─ improvement.py
│     │
│     ├─ reasoning/
│     │  ├─ base.py
│     │  ├─ stub.py
│     │  ├─ context_builder.py
│     │  └─ adapters/
│     │     ├─ local_transformers.py
│     │     └─ openai_compatible.py
│     │
│     ├─ tools/
│     │  ├─ schemas.py
│     │  ├─ registry.py
│     │  ├─ permissions.py
│     │  └─ executor.py
│     │
│     ├─ verification/
│     │  ├─ base.py
│     │  ├─ schema.py
│     │  ├─ constraints.py
│     │  └─ composite.py
│     │
│     ├─ telemetry/
│     │  ├─ trace_store.py
│     │  ├─ metrics.py
│     │  ├─ self_schema.py
│     │  └─ replay.py
│     │
│     ├─ improvement/
│     │  ├─ candidates.py
│     │  ├─ sandbox.py
│     │  ├─ experiments.py
│     │  ├─ comparison.py
│     │  ├─ promotion.py
│     │  └─ rollback.py
│     │
│     └─ training/
│        ├─ self_model_dataset.py
│        ├─ fault_injection.py
│        ├─ curriculum.py
│        └─ adapters.py
│
├─ benchmarks/
│  ├─ fixtures/
│  ├─ scenarios/
│  └─ expected/
├─ experiments/
│  └─ README.md
└─ tests/
   ├─ unit/
   ├─ integration/
   ├─ replay/
   └─ autonomy/
```

Do not create every empty file on day one. Add modules as their milestone starts. The tree above defines intended ownership boundaries.

---

# PART I — BUILD THE SYSTEM WITHOUT A REAL MODEL

## Milestone 0 — Repository and contract foundation

### Objective

Make the repository executable, typed, testable, and structurally ready for later components.

### Implement

1. `pyproject.toml`
2. `src/harness_x/`
3. CLI entry point
4. core ID types
5. timestamps / clock abstraction
6. common result/error types
7. base configuration loader
8. test setup

### First contracts

Define these before implementing behavior:

```text
TaskId
GoalId
MemoryId
RoutineId
TraceId
CandidateId
SystemVersion
```

and:

```text
Observation
Proposal
ActionProposal
VerificationResult
ComputeBudget
Provenance
```

### Acceptance criteria

- package installs locally;
- `harness-x --help` works;
- schemas serialize and deserialize without losing IDs/provenance;
- tests run from a clean checkout;
- no model dependency exists yet.

---

## Milestone 1 — Event spine, trace ledger, and replay foundation

### Why this comes first

Self-improvement is almost impossible to debug if observability is added later.

Every important component should emit structured trace events from the beginning.

### Implement

`core/events.py`

Base event schema:

```yaml
trace_id: ...
task_id: ...
step: 1
timestamp: ...
event_type: ...
component: ...
system_version: ...
input_refs: [...]
output_refs: [...]
metadata: {...}
```

Initial event types:

```text
task_created
goal_created
goal_updated
mode_changed
memory_written
memory_evicted
memory_retrieved
gate_decision
routine_started
routine_finished
action_proposed
action_executed
observation_received
verification_completed
error_recorded
budget_changed
candidate_created
candidate_evaluated
candidate_promoted
candidate_rejected
```

### Trace store

Begin with append-only JSONL or SQLite.

Requirements:

- ordered events;
- task-scoped queries;
- component-scoped queries;
- stable event schema version;
- ability to export one run as a portable fixture.

### Replay

Replay v1 does not need to reproduce model randomness because there is no real model yet.

It should reconstruct authoritative state from an event sequence and assert that the reconstructed state matches the recorded final state.

### Acceptance criteria

- a synthetic task can emit a trace;
- state can be rebuilt from the trace;
- corrupt/out-of-order events are detected;
- a golden trace test is deterministic.

### High-value rule

**Do not log private free-form reasoning as the primary debugging mechanism.**

Log causal system events, decisions, inputs, outputs, versions, and evidence.

---

## Milestone 2 — Orchestrator state machine

### Objective

Create the lifecycle independently from model intelligence.

### Modes

Implement an explicit state machine with at least:

```text
READY
TASK_ACTIVE
VERIFY
RECOVERY
MAINTENANCE
CONSOLIDATION
EXPERIMENT
SUSPENDED
COMPLETE
FAILED
```

### Implement

- legal mode transitions;
- transition reasons;
- budget checks;
- interruption points;
- checkpoint/resume;
- task parent/child hierarchy;
- scheduler hooks.

### Test cases

1. normal task completion;
2. tool/step failure -> recovery;
3. high memory pressure -> maintenance;
4. task suspension -> persisted checkpoint -> resume;
5. exhausted budget -> safe suspension/failure;
6. illegal state transition rejected.

### Acceptance criteria

The full state-machine suite must run with **zero LLM calls**.

---

## Milestone 3 — Minimal authoritative memory system

Do not implement every planned memory class at once.

Start with the minimum needed to exercise the architecture.

### 3.1 Goal memory

Implement first because goals must never depend on model context.

Features:

- create goal;
- create subgoal;
- update status;
- pin governing constraints;
- completion criteria;
- provenance;
- history.

### 3.2 Working state

Implement bounded capacity.

Each item should have:

```yaml
id: ...
kind: ...
priority: ...
pinned: false
size_units: ...
source: ...
created_step: ...
last_used_step: ...
```

Implement pressure calculation and deterministic eviction.

Do not optimize eviction yet; make it understandable.

### 3.3 Episodic memory

Store compact structured episodes referencing raw traces.

Initial retrieval can be simple metadata/full-text filtering.

Do not introduce embeddings until a baseline exists.

### 3.4 Error buffer

Track:

- observed anomaly;
- source event;
- severity;
- status;
- suspected causes separately;
- resolution evidence.

### Acceptance criteria

Create synthetic scenarios demonstrating:

- goal survives working-state churn;
- working-state pressure triggers eviction but never removes pinned governing state;
- episodic memory can retrieve a previous failed attempt;
- error buffer does not convert a hypothesis into a confirmed cause.

---

## Milestone 4 — Deterministic gates

### Objective

Introduce explicit flow control without machine learning.

Every gate implements a common contract:

```python
class GateDecision:
    gate_id: str
    decision: object
    inputs: list[str]
    policy_version: str
    confidence: float | None
    cost: float | None
```

### Implement first

#### Retrieval gate v0

Simple policy using:

- current routine;
- unresolved entities;
- uncertainty flag;
- working pressure;
- recent retrieval count.

#### Write gate v0

Maps accepted observations/proposals into correct memory classes.

#### Focus gate v0

Deterministically ranks/pins working-state items.

#### Compute gate v0

Uses hard budgets and explicit stop conditions.

#### Maintenance gate v0

Triggers maintenance from measurable system pressure.

### Acceptance criteria

- every gate decision appears in trace;
- same state/config produces same decision;
- no gate can directly mutate memory;
- policy version is recorded;
- thresholds live in configuration, not scattered constants.

---

## Milestone 5 — Routine engine

### Objective

Separate *how a task is processed* from the model that may later assist the processing.

### Routine contract

A routine should expose:

```text
preconditions
required state views
allowed tools
allowed memory writes
step policy
verification requirements
termination rule
```

### Implement with scripted behavior first

#### Task routine

```text
observe -> update -> retrieve -> decide -> act -> verify -> store
```

#### Verification routine

Can initially run deterministic validators only.

#### Recovery routine

Given injected failures, reconstruct a coherent task state.

#### Consolidation routine

Promote selected episodic observations into semantic candidates later; initially create structured summaries without promotion.

### Acceptance criteria

A deterministic demo task should complete through the real orchestrator, memory, gates, routines, verification, and trace system while the reasoning core remains a stub.

This is the first major architecture checkpoint.

---

## Milestone 6 — Tool registry and permission boundary

### Objective

Make external action an explicit subsystem.

### Tool schema

Each tool defines:

```yaml
name: ...
version: ...
input_schema: ...
output_schema: ...
permissions: [...]
side_effect_level: none | reversible | persistent
cost_class: ...
timeout: ...
idempotent: ...
```

### Implement

- registry;
- permission evaluator;
- executor;
- timeout handling;
- normalized result;
- trace events;
- mock tools for tests.

### Test tools

Use deliberately boring tools first:

- calculator-like deterministic tool;
- key/value read tool;
- temporary-file writer in sandbox;
- simulated unreliable tool that fails on command.

### Acceptance criteria

- model/stub cannot invoke undeclared tool;
- action proposal does not equal execution;
- permission failure is observable and recoverable;
- tool output is parsed before becoming an observation.

---

## Milestone 7 — Semantic and procedural memory

Only add these after episodic memory and traces are working.

### Semantic memory

Implement:

- candidate claims;
- confidence;
- provenance;
- contradictions;
- verification state;
- version history;
- invalidation.

Do not let one model statement become semantic truth by default.

### Procedural memory

Version routines/policies as data where practical.

Track:

- usage count;
- task categories;
- success/failure statistics;
- cost;
- known failure modes.

### Consolidation

Build explicit pipelines:

```text
episodes -> candidate knowledge -> verification -> semantic promotion
```

and:

```text
repeated successful strategy -> candidate routine -> evaluation -> procedural promotion
```

### Acceptance criteria

- contradictory semantic memories remain represented as a contradiction;
- provenance survives consolidation;
- a bad candidate can be rejected without deleting its episode history;
- routine versions can coexist for A/B evaluation.

---

# PART II — BUILD AUTONOMY BEFORE ADDING A REAL MODEL

## Milestone 8 — Long-horizon scripted autonomy benchmark

### Objective

Prove the architecture can maintain coherent state over many actions without relying on language-model intelligence.

### Build benchmark scenarios

At least five synthetic environments:

1. **multi-step dependency task** — must complete actions in correct order;
2. **interruption task** — suspend halfway and resume from stored state;
3. **memory-pressure task** — working state overflows repeatedly;
4. **failure/recovery task** — tool fails and alternative path exists;
5. **contradiction task** — two observations conflict and require verification.

### Metrics

Track:

- goal retention;
- state correctness;
- illegal transitions;
- recoveries;
- memory pressure;
- retrieval usefulness;
- action count;
- verification failures;
- trace completeness.

### Acceptance criteria

Harness X should survive hundreds of scripted state transitions without losing governing goals or corrupting authoritative state.

If it cannot, do not add a real model yet.

---

## Milestone 9 — External self-schema and telemetry dashboard data

### Objective

Make the system able to describe itself from actual state.

### Self-schema generator

Create a machine-readable snapshot containing:

```text
system version
component versions
active task/mode
memory capacities/utilization
active routine
gate policies
budgets
recent error patterns
known component limitations
available tools/permissions
```

### Metrics store

Begin collecting rolling metrics such as:

- working pressure;
- retrieval hit usefulness;
- routine success rate;
- recovery success;
- verifier rejection rate;
- average steps per task;
- error-buffer age;
- memory contradiction count.

### Acceptance criteria

The self-schema must be generated without asking a model what the system contains.

---

# PART III — ONLY NOW PLUG IN THE REASONING MODEL

## Milestone 10 — Reasoning-core interface and fake-to-real swap

### Objective

Replace the deterministic stub with a real reasoning core without changing the orchestrator, memory ownership, or tool boundaries.

### Define stable API

`ReasoningRequest` should include only bounded views of:

- active goal;
- current working state;
- selected retrieved memories;
- active routine;
- relevant self-schema;
- available actions;
- compute budget.

`ReasoningResult` should return proposals, not direct mutations.

### Context builder

The context builder is a first-class component.

It decides how structured state becomes model input.

Requirements:

- deterministic ordering where possible;
- clear provenance labels;
- token/size budget;
- separate authoritative facts from hypotheses;
- avoid dumping entire memory stores into context.

### Adapter implementations

Implement one local adapter first.

The interface should later support:

- local Transformers model;
- llama.cpp-compatible runtime;
- OpenAI-compatible HTTP endpoint;
- recurrent-depth experimental core.

### Acceptance criteria

The same architecture benchmark should run with:

```text
StubReasoningCore
```

and:

```text
RealReasoningCore
```

without changing memory/scheduler code.

---

## Milestone 11 — Model-assisted routines

Now gradually hand reasoning-heavy decisions to the core.

Recommended sequence:

1. planning proposals;
2. retrieval query formulation;
3. hypothesis generation;
4. recovery proposals;
5. semantic candidate extraction;
6. routine selection recommendations;
7. experiment proposals.

Keep final state transitions external.

### Evaluation rule

For each newly model-assisted behavior:

```text
old deterministic baseline
vs
model-assisted version
```

must be measurable.

Do not replace a simple reliable rule merely because the model version sounds smarter.

---

# PART IV — TRAIN THE MODEL TO UNDERSTAND HARNESS X

## Milestone 12 — Self-model curriculum generator

### Objective

Generate clean training data from known system state and deliberately injected faults.

### Dataset families

#### Structural

Examples:

- identify component ownership;
- identify valid state transition;
- choose which memory class owns information;
- distinguish authoritative vs inferred vs proposed state.

#### Operational

Examples:

- working pressure high;
- retrieval results conflict;
- goal is blocked;
- tool repeatedly fails;
- verification rejects proposal;
- task needs suspension;
- maintenance is due.

#### Diagnostic

Inject a known fault and provide telemetry.

Ask the model to identify:

- observed symptom;
- likely component;
- evidence;
- uncertainty;
- safe next experiment.

#### Causal / counterfactual

Provide before/after system metrics and ask for an explanation and follow-up test.

### Critical rule

The label should be produced from the simulator/known injected fault whenever possible, not from another model guessing the answer.

### Dataset format

Store:

```text
scenario definition
system version
input state
expected structured decision
accepted alternative decisions
explanation/rationale metadata if needed for training
```

Keep evaluation cases separate from training generation seeds.

---

## Milestone 13 — Initial self-model adapter training

### Objective

Teach a capable existing model to operate Harness X more reliably without turning the project into foundation-model training.

### Initial training approach

Prefer parameter-efficient tuning first:

```text
base model
+ LoRA / QLoRA adapter
```

The first training target should be **system operation and diagnosis**, not general knowledge.

### Start small

Do not start with a massive synthetic corpus.

Suggested progression:

```text
1k high-quality scenarios
-> evaluate
5k
-> evaluate
10k+
-> only if additional data continues to help
```

Token count matters less than scenario quality, coverage, and correct labels.

### Evaluation split

Hold out entire fault families and architecture configurations, not merely random examples.

Otherwise the model can memorize surface patterns without learning a causal self-model.

### Acceptance criteria

A self-trained adapter should improve at least:

- correct memory/routine selection;
- diagnosis of injected failures;
- respect for authoritative state;
- calibrated uncertainty;
- recovery decisions;

without materially degrading general task performance.

---

# PART V — CONTROLLED SYSTEM-LEVEL SELF-IMPROVEMENT

## Milestone 14 — Improvement candidate model

### Objective

Represent system modifications as first-class objects.

Candidate schema:

```yaml
candidate_id: ...
created_by: human | system
baseline_version: ...
change_type: config | routine | gate | memory | tool | code | adapter
scope: [...]
hypothesis: ...
predicted_metrics: {...}
required_tests: [...]
resource_budget: ...
risk_level: ...
status: proposed
```

### Candidate classes for first experiments

Only permit bounded, easy-to-revert classes first:

1. configuration thresholds;
2. retrieval scoring policy;
3. routine ordering;
4. context-builder policy;
5. verification frequency;
6. memory retention/compaction policy.

Do not start self-improvement with arbitrary source-code mutation.

---

## Milestone 15 — Experiment sandbox

### Objective

A candidate runs against a copy/snapshot, not the live system.

### Sandbox requirements

- isolated config/version;
- fixed benchmark suite;
- resource budget;
- deterministic seeds when possible;
- captured traces;
- baseline run under identical conditions;
- regression suite;
- automatic teardown.

### Compare

A comparison result should include:

```text
primary capability delta
cost delta
memory delta
recovery delta
new failure modes
regressions
variance across runs
```

### Promotion policy

Initial promotion can require:

- primary target metric improves;
- no critical invariant violation;
- no unacceptable regression;
- improvement persists across repeated runs;
- candidate scope matches declared scope;
- rollback artifact exists.

Promotion criteria should be configuration, not hidden model judgment.

---

## Milestone 16 — First closed improvement loop

### Target

Demonstrate the smallest real system-level recursive improvement cycle.

Example experiment:

```text
1. Harness X detects repeated irrelevant retrievals.
2. Self-analysis groups traces and identifies retrieval policy as suspected cause.
3. System proposes a higher relevance threshold / changed scoring policy.
4. Candidate policy is instantiated in sandbox.
5. Baseline and candidate run against retrieval-sensitive tasks.
6. Candidate improves success/cost without forbidden regressions.
7. Candidate is promoted as retrieval-gate v2.
8. Future self-analysis now runs using the improved system.
```

The base reasoning-model weights remain unchanged.

This is the first meaningful system-level self-improvement milestone.

### Important measurement

Record whether the new system becomes better at producing the **next** useful improvement.

That is more interesting than one isolated optimization.

---

# PART VI — LEARNED PERIPHERAL CONTROLLERS

## Milestone 17 — Collect gate training data

Before training gates, collect traces from deterministic policies.

For each decision store:

```text
state features
policy decision
model recommendation if available
actual outcome
cost
later usefulness
```

This creates supervised/offline-RL style data for small controllers.

### Candidate learned components

Train in this order:

1. retrieval ranker;
2. memory write classifier;
3. anomaly classifier;
4. routine router;
5. compute/depth predictor;
6. maintenance trigger.

These models should be small and independently replaceable.

### Required baseline

Every learned gate competes against its deterministic predecessor.

A learned controller that is less interpretable and not measurably better should not be promoted.

---

## Milestone 18 — Dynamic compute allocation

### Conventional model version

The compute gate may select among:

```text
stop
another reasoning call
larger context
stronger model
extra retrieval
extra verification
parallel candidate generation
```

### Metrics

- task success vs total inference cost;
- unnecessary extra calls;
- premature stopping;
- difficult-task allocation;
- calibration of predicted value of more compute.

### Failure modes to test

- always stop early;
- always request maximum compute;
- route every task to strongest model;
- positive feedback between uncertainty and retrieval explosion;
- gate decision alters trajectory so its own prediction becomes invalid.

---

# PART VII — ADVANCED RESEARCH BRANCHES

These are **not blockers** for the primary Harness X implementation.

## Milestone 19A — Recurrent-depth core experiment

### Goal

Test whether effective reasoning depth can become a gated resource while parameter count stays relatively small.

Potential path:

1. integrate an existing recurrent-depth research model behind `ReasoningCore`;
2. expose fixed recurrence depth first;
3. benchmark depth vs quality/cost;
4. add external Harness X depth selection;
5. compare deterministic depth policy vs learned gate;
6. only then explore core-level gate training.

### Success criterion

Dynamic depth must improve the quality/cost frontier, not merely produce longer computation.

---

## Milestone 19B — DiffusionBlocks research experiment

### Goal

Determine whether block-wise denoising-style training is useful for a future Harness X reasoning core or controller stack.

### Do not begin at billion scale

First reproduce a small published-style experiment.

Then test:

```text
B = 1
B = 2
B = 3
B = 4
...
```

Measure:

- peak active memory;
- total training compute;
- wall-clock time;
- quality;
- communication volume;
- degradation as blocks become too shallow.

### Key research question

Does useful minimum block capacity behave more like:

```text
fixed absolute depth
```

or:

```text
fixed fraction of total depth?
```

The answer determines whether memory savings can grow strongly with model depth or naturally plateau.

### Integration rule

Do not make the main Harness X architecture depend on DiffusionBlocks until it demonstrates value on relevant reasoning workloads.

---

## Milestone 19C — Recurrent depth + block-wise training + gates

Only attempt this after both preceding branches independently work.

Conceptual target:

```text
state
 -> recurrent reasoning block
 -> gate: enough progress?
      yes -> exit
      no  -> repeat
```

with training methods that avoid one enormous unrolled backpropagation graph where possible.

This is a research target, not an MVP feature.

---

# PART VIII — MODEL-WEIGHT IMPROVEMENT COMES LAST

## Milestone 20 — Decide whether the core is actually the bottleneck

Before changing the main model weights, use accumulated telemetry to ask:

- Are failures caused by reasoning capability or bad state?
- Is relevant memory available but not used?
- Are routines wrong?
- Is verification weak?
- Is context construction losing important structure?
- Are tool interfaces inadequate?
- Is the compute gate stopping too early?
- Does a stronger drop-in model solve the failure without architectural changes?

Only if evidence repeatedly points to the reasoning core should deeper adaptation become a priority.

### Escalation ladder

```text
external memory/routine improvement
-> deterministic controller improvement
-> learned peripheral controller
-> self-model LoRA
-> task-specific adapter
-> recurrent retrofit / architecture experiment
-> broader fine-tuning
-> core pretraining changes
```

This keeps expensive and irreversible interventions last.

---

# PART IX — CROSS-CUTTING ENGINEERING REQUIREMENTS

## 21. Configuration

All behavioral thresholds should be configuration-backed.

Example:

```yaml
working_memory:
  capacity_units: 128
  maintenance_pressure: 0.85

gates:
  retrieval:
    policy: deterministic_v1
  compute:
    max_reasoning_calls_per_step: 3

improvement:
  allow_auto_promotion: false
  max_candidate_compute: ...
```

Early threshold values are experimental defaults, not architecture truths.

---

## 22. Provenance everywhere

Every important memory or conclusion should answer:

```text
Where did this come from?
When?
From which system version?
Was it observed, inferred, or proposed?
What evidence supports it?
Has it been verified?
```

Provenance should be a shared core type rather than reinvented per memory class.

---

## 23. Budgets as external authority

Budgets should not be suggestions inside prompts.

Implement external counters for:

- model calls;
- tokens if available;
- wall-clock time;
- tool actions;
- retries;
- retrieval count;
- experiment compute;
- disk/memory quotas.

The model can request more budget. It cannot grant itself more budget.

---

## 24. Permissions

Use capability-style permissions.

Examples:

```text
memory.read.semantic
memory.write.episodic
tool.files.read
tool.files.write.sandbox
improvement.propose.config
improvement.test.routine
improvement.promote
```

The improvement system should have narrower permissions than a human operator during early versions.

---

## 25. Failure injection

Fault injection is both a test tool and future training-data generator.

Implement reusable injectors for:

- memory corruption candidate;
- missing memory;
- wrong retrieval;
- stale self-schema;
- tool timeout;
- tool incorrect result;
- scheduler delay;
- budget exhaustion;
- verifier disagreement;
- gate misconfiguration.

Never inject faults into the live system outside a dedicated test/sandbox mode.

---

## 26. Deterministic replay before fancy observability

A web dashboard can wait.

First make it possible to answer:

> Why did task T change from state A to state B at step 412?

using only stored structured events and versions.

That capability will be more valuable for architecture research than pretty logs.

---

# PART X — FIRST PRACTICAL RELEASE TARGETS

## v0.1 — Cognitive skeleton

Contains:

- core contracts;
- trace ledger;
- replay;
- orchestrator state machine;
- goal memory;
- working state;
- episodic memory;
- error buffer;
- deterministic gates;
- task/recovery/verification routines;
- mock tools;
- deterministic reasoning stub;
- synthetic autonomy benchmark.

**No real LLM required.**

Success means the architecture itself works.

---

## v0.2 — Model socket

Adds:

- context builder;
- `ReasoningCore` adapter;
- one local real model;
- model-assisted planning/hypothesis generation;
- real-model benchmark comparison.

Success means the model can be inserted without taking ownership of system state.

---

## v0.3 — Self-aware operation

Adds:

- external self-schema;
- rolling telemetry;
- self-analysis routine;
- fault-injection dataset generator;
- initial self-model adapter training.

Success means the model can diagnose Harness X from real telemetry better than the unadapted baseline.

---

## v0.4 — Controlled self-improvement

Adds:

- candidate system;
- sandbox;
- baseline/candidate comparison;
- promotion/rejection/rollback;
- first bounded self-generated policy improvement.

Success means Harness X can improve one part of its surrounding architecture through evidence while keeping the reasoning core unchanged.

---

## v0.5 — Learned control

Adds:

- learned retrieval/routing controllers;
- dynamic compute allocation;
- controller A/B testing;
- longer autonomous benchmarks.

Success means learned control beats deterministic baselines on capability/cost without unacceptable regressions.

---

## Experimental releases after v0.5

Possible tracks:

```text
recurrent-depth core
DiffusionBlocks reproduction
block-wise controller training
multi-core routing
architecture mutation experiments
adapter/self-training cycles
```

These should remain separate experiments until they earn promotion into the core architecture.

---

# 27. What not to optimize too early

Avoid spending early effort on:

- huge vector databases;
- complex distributed systems;
- multi-agent swarms;
- giant prompt templates;
- foundation-model pretraining;
- UI polish;
- arbitrary code self-modification;
- dozens of memory types;
- learned gates before deterministic baselines exist;
- benchmark gaming.

The highest-value early asset is a **small architecture whose behavior can be understood completely**.

---

# 28. Recommended first coding sequence

If implementation begins immediately, the first concrete sequence should be:

```text
1. pyproject + src package + tests
2. IDs / provenance / event contracts
3. append-only trace store
4. state replay
5. orchestrator modes / state machine
6. goal store
7. bounded working state
8. episodic store
9. error buffer
10. deterministic gate interface
11. retrieval/write/focus/maintenance gates
12. routine interface
13. scripted task + recovery routine
14. mock tool registry/executor
15. synthetic 100+ step autonomy scenario
16. checkpoint/resume test
17. self-schema generator
18. only then ReasoningCore adapter
```

That sequence gives Harness X a real skeleton before any model can mask the weaknesses.

---

# 29. Definition of the first major success

The first major success is **not** an impressive chatbot response.

It is this:

> A long-running Harness X task preserves goals, manages bounded working state, retrieves relevant prior experience, recovers from injected failures, records causal telemetry, suspends/resumes correctly, and can explain its current architecture from ground-truth state — all before a real LLM is required.

Once that works, plugging in a capable reasoning model becomes an upgrade to an existing cognitive machine rather than the moment the machine first comes into existence.