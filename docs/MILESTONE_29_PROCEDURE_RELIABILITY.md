# Milestone 29 — Procedure Reliability and Adaptation

Milestone 29 adds a software-owned reliability gate for the reusable project procedures introduced in Milestone 28.

The distinction is intentional:

- **M28 project memory** answers whether repeated software-verified successful tasks support a reusable procedure.
- **M29 procedure reliability** answers whether that still-supported procedure is currently reliable enough for automatic reuse.

M29 does not rewrite M28 history. A procedure can remain historically `ACTIVE` in project memory while being temporarily `SUSPENDED` from automatic reuse by the reliability sidecar.

## Why this layer exists

Repeated historical success is not a permanent guarantee. Repository structure, dependencies, verification expectations, and task distributions change. A procedure that was useful on earlier tasks can later become a source of repeated failures.

M29 therefore closes a practical adaptation loop:

```text
M28 repeated verified support
        ↓
ACTIVE project procedure
        ↓
model declares actual reuse
        ↓
software-owned final task verdict
        ↓
M29 verified reuse outcome
     /                \
reliable             degraded
   ↓                    ↓
ELIGIBLE            SUSPENDED
                         ↓
              fresh verified support
                         ↓
                 revalidation gate
                         ↓
                    ELIGIBLE
```

This is a reliability controller around procedural memory, not autonomous procedure rewriting.

## Persistent state

M29 stores two artifacts beside the M28 project-memory files:

```text
<project-memory-root>/
    project-memory.json
    project-memory-episodes.jsonl
    procedure-reliability.json
    procedure-reliability-usage.jsonl
```

`procedure-reliability.json` is an atomic, fingerprinted state snapshot. It contains the project identity, policy, revision, per-procedure reliability records, aggregate usage count, and state fingerprint.

`procedure-reliability-usage.jsonl` is an append-only ledger of verified procedure-reuse outcomes. Each row is fingerprinted and binds:

- project ID;
- procedure ID;
- M28 task episode ID;
- final task success/failure;
- bounded failure-mode text when applicable;
- creation timestamp.

The ledger is the durable evidence source. The snapshot is the derived controller state.

## Default reliability policy

The current deterministic policy is deliberately small and inspectable:

```text
consecutive verified reuse failures >= 2
    → SUSPENDED

OR

verified uses >= 4
and all-time verified success rate < 0.50
    → SUSPENDED
```

Revalidation requires:

```text
2 distinct fresh software-verified successful supports
for the same M28 procedure content
after suspension
    → ELIGIBLE
```

The persisted policy is fingerprinted with the reliability state. Supplying a different policy to an existing reliability root is rejected rather than silently changing historical semantics.

## Authority boundary

The model does not directly control M29 state.

During a task the model may include an M28 `project_memory_update` proposal containing `used_procedure_ids`. Harness X accepts such an ID only when it refers to a procedure that is:

1. M28 `ACTIVE`;
2. conflict-free;
3. M29 `ELIGIBLE`.

The model declaration says that the procedure materially informed the task. It does **not** decide whether the reuse succeeded.

At task closeout Harness X uses the software-owned final coding/browser verification verdict to record the reuse outcome. A model cannot label its own reuse successful, suspend a procedure, or re-enable a suspended procedure.

This is grounded outcome accounting, not perfect causal attribution. A task can fail for reasons unrelated to the declared procedure; M29 intentionally uses verified task outcomes as a conservative operational signal.

## Closeout ordering

For a reliability-aware coding task, closeout is ordered as follows:

```text
software-owned final task verdict
    ↓
append M28 task episode
    ↓
for every valid declared procedure reuse:
    record M28 historical usage
    append M29 verified reuse evidence
    update M29 reliability state
    ↓
if task succeeded:
    admit staged M28 memory candidates
    ↓
for freshly supported active procedures:
    feed post-suspension support to M29 revalidation
```

Reuse evidence is recorded before newly learned candidates can alter memory/conflict state at the same closeout. This preserves the fact that the procedure was validly available when it was actually used.

## Suspension semantics

Suspension is intentionally non-destructive.

When M29 suspends a procedure:

- the M28 entry remains historically `ACTIVE` if its support evidence still qualifies;
- M28 support episode IDs are retained;
- M28 usage counts and known failure modes remain retained;
- the M29 record stores the suspension reason and support-count baseline;
- the procedure is removed from automatic active-memory selection;
- `project_memory_recall` suppresses it through the reliability-aware facade;
- the model may no longer declare it as a valid `used_procedure_id`.

Facts are not subject to M29 procedure reliability filtering.

