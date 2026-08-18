# Harness X — Planned Cognitive Architecture

Status: **design document**  
Purpose: define the intended system before implementation begins.

---

## 1. What Harness X is trying to build

Harness X is an architecture-first cognitive system for long-running autonomous work, explicit memory, dynamic compute allocation, and controlled system-level self-improvement.

The central assumption is:

> A model is a reasoning component. It is not the entire intelligent system.

Harness X therefore avoids making the LLM responsible for pretending to be every cognitive function at once. Memory, goals, state, telemetry, routing, permissions, verification, scheduling, and improvement are represented by explicit software components with their own contracts.

The reasoning model consumes structured state and proposes changes. The system remains authoritative over what state actually exists and what changes are accepted.

This distinction is fundamental.

A model may say:

> "I think my working memory is overloaded."

Harness X should instead be able to expose facts such as:

```text
working_memory.capacity = 128
working_memory.used = 119
working_memory.pressure = 0.929
working_memory.evictions_last_10_steps = 7
goal_items_evicted = 0
```

The model can reason over those facts, but the facts do not depend on model introspection.

---

## 2. Primary design goals

### 2.1 Architecture before model specialization

The surrounding cognitive machinery should be designed and validated before committing to a particular model family.

The model should sit behind a stable adapter so that a 7B model, a larger model, a recurrent-depth model, or multiple specialized cores can be exchanged without rewriting the rest of the system.

### 2.2 Explicit state instead of prompt-only state

Prompt context is a transport mechanism, not the authoritative memory system.

Harness X should keep important state in structured stores and build context from that state when the reasoning core is invoked.

### 2.3 Different memories need different behavior

Not all stored information should have the same lifecycle.

A temporary observation, an established semantic fact, a failed hypothesis, a long-running goal, and a reusable procedure should not all be inserted into one vector database with identical retrieval rules.

Each memory class should have its own:

- schema;
- write policy;
- retention policy;
- confidence model;
- provenance requirements;
- retrieval routine;
- consolidation routine;
- invalidation rules;
- access permissions.

### 2.4 Gates control information and compute flow

Gates are treated as explicit control mechanisms rather than vague metaphors.

They may begin as deterministic policies and later become learned controllers.

Examples include:

- retrieval gate;
- write gate;
- focus gate;
- compute/depth gate;
- routine-selection gate;
- verification gate;
- model-selection gate;
- maintenance gate;
- improvement gate.

### 2.5 Separate fast cognition from slow adaptation

Harness X should support multiple timescales:

```text
milliseconds / seconds  -> state transitions, routing, focus, inference
minutes / hours          -> memory consolidation, strategy updates, task adaptation
longer cycles            -> architecture experiments, controller training
rarely                    -> adapters or reasoning-core weight changes
```

This prevents every new observation from immediately becoming a permanent model change.

### 2.6 Improvement must be observable and reversible

A system that can change itself needs stronger versioning than a system that cannot.

Candidate improvements should be isolated from the active system until tested.

No live component should silently overwrite itself in place.

---

## 3. Non-goals for the first versions

Harness X is **not initially** trying to:

- pretrain a foundation model from scratch;
- autonomously rewrite its own base weights;
- invent a biologically accurate brain simulation;
- simulate literal fluid mechanics for information flow;
- depend on one experimental architecture such as recurrent depth or DiffusionBlocks;
- maximize benchmark scores before the architecture is understandable;
- hide internal behavior behind free-form chain-of-thought logs;
- equate more context tokens with better memory.

The first objective is a coherent, inspectable system.

---

## 4. System overview

```text
                           +----------------------+
                           | Goals / Constraints  |
                           +----------+-----------+
                                      |
                                Priority Gate
                                      |
                                      v
+----------------+           +------------------+           +----------------+
| Memory System  |<--gates-->|  Working State   |<--gates-->| Routine Router |
+-------+--------+           +---------+--------+           +-------+--------+
        ^                              |                            |
        |                         Focus Gate                        |
        |                              |                            |
        |                              v                            v
        |                     +------------------+          +---------------+
        |                     | Reasoning Core   |<-------->| Tool / Model  |
        |                     +---------+--------+          |   Selection   |
        |                               |                   +---------------+
        |                    +----------+----------+
        |                    |          |          |
        |                    v          v          v
        +----------------- Memory     Action    Evaluation
                                    Proposal       |
                                       |           v
                                       v      +-----------+
                                  Environment | Error /   |
                                              | Self State|
                                              +-----+-----+
                                                    |
                                             Scheduler / Modes
                                                    |
                              +---------------------+--------------------+
                              |                                          |
                              v                                          v
                       External Task Work                         Internal Work
                                                                    |
                                                       +------------+------------+
                                                       |                         |
                                                       v                         v
                                                  Maintenance              Improvement Lab
```

