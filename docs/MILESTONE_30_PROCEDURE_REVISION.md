# Milestone 30 — Failure-Driven Procedure Revision

M30 extends the M28/M29 project-learning stack from **remembering and suspending procedures** to **bounded revision with explicit lineage**.

M28 answers whether repeated verified task episodes support reusable project knowledge. M29 separately answers whether an M28-active procedure is still reliable enough for current reuse. M30 adds the next control loop: when M29 suspends a procedure after degraded verified reuse outcomes, Harness X may create a proposed replacement, validate that replacement in isolated coding tasks, and promote it only after independent evidence supports the new procedure.

M30 does **not** edit the old procedure in place. Historical M28 support and M29 reliability evidence remain inspectable.

## Core authority rule

A reasoning model may:

- propose a bounded revision for an M29-suspended procedure;
- explain why the observed failure history motivates the change;
- in an isolated task, declare that an existing revision candidate materially informed the task.

The model may **not**:

- mark a revision trial successful;
- make an in-place task count as a revision validation;
- activate the replacement in project memory;
- promote the replacement;
- erase or mutate the parent procedure's historical support or reliability evidence.

Software-owned task verification supplies the trial verdict. Promotion is also software-owned.

## State and evidence

M30 stores two source-project-scoped artifacts beside M28/M29 memory:

```text
.harness-x/project-memory/
├── project-memory.json
├── project-memory-episodes.jsonl
├── procedure-reliability.json
├── procedure-reliability-usage.jsonl
├── procedure-revisions.json
└── procedure-revision-validations.jsonl
```

`procedure-revisions.json` is atomically replaced and fingerprinted. It records candidate lineage and lifecycle state.

`procedure-revision-validations.jsonl` is append-only, fsynced, and fingerprint-checked. Each durable row binds one revision candidate to one software-verified task episode and its final success/failure outcome.

## Candidate lineage

Each revision candidate records at least:

- `candidate_id`;
- parent M28 procedure ID;
- parent content fingerprint;
- revised statement and steps;
- task categories;
- explicit rationale;
- origin task episode;
- origin M29 reliability revision and suspension reason;
- successful and failed validation episode IDs;
- known failure modes;
- deterministic technical replacement-memory key;
- eventual replacement M28 entry ID if promoted.

The technical replacement key is:

```text
hx-revision/<candidate-id>
```

That entry is hidden from normal project-memory retrieval until M30 promotion succeeds.

## Admission

A candidate can be admitted only while its parent:

1. is an M28 `ACTIVE`, conflict-free procedure; and
2. is M29 `SUSPENDED`.

The revision must materially differ from the parent statement/steps. Identical reformulations are rejected.

The default policy permits at most four open candidates for one parent.

## Isolated validation

A revision proposal grants no reusable authority. Validation trials count only when the M30 context is running with:

```text
allow_revision_trials = true
```

The normal authority for that mode is the M24 isolated coding runtime. Browser revision trials use the isolated-browser composition.

`harness-x-code` defaults to isolation, so its normal M30 execution mode can validate a revision. `--in-place` keeps revision context/proposal support but leaves validation disabled.

During an isolated trial, the model can return:

```json
{
  "kind": "procedure_revision_update",
  "used_revision_candidate_ids": ["prev_..."]
}
```

Software validates that:

- the candidate exists and is still open;
- the parent is still suspended;
- the task is using an isolated revision-trial runtime.

Only after the final software-owned task verdict is known does M30 append validation evidence.

## Default lifecycle policy

Default M30 policy:

```text
successful validations required: 2
failed validations before reject: 2
maximum open candidates / parent: 4
```

Two distinct successful verification episodes move the revision candidate from `CANDIDATE` to `READY`.

Two failed validation episodes move it to `REJECTED`.

A `READY` candidate is still not reusable.

## Independent M28 support requirement

Every successful revision trial also contributes normal M28 support to the candidate's hidden technical replacement procedure.

Therefore the replacement must independently satisfy M28's existing evidence rule. With the current default M28 policy, two distinct successful episodes are required for that replacement to become `ACTIVE`.

Promotion requires both conditions at the same time:

```text
revision candidate == READY
AND
replacement M28 entry == ACTIVE + conflict-free
AND
parent M29 reliability == SUSPENDED
```

Only then can M30 software promote the candidate.