## Revalidation semantics

A suspended procedure does not recover merely because time passes or because the model asks to use it again.

Fresh M28 support must come from later software-verified successful task episodes supporting the same normalized procedure content. M29 counts only support above the support-count baseline captured when the procedure was suspended.

The same episode cannot count twice. After the configured number of distinct fresh supports, M29 restores `ELIGIBLE` status and automatic retrieval becomes available again.

## Crash behavior and idempotency

M29 uses append-first durability for reuse evidence:

1. append and `fsync` the verified usage row;
2. derive the next reliability record;
3. atomically replace the fingerprinted state file.

If the process stops after step 1 but before step 3, reopening the store reconciles the durable ledger count. Retrying the same `(procedure_id, episode_id)` finds the existing evidence, applies the missing lifecycle transition, and does not append a duplicate row.

If a usage row is duplicated or its fingerprint is invalid, loading fails closed.

## Runtime composition

M29 wraps the M27/M28 coding runtime rather than replacing lower-level authorities.

The default `harness-x-code` runtime stack is now conceptually:

```text
coding task
    ↓
M27 durable long-horizon task state
    ↓
M28 project memory / procedural support
    ↓
M29 current procedure reliability gate
    ↓
reasoning core
    ↓
typed tools / permissions / budgets
    ↓
software-owned verification
    ↓
M28 + M29 closeout evidence
```

M29 is implemented for all four existing coding modes:

- direct in-place repository task;
- isolated repository task;
- browser-verified repository task;
- isolated browser-verified repository task.

For isolated tasks, both M28 and M29 persistence remain scoped to the **source project**, not the temporary task clone.

## Model context and recall

The model receives two distinct sections:

- `project_memory`: active reusable project knowledge after the M29 filter;
- `procedure_reliability`: controller-owned reliability policy/state summary.

The reliability-aware wrapper reserves context headroom and deterministically reduces the sidecar projection if the composed context approaches the configured 76,000-character bound. Suspended procedures do not consume active project-memory slots.

`project_memory_recall` is registered against the reliability-aware facade, so normal recall cannot bypass an M29 suspension.

## Report fields

M29 coding reports extend M28 reports with:

- `procedure_reliability_state_path`;
- `procedure_reliability_usage_path`;
- `procedure_reliability_state_fingerprint`;
- `procedure_reliability_state_revision`;
- `procedure_reliability_usage_total`;
- `procedure_reliability_eligible_records`;
- `procedure_reliability_suspended_count`;
- `procedure_reliability_suspended_ids`.

The M28 project-memory fields remain present, allowing an operator to distinguish historical support state from current reliability eligibility.

## Deterministic acceptance coverage

M29 qualification includes:

1. standalone suspension after verified reuse failures without erasing M28 support;
2. revalidation from two distinct fresh verified supports;
3. state fingerprint tamper rejection;
4. an eight-task runtime lifecycle:
   - support 1;
   - support 2 / M28 activation;
   - verified failed reuse 1;
   - verified failed reuse 2 / M29 suspension;
   - later task confirms context and recall suppression;
   - fresh verified support 1;
   - fresh verified support 2 / M29 revalidation;
   - later task successfully reuses the recovered procedure;
5. browser + isolation composition while the source project remains unchanged;
6. append-before-state crash recovery with exact-once retry behavior;
7. context pressure with large M28 memory and many M29 suspensions under the explicit context bound;
8. CLI selection of M29 runtimes by default.

## Known limits

M29 is intentionally narrower than full procedural self-improvement.

- Procedure-use attribution depends on the model explicitly declaring which active procedure materially informed the task. Software validates the ID and owns the outcome, but this is not perfect causal identification.
- The default reliability policy is global per procedure. It does not yet condition success rate on task category, repository region, dependency version, or other context.
- A suspended procedure is gated, not automatically rewritten.
- M28 semantic conflicts remain a separate fail-closed mechanism; M29 does not resolve contradictory project memories.
- Revalidation proves renewed successful support for the same procedure content. It does not prove that the procedure is optimal.
- The controller currently uses deterministic thresholds rather than a learned reliability model.

These limits define the next improvement surface rather than hidden behavior.

## Next direction

A natural successor is failure-driven **procedure revision with lineage**:

```text
repeated verified failure
    ↓
cluster failure evidence
    ↓
propose bounded procedure revision
    ↓
isolated coding-task validation
    ↓
verified replacement candidate
    ↓
promote new revision while preserving parent/history
```

That would move Harness X from reliability gating into controlled procedural adaptation without giving the reasoning model unilateral authority over reusable system behavior.