---

## 5. Authoritative system state

Harness X should distinguish three categories of state.

### 5.1 Ground-truth state

Produced directly by software or tools.

Examples:

- memory utilization;
- current routine;
- current goal ID;
- tool return code;
- token/compute budget usage;
- task elapsed time;
- number of retries;
- candidate version IDs;
- benchmark result;
- file hashes;
- dependency graph state.

This state should never be replaced by a model's opinion.

### 5.2 Inferred state

Computed from evidence but not certain.

Examples:

- estimated task difficulty;
- estimated memory relevance;
- suspected failure cause;
- confidence that a semantic memory is correct;
- expected value of running another reasoning cycle.

Inferred state must retain uncertainty and provenance.

### 5.3 Proposed state

Suggested by a reasoning core or routine but not yet accepted.

Examples:

- proposed memory write;
- proposed goal update;
- proposed architectural change;
- proposed action;
- proposed deletion;
- proposed confidence change.

A proposal becomes authoritative only through the owning subsystem's validation path.

---

## 6. Core subsystems

## 6.1 Orchestrator / scheduler

The orchestrator owns execution lifecycle.

Responsibilities:

- create and resume tasks;
- maintain task IDs and parent/child relationships;
- choose active operating mode;
- invoke routines;
- enforce budgets;
- coordinate gates;
- suspend and resume work;
- trigger maintenance;
- route failures;
- record trace events;
- prevent concurrent components from corrupting shared state.

The orchestrator should be mostly deterministic in early versions.

The reasoning model may recommend scheduling decisions, but it should not secretly become the scheduler.

### Operating modes

Initial modes:

```text
READY
TASK_ACTIVE
TOOL_WAIT
VERIFY
RECOVERY
MAINTENANCE
CONSOLIDATION
EXPERIMENT
IMPROVEMENT_EVALUATION
SUSPENDED
```

The exact names may change, but modes should be explicit and externally observable.

---

## 6.2 Working state

Working state is the high-priority short-lived representation of the current problem.

It should remain deliberately bounded.

Possible contents:

- active goal reference;
- current plan fragment;
- unresolved local dependencies;
- current entities/files/components;
- recent observations;
- active hypotheses;
- currently pinned constraints;
- immediate next actions;
- small amount of scratch state.

Working state should have explicit pressure metrics.

Example:

```yaml
capacity_units: 128
used_units: 93
pressure: 0.73
pinned_units: 21
eviction_candidates: 17
last_compaction_step: 411
```

A pressure threshold can trigger compaction, externalization, retrieval suppression, or a maintenance routine.

---

## 6.3 Goal and constraint memory

Goals should not live only inside conversational context.

A goal object should carry at least:

```yaml
goal_id: string
parent_goal_id: optional[string]
description: string
priority: float
status: pending | active | blocked | complete | abandoned
success_criteria: list
constraints: list
permissions: list
stop_conditions: list
created_at: timestamp
updated_at: timestamp
source: provenance
```

Goals and constraints should normally be pinned against accidental working-memory eviction.

Subgoals may change frequently; governing constraints should change much less frequently.

---

## 6.4 Episodic memory

Episodic memory stores what happened.

It should preserve causal structure rather than only summaries.

Useful fields:

- task ID;
- time range;
- triggering state;
- action or reasoning routine used;
- evidence available at the time;
- outcome;
- verification result;
- failure classification;
- dependencies;
- links to raw traces;
- compression level.

Episodic memory is especially important for autonomous work because the system should be able to answer:

> Have I encountered this failure pattern before, what did I try, and what happened?

---

## 6.5 Semantic memory

Semantic memory stores consolidated reusable knowledge.

A semantic memory should not be treated as true merely because the model generated it.

Suggested fields:

```yaml
memory_id: string
claim: structured_or_text
confidence: float
provenance: list[source]
evidence_for: list
contradictions: list
valid_from: optional[timestamp]
valid_until: optional[timestamp]
last_verified: optional[timestamp]
retrieval_tags: list
embedding_ref: optional[string]
version: int
```

Promotion from episodic observations into semantic memory should require a consolidation routine.

---

## 6.6 Procedural / routine memory

Procedural memory stores reusable ways of doing things.

Examples:

- repository-debugging procedure;
- research procedure;
- experiment-design procedure;
- memory-consolidation procedure;
- code-review procedure;
- planning procedure;
- failure-analysis procedure.

A routine should be versioned and measurable.

```yaml
routine_id: debugging
version: 7
inputs: [...]
outputs: [...]
preconditions: [...]
policy_ref: ...
success_rate: ...
cost_profile: ...
known_failure_modes: [...]
```

This is one of the easiest early self-improvement surfaces because routines can change without changing the reasoning core.

---

## 6.7 Error / anomaly buffer

The error buffer is not simply a log file.

It tracks unresolved evidence that something is wrong.

Examples:

- verifier disagreement;
- repeated failed actions;
- contradictions between memories;
- unexpected tool outputs;
- goal drift;
- low-confidence state transitions;
- failed predictions;
- resource exhaustion;
- routine underperformance.

Error pressure may trigger a failure-analysis routine.

Important distinction:

> An unresolved anomaly is not yet an explanation.

The system should preserve the difference between observed failure and suspected cause.

---

## 6.8 Experiment / hypothesis buffer

The experiment buffer stores candidate explanations and candidate changes before they become accepted knowledge or live architecture.

A candidate should include:

```yaml
candidate_id: string
hypothesis: string
predicted_effects: list
change_scope: list[component]
required_experiments: list
risk_level: enum
status: proposed | testing | rejected | accepted
baseline_version: string
candidate_version: string
results: list
```

This buffer is central to controlled self-improvement.

---

## 6.9 Self-model and telemetry

The reasoning core should understand the system it operates inside, but its self-model should be grounded in actual telemetry.

The self-model contains two layers.

### Learned self-understanding

The reasoning model learns general concepts such as:

- what working memory is;
- what a gate does;
- how confidence differs from evidence;
- how routines are selected;
- what a buffer overflow means;
- what permissions are;
- how to interpret telemetry;
- why candidate changes must be evaluated.

### External current self-description

The exact current system is stored externally and injected when needed.

Example:

```yaml
system_version: 0.4.2
reasoning_core:
  adapter: qwen_adapter
  model_id: example-model
memory:
  episodic: v3
  semantic: v2
  working: v5
controllers:
  focus_gate: deterministic-v4
  retrieval_gate: learned-v2
routines:
  planning: v7
  debugging: v9
known_issues:
  - id: HX-142
    description: retrieval over-expands after long tool chains
telemetry:
  working_pressure: 0.64
  semantic_retrieval_precision_rolling: 0.81
```

The model therefore does not need retraining every time Harness X adds a new component.

---

## 6.10 Reasoning-core interface

The reasoning model should not be tightly coupled to system internals.

A first interface can look conceptually like:

```python
class ReasoningRequest:
    task_id: str
    goal: GoalView
    active_state: WorkingStateView
    retrieved_memory: list[MemoryView]
    routine: RoutineView
    self_state: SelfStateView
    available_actions: list[ActionSchema]
    budget: ComputeBudget

class ReasoningResult:
    proposed_actions: list[ActionProposal]
    proposed_memory_writes: list[MemoryProposal]
    proposed_state_changes: list[StateProposal]
    requested_retrievals: list[RetrievalRequest]
    requested_compute: ComputeRequest | None
    uncertainty: float
    claims: list[Claim]
```

The actual implementation may use Pydantic models, dataclasses, protobuf, or another typed contract.

The important property is that reasoning input and output are inspectable and model-independent.

---

## 6.11 Tool environment

Tools are capabilities exposed through explicit schemas.

Each tool should define:

- name;
- version;
- input schema;
- output schema;
- permissions required;
- cost model;
- timeout;
- side effects;
- idempotency information;
- rollback support if applicable.

Tool outputs should become authoritative observations only after parsing/validation.

The model should not be able to invent a successful tool result.

---

## 6.12 Evaluator / verifier layer