This intentionally prevents M30 from bypassing M28's project-memory qualification rules.

## Promotion and retrieval

After promotion:

- the original parent remains in M28 history;
- its M29 reliability state remains inspectable;
- the parent is hidden from current automatic procedure retrieval;
- the promoted replacement becomes visible through normal project-memory recall/context;
- sibling open revision candidates for the same parent become `SUPERSEDED`.

An unpromoted `hx-revision/*` M28 entry is never exposed as normal reusable guidance, even if partial validation has already written support for it.

If a promoted replacement later accumulates poor verified reuse outcomes, M29 can suspend that replacement too. M30 can therefore revise a revision; the mechanism is not limited to one generation.

## Parent recovery race

M29 can independently revalidate a suspended parent from fresh verified support.

If the parent becomes M29-eligible before an M30 replacement is promoted, M30 supersedes open repair candidates for that parent instead of allowing a stale experiment to replace a recovered procedure later.

## Crash behavior

Validation evidence is append-first:

1. append and fsync the fingerprinted validation row;
2. atomically replace the revision state.

If the process stops between those operations, reopening the M30 store reconciles `validation_total` against the durable ledger. Retrying the same `(candidate_id, episode_id)` applies the candidate lifecycle transition once without appending a duplicate row.

A conflicting retry outcome is rejected.

## Context bounds

M30 composes inside the existing bounded reasoning context rather than opening an unbounded history channel.

The full M28 + M29 + M30 context remains capped at 76,000 serialized characters. M30 reserves headroom from the M29 wrapper, then deterministically reduces open-candidate and promoted-lineage projections if pressure is high. As a final fallback the detailed M30 section can be reduced or omitted rather than violating the hard bound.

Unpromoted technical replacement entries remain hidden from M28 selected memory under context pressure.

## Runtime composition

M30 provides four runtime variants:

```text
ProcedureRevisionVerifiedRepositoryCodingTaskRuntime
ProcedureRevisionIsolatedRepositoryCodingTaskRuntime
ProcedureRevisionBrowserRepositoryCodingTaskRuntime
ProcedureRevisionBrowserIsolatedRepositoryCodingTaskRuntime
```

In-place variants default to `allow_revision_trials=False`.

Isolated variants create a fresh M24 task workspace but persist M28/M29/M30 evidence at the source project's shared memory root. The source checkout is not modified by revision validation trials.

Browser-isolated validation additionally requires both the code verification platform and browser verification platform to pass before the task supplies a successful revision-validation outcome.

## Report observability

M30 task reports expose:

- revision state path;
- validation ledger path;
- state fingerprint and revision;
- total validation count;
- open candidate count;
- ready candidate count;
- promoted candidate count;
- rejected candidate count;
- candidate IDs admitted during this task;
- candidate IDs validated during this task;
- candidate IDs promoted during this task.

M28 project-memory and M29 reliability report fields remain present as inherited evidence.

## Deterministic qualification coverage

The M30 test suite includes:

- candidate admission only for an M28-active/M29-suspended parent;
- rejection of revisions identical to the parent;
- two successful validations producing `READY` state;
- independent M28 replacement activation before promotion;
- two failed validations rejecting a candidate;
- promotion superseding sibling candidates;
- full runtime lifecycle across independent tasks: suspended parent -> proposal -> two fresh isolated verified trials -> replacement M28 activation -> promotion -> future retrieval exposes replacement and suppresses parent;
- source checkout unchanged by both isolated revision trials;
- browser-isolated validation and promotion with code + browser verification;
- CLI selection of M30 as the default coding runtime while preserving the in-place trial prohibition;
- append-before-state crash recovery with exactly-once validation application;
- context pressure with many long open revision candidates while maintaining the 76,000-character bound and keeping unpromoted replacements hidden.

## What M30 does not claim

M30 does not prove that the model-generated revision is globally optimal, nor does it establish perfect causal attribution between one procedure and one task outcome.

Its claim is narrower and operational:

> Harness X can turn repeated verified procedure failures into explicitly linked replacement candidates, test those candidates only in isolated verified task executions, require the replacement to independently earn normal project-memory support, and switch future retrieval to the replacement without destroying the historical evidence that produced either version.

That is a bounded failure-driven adaptation loop, not unconstrained self-rewriting.