Harness X should distinguish generation from acceptance.

Verification may include:

- deterministic validators;
- unit tests;
- schema checks;
- cross-model review;
- source checks;
- simulations;
- constraint checks;
- environment observation;
- statistical evaluation.

A verifier can reject a plausible-looking model proposal without corrupting state.

This layer is essential for long autonomous tasks because small unverified errors otherwise compound over time.

---

## 7. Gates

Gates are the main control surface for dynamic information flow.

They should begin simple and interpretable.

## 7.1 Retrieval gate

Decides whether additional memory should be retrieved and from which store.

Inputs may include:

- current goal;
- active entities;
- uncertainty;
- routine;
- working-memory pressure;
- recent retrieval usefulness;
- cost budget.

Outputs:

- retrieve or not;
- memory classes;
- query;
- result count / budget;
- confidence threshold.

## 7.2 Write gate

Decides whether a proposal belongs in:

- working state;
- episodic memory;
- semantic candidate memory;
- error buffer;
- experiment buffer;
- nowhere.

This prevents every generated thought from becoming permanent memory.

## 7.3 Focus gate

Allocates priority among active state items.

It can pin important goals or dependencies and suppress low-value distractions.

The focus gate should expose its decisions so goal drift can be diagnosed.

## 7.4 Compute / depth gate

Determines whether additional reasoning is worth the cost.

In a conventional LLM integration it may choose:

- one more model call;
- a larger context;
- a stronger model;
- another verification pass;
- stop.

In a recurrent-depth model it may eventually control additional latent iterations.

## 7.5 Routine-selection gate

Chooses the active operating procedure.

Examples:

```text
normal task
planning
research
coding
verification
failure analysis
memory consolidation
self-analysis
experiment design
architecture improvement
```

## 7.6 Model-selection gate

If multiple reasoning cores are available, the gate chooses the cheapest sufficient core.

For example:

```text
fast small core -> routine tasks
coding core     -> repository implementation
large core      -> difficult planning / diagnosis
vision core     -> image state
```

## 7.7 Maintenance gate

Triggers internal work when system pressure crosses thresholds.

Examples:

- working-memory pressure too high;
- episodic store needs consolidation;
- unresolved-error count high;
- stale semantic memories require verification;
- autonomous task reaches a safe checkpoint.

---

## 8. Routines

A routine is a versioned procedure that coordinates state, gates, model calls, tools, and verification.

Initial routines should include:

### 8.1 Task routine

```text
observe -> update state -> retrieve -> reason -> act -> observe -> verify -> store
```

### 8.2 Planning routine

Produces or revises an explicit task graph instead of relying on hidden model intent.

### 8.3 Research routine

Separates:

```text
question -> evidence gathering -> source assessment -> synthesis -> unresolved claims
```

### 8.4 Debugging routine

Separates symptoms, hypotheses, experiments, and confirmed root causes.

### 8.5 Verification routine

Checks proposed conclusions/actions before they become trusted state.

### 8.6 Recovery routine

Restores task coherence after:

- interruption;
- context failure;
- tool error;
- goal drift;
- inconsistent memory;
- invalid action.

### 8.7 Consolidation routine

Transforms episodic traces into compact semantic/procedural knowledge.

### 8.8 Self-analysis routine

Uses telemetry and traces to diagnose persistent system weaknesses.

### 8.9 Improvement routine

Turns a diagnosed weakness into a bounded candidate change and experiment plan.

---

## 9. Autonomous execution loop

A long-running task should not simply be a repeated LLM chat.

A planned loop is:

```text
1. Read authoritative goal and constraints.
2. Inspect current working state.
3. Decide whether retrieval is required.
4. Select routine and compute budget.
5. Build a bounded ReasoningRequest.
6. Invoke reasoning core.
7. Validate returned proposals.
8. Execute permitted action(s).
9. Observe real result.
10. Run required verification.
11. Update episodic/error/state stores.
12. Recalculate task and memory pressure.
13. Continue, maintain, recover, suspend, or finish.
```

The orchestrator owns the loop; the model participates in it.

---

## 10. Internal maintenance loop

When external work is not the best use of compute, Harness X may switch modes.

This is the implementation behind the earlier "sleep" analogy: not inactivity, but a different working state.

Possible maintenance work:

- compact working state;
- consolidate episodic memory;
- deduplicate semantic memories;
- re-verify stale knowledge;
- cluster failure patterns;
- update retrieval indices;
- score routine performance;
- summarize unfinished tasks;
- produce improvement hypotheses.

Maintenance should be interruptible when higher-priority external work arrives.

---

## 11. Self-model training

Harness X should provide a small specialized curriculum so the reasoning core understands how to operate the system.

This is not intended to replace foundation-model training.

### 11.1 Structural curriculum

Teach:

- component names and responsibilities;
- schemas;
- permissions;
- state ownership;
- difference between observed, inferred, and proposed state.

### 11.2 Operational curriculum

Teach cases such as:

- high working-memory pressure;
- contradictory retrieved memories;
- missing evidence;
- repeated routine failure;
- goal drift;
- tool failure;
- need for verification;
- safe suspension and resumption.

### 11.3 Causal curriculum

Train the model to reason about changes to its own architecture.

Example:

```text
retrieval threshold: 0.40 -> 0.70
irrelevant retrievals: -43%
missed useful memories: +9%
task success: +12%
```

Ask the model to identify tradeoffs and propose a follow-up experiment.

### 11.4 Deliberately injected defects

Synthetic system failures can produce unusually clean training data because the actual cause is known.

Examples:

- goal pinning disabled;
- retrieval gate too permissive;
- write gate too permissive;
- verification disabled;
- buffer artificially constrained;
- scheduler starvation;
- stale self-schema;
- incorrect routine routing.

This allows training on ground-truth diagnosis rather than generated post-hoc explanations.

---

## 12. System-level self-improvement

Harness X defines early self-improvement over the complete system state `S`, not only model weights `M`.

```text
S0 -> diagnose -> candidate S1 -> evaluate -> promote/reject
```

A valid recursive sequence can therefore be:

```text
S0 improves retrieval
S1 diagnoses failures more accurately
S2 builds better evaluation routines
S3 performs better architecture experiments
S4 improves routing and memory
...
```

The reasoning model may remain unchanged during several generations.

### 12.1 Initial improvement surfaces

Preferred order:

1. memory policies;
2. retrieval;
3. routines;
4. context construction;
5. verification;
6. scheduling;
7. deterministic gate policies;
8. learned small controllers;
9. tools;
10. adapters;
11. larger architectural changes;
12. reasoning-core weight changes.

This order favors cheap, inspectable, reversible improvements first.

### 12.2 Improvement sandbox

Every candidate change should have:

- baseline version;
- candidate version;
- scope;
- expected effect;
- benchmark set;
- resource budget;
- rollback path;
- regression criteria;
- acceptance rule.

The active system should never replace itself merely because it predicts the candidate is better.

Evidence decides promotion.

---

## 13. Versioning model

Harness X should version more than source code.

A run should be reproducible from:

```yaml
system_version: ...
reasoning_core_version: ...
config_version: ...
working_memory_policy: ...
episodic_memory_version: ...
semantic_memory_version: ...
retrieval_gate_version: ...
focus_gate_version: ...
routine_versions: ...
tool_versions: ...
benchmark_version: ...
random_seed: ...
```

This is necessary to know *why* capability changed.

---

## 14. Trace and replay

All important state transitions should produce structured events.

Example:

```yaml
trace_id: ...
task_id: ...
step: 412
event_type: gate_decision
component: retrieval_gate
input_digest: ...
decision:
  retrieve: true
  stores: [episodic, semantic]
  budget: 12
reason: structured_fields_if_available
system_version: ...
timestamp: ...
```

The objective is not to store private chain-of-thought.

The objective is to store **observable system causality**:

- what state existed;
- what component acted;
- what it decided;
- what changed;
- what the environment returned;
- what verification concluded.

Deterministic replay should be supported wherever external nondeterminism allows it.

---

## 15. Evaluation model

Harness X should measure the whole system rather than only model benchmark scores.

Core dimensions:

### Capability

- task success;
- quality;
- planning correctness;
- recovery rate;
- tool-use accuracy.

### Autonomy

- successful action horizon;
- time until human intervention;
- number of recoverable vs unrecoverable failures;
- goal retention over long tasks.

### Memory

- retrieval precision/recall;
- useful memory hit rate;
- stale-memory rate;
- contradiction rate;
- consolidation value;
- working-state pressure.

### Control

- compute spent per successful task;
- unnecessary retrievals;
- unnecessary model calls;
- routine-routing accuracy;
- gate calibration.

### Self-model

- diagnosis accuracy;
- ability to identify the actual failing subsystem;
- calibration between confidence and evidence;
- prediction accuracy for candidate changes.

### Improvement

- candidate success rate;
- regression rate;
- improvement per experiment;
- improvement per unit compute;
- rate of false promotions;
- rollback frequency.

---

## 16. Experimental research branches

The following ideas fit Harness X but must remain optional.

## 16.1 Recurrent / looped depth

Depth-recurrent models reuse a block multiple times so effective inference depth can increase without proportionally increasing parameters.

Potential Harness X connection:

```text
reasoning state -> recurrent block -> depth gate -> continue / stop
```

Relevant research:

- *Scaling up Test-Time Compute with Latent Reasoning: A Recurrent Depth Approach*  
  https://arxiv.org/abs/2502.05171
- *Teaching Pretrained Language Models to Think Deeper with Retrofitted Recurrence*  
  https://arxiv.org/abs/2511.07384

This is interesting because Harness X already intends to model compute allocation explicitly.

It should not be assumed that more recurrence always improves reasoning; depth selection itself is an experimental problem.

## 16.2 Learned depth / halting gates

Learned gates may eventually decide how much computation an input or internal state receives.

This could move Harness X from fixed budgets toward difficulty-dependent compute allocation.

Important failure mode: a gate can learn the wrong stopping policy or distort the trajectory it is meant to read. Therefore learned gates should be evaluated against simple deterministic and post-hoc baselines rather than assumed superior.

## 16.3 DiffusionBlocks-style block-wise training

DiffusionBlocks reframes block-wise training using a diffusion interpretation and reports memory reduction approximately proportional to the number of independently trained blocks in the tested settings.

Relevant research:

- *DiffusionBlocks: Blockwise Training for Generative Models via Score-Based Diffusion*  
  https://arxiv.org/abs/2506.14202

Potential Harness X value:

- lower active training memory;
- weaker inter-block synchronization requirements;
- possible compatibility with recurrent-depth experimentation;
- cheaper training of experimental cognitive cores.

However, this remains a research branch until demonstrated convincingly at the model scales and tasks Harness X cares about.

## 16.4 Small learned controllers

A more immediate path than retraining the core model is to train small components for:

- retrieval scoring;
- routing;
- compute budgeting;
- anomaly detection;
- memory promotion;
- candidate ranking.

This allows neural adaptation while preserving a stable reasoning core.

---

## 17. Architectural evolution

Harness X should be allowed to discover that the original human-designed memory taxonomy is wrong.

For example, the initial architecture might contain:

```text
working
semantic
episodic
procedural
error
experiment
```

Later evidence might justify adding or splitting a class such as:

- unresolved causal relations;
- competing world models;
- unfinished reasoning state;
- task-specific learned representations.

A new memory type should not be created because it sounds cognitively plausible. It should be created because an experiment demonstrates measurable value.

The same principle applies to gates and routines.

---

## 18. Architectural success criterion

The project succeeds architecturally if the complete system becomes measurably more capable, efficient, persistent, and self-correcting while the reasoning-core interface remains stable and replaceable.

The strongest evidence for the central hypothesis would be a sequence where:

```text
same reasoning core
+ better memory/control/routines/evaluation
= substantially better autonomous capability
```

followed by successful system-generated improvements that further increase those capabilities without requiring immediate foundation-model retraining.

---

## 19. Open questions

The implementation should preserve enough observability to answer these experimentally:

1. Which memory classes actually improve long-horizon autonomy?
2. Which memory routines should be deterministic versus model-driven?
3. How much should gate decisions rely on learned controllers?
4. Can compute allocation become difficulty-adaptive without gate collapse?
5. How much system capability can improve with a frozen reasoning core?
6. How much self-model training is necessary before external self-schema is sufficient?
7. Which improvements compound and which merely shift failure modes?
8. Does a self-improving architecture converge on structures unlike the original human-designed taxonomy?
9. When does the reasoning core become the dominant bottleneck again?
10. At what point do recurrent-depth or block-wise training approaches become worth the added research complexity?

These are not details to hide. They are the main experiments Harness X exists to make possible.