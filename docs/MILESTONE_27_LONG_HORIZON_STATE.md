# Milestone 27 — Durable Long-Horizon Task State

M27 addresses a different problem from increasing the model context window: important task information must remain available even after hundreds of reasoning turns, thousands of evidence records, or a process restart.

The raw trajectory is not the task state.

```text
raw tool / reasoning / verification history
                │
                ├── append-only bounded evidence ledger
                │
                ├── software-owned active evidence index
                │
                ├── durable obligations / decisions / strategy
                │
                └── exact workspace checkpoints
                             │
                             ↓
                 bounded model projection
                             │
                   ┌─────────┴─────────┐
                   │                   │
             current work       task_state_recall
                                  older evidence
```

M27 composes on the existing coding stack:

```text
M22 software-owned long-horizon controller
M23 repository intelligence / coding ACI
M24 isolated task workspaces
M25 typed code/process/file verification
M26 browser/application feedback
M27 durable task state + evidence recall + safe-point resume
```

## Why this is separate from model context

The M22 coding loop intentionally supplies only a bounded WorkingState projection to each reasoning turn. Increasing that projection indefinitely would eventually make the model input slower, less focused, and harder to reason over.

M27 therefore maintains a separate durable state whose size and authority do not depend on what the model happens to mention in its most recent turns.

The model sees only:

- the immutable task and acceptance requirements;
- the current bounded strategy;
- high-priority open obligations;
- active decisions;
- a small selected evidence index;
- counts/rollups and the latest checkpoint;
- an instruction to use `task_state_recall` when older evidence matters.

The full bounded evidence ledger stays on disk.

## Authority model

M27 separates immutable authority from advisory model-maintained state.

### Immutable

The model cannot rewrite:

```text
user task
verification-derived acceptance requirements
```

These values are fixed when a long-horizon session is initialized. A resume attempt must provide the same task and acceptance requirements.

### Advisory durable state

The model may propose updates to:

```text
current focus
next actions
known risks
open obligations
resolved/superseded obligations
architecture / implementation decisions
superseded decisions
optional checkpoint request
```

The proposal is validated by software. Invalid IDs or malformed updates are rejected and returned as an observation rather than silently mutating state.

### Software-owned evidence

Tool outcomes, verification results, controller interventions, fingerprints, and checkpoints are written by Harness X software rather than accepted from model claims.

## State-update protocol

M27 uses the existing reasoning `proposals` channel rather than introducing a memory-write tool turn.

A model may return one proposal such as:

```json
{
  "summary": "preserve the current implementation strategy",
  "payload": {
    "kind": "long_horizon_state_update",
    "strategy": {
      "current_focus": "finish authentication without changing the public session API",
      "next_actions": [
        "patch the login handler",
        "run the auth verification plan"
      ],
      "risks": [
        "existing session compatibility"
      ]
    },
    "add_obligations": [
      {
        "text": "Preserve the existing session API",
        "rationale": "downstream callers already depend on it",
        "priority": 0.95
      }
    ],
    "decisions": [
      {
        "statement": "Reuse the existing session abstraction",
        "rationale": "it is already the repository authority",
        "evidence_refs": [],
        "supersedes": []
      }
    ]
  }
}
```

That proposal can accompany a normal tool action in the same reasoning turn.

The state proposal itself does **not** count as implementation progress and consumes no extra tool action. When coding work remains, the model is instructed to pair state maintenance with a concrete coding action rather than spending turns only rewriting memory.

At most one long-horizon state update is accepted per reasoning turn.

## Durable obligations

Obligations receive stable IDs:

```text
obl_000001
obl_000002
...
```

An obligation has:

- text and rationale;
- priority;
- open/resolved/superseded status;
- created/updated state revisions;
- optional evidence references.

Open obligations are selected for model context by priority and recency. Resolved and superseded obligations remain in durable state rather than disappearing by omission.

The M22 root commitment remains separate software-owned control authority. M27 obligations are the richer durable working set used to preserve cross-turn requirements and implementation constraints.

## Durable decisions

Decisions receive stable IDs:

```text
decision_000001
decision_000002
...
```

They preserve implementation/architecture choices that would otherwise be easy to rediscover or accidentally contradict after large amounts of subsequent work.

A later decision can explicitly supersede earlier decisions. Superseded decisions remain present as historical state.

## Evidence ledger

Every durable evidence record contains:

```text
evidence_id
kind
summary
source_ref
importance
success / failure / unknown
created revision
bounded metadata
SHA-256 fingerprint
```

Evidence IDs are UUID-derived rather than sequential. This is important for crash consistency: Harness X appends and fsyncs an evidence record before replacing the atomic active-state file. If the process dies between those two operations, a later resume can reconcile the orphan record without risking evidence-ID reuse.

Typical evidence kinds include:

```text
tool:workspace_patch
tool:workspace_read
tool:process_run
tool:browser_snapshot
verification:baseline
verification:completion
control_intervention
```

For mutations, the ledger retains paths and available before/after hashes. Process evidence retains bounded command/output information. Browser evidence retains URL/title and explicit truncation state. Verification evidence links the current M25/M26 typed run fingerprints, failure signatures, and workspace-freshness evidence.

## Bounded active evidence index

The full evidence ledger is append-only, but the active state keeps at most 256 evidence records.

When that bound is exceeded, selection preserves a union of:

- high-importance evidence;
- recent evidence.

The model context is narrower still: by default M27 projects only 18 selected evidence summaries.

An old record can therefore be absent from both the active index and current model context while still remaining queryable in the durable ledger.

This is intentional:

```text
not currently selected != forgotten
```

## `task_state_recall`

M27 adds one read-only historical tool:

```text
task_state_recall
```

Input:

```json
{
  "query": "authentication failure",
  "kinds": ["tool:process_run"],
  "limit": 12
}
```

At least a query or evidence kind must be supplied. The result includes the current session/revision/fingerprint and matching durable evidence rows.

Recall is a read-only task-state operation. It uses the existing `workspace.read` permission and has no state/tool side effect.

M27 deliberately does not expose a generic model-controlled memory-write tool.

## Automatic evidence capture

The model does not need to remember routine facts manually.

M27 hooks the existing coding runtime boundaries:

```text
model tool result
    ↓
WorkingState (existing M21/M22 behavior)
    ↓
M27 durable evidence

software verification boundary
    ↓
WorkingState verification snapshot
    ↓
M27 typed verification evidence
    ↓
exact safe-point checkpoint

M22 controller intervention
    ↓
WorkingState intervention
    ↓
M27 durable intervention evidence
```

This makes the durable state useful even when the model never emits a state-update proposal.

## Fingerprinted active state

`long-horizon-state.json` is an atomic, fingerprinted state document.

The state fingerprint covers the complete state except the fingerprint field itself. Loading verifies the stored fingerprint before the state can be used.

Changing task text, obligations, decisions, evidence references, counters, resume status, or checkpoint data without recomputing the authoritative state is therefore detected as tampering/corruption.

Resume does not use Pydantic `model_copy()` to mark a state as resumed because that would bypass fingerprint derivation. M27 reconstructs the state through full validation and derives a new fingerprint/revision.

## Exact workspace checkpoints

A checkpoint contains:

```text
checkpoint ID
checkpoint revision
reason
parent-state fingerprint
exact source-relevant workspace fingerprint
workspace path
current durable evidence count
```

The checkpoint's `parent_state_fingerprint` intentionally identifies the state immediately before the checkpoint object is inserted. The checkpoint snapshot file then contains the newly fingerprinted state including that checkpoint. This avoids an impossible self-referential fingerprint.

Workspace fingerprints hash source-relevant file paths, contents, sizes, and symlink targets while excluding generated/dependency/cache directories such as `.git`, `.venv`, `node_modules`, `.next`, `dist`, and build caches.

A default verification workspace is limited to 20,000 source-relevant files so an apparently exact checkpoint cannot silently become an unbounded partial scan.

## Safe-point resume

M27 resume is **safe-point task-state resume**, not instruction-pointer restoration.

A normal run creates checkpoints at:

```text
run start / resume
software-owned verification boundaries
model-requested checkpoint boundaries
run complete / failed
runtime exception (best effort)
```

On resume, Harness X verifies:

1. the long-horizon state fingerprint;
2. the supplied task is identical;
3. acceptance requirements are identical;
4. by default, the current workspace fingerprint exactly matches the latest checkpoint;
5. the append-only evidence ledger is consistent with the active state's evidence count.

If an evidence record was fsynced but the process crashed before the atomic state replacement, initialization reconciles that orphan into the active state.

### What is resumed

M27 preserves:

- session identity;
- strategy;
- obligations and statuses;
- decisions and supersession history;
- durable evidence and rollups;
- checkpoint history.

A new M22 controller instance is created for the new process. Its per-process reasoning/tool counters restart. M27 does not pretend to resume the Python instruction pointer or an interrupted tool call.

## Resume CLI

Normal new tasks remain isolated by default:

```powershell
harness-x-code D:\projects\my-repo `
  --task "Implement the requested feature" `
  --verification-plan .\verification.json `
  --output .harness-x\coding-run
```

To resume a retained/checkpointed workspace, point `workspace` at that retained task workspace and explicitly use in-place mode:

```powershell
harness-x-code D:\retained\harness-task-workspace `
  --in-place `
  --task "Implement the requested feature" `
  --verification-plan .\verification.json `
  --resume-long-horizon-state D:\previous-run\long-horizon\long-horizon-state.json `
  --output D:\previous-run\resume-2
```

Resume currently requires `--in-place`. A fresh M24 isolated clone cannot be assumed to contain unexported edits from the previous retained task workspace.

### Explicit workspace-drift escape hatch

By default any mismatch from the latest exact checkpoint rejects resume.

An operator who understands an intentional external workspace change may explicitly select:

```text
--resume-allow-workspace-drift
```

This disables only the checkpoint workspace-equality requirement. State fingerprint, task identity, acceptance identity, and evidence-ledger validation still apply.

The flag is deliberately separate and cannot be used without a resume state.

## Browser composition

M27 wraps both non-browser and M26 browser-aware runtimes.

For a browser task:

```text
model edit / browser inspection
        ↓
M25 code verification
        ↓
M26 independent browser verification
        ↓
M25 freshness re-check
        ↓
M27 durable verification evidence
        ↓
M27 exact safe-point checkpoint
```

Browser evidence does not become stronger merely because it is remembered. M26 remains the application verification authority; M27 records its typed result and identity.

## Isolation composition

New tasks use M24 isolation by default. The long-horizon state is stored in the operator output directory while the checkpoint fingerprint refers to the actual isolated task workspace.

At normal task finalization M24 still controls retention/export behavior and preserves the source checkout.

If an isolated workspace is deleted by retention policy, its M27 state remains useful as task history but cannot satisfy exact workspace resume. For resumable long tasks, retain the task workspace.

## Context bound

The long-horizon wrapper has a separate maximum context envelope of 68,000 characters after earlier repository/verification/browser context enrichment.

If the durable projection would exceed that envelope, M27 deterministically reduces:

1. selected evidence;
2. open obligation count;
3. active decision count;
4. obligation/decision detail;
5. finally, the long-horizon section to session/fingerprint/count/recall metadata.

It does not grow the model context in proportion to the evidence ledger.

The governing task still exists in the base reasoning goal even if the M27 section itself reaches its minimal projection.

## Artifacts

A typical M27 output adds:

```text
coding-task-report.json
long-horizon/
    long-horizon-state.json
    long-horizon-evidence.jsonl
    long-horizon-checkpoints/
        checkpoint_00001.json
        checkpoint_00002.json
        ...
```

When `--resume-long-horizon-state` points to a previous state file, that state file and its sibling evidence/checkpoint artifacts remain the authoritative durable session store. The new run report records their absolute paths.

M25/M26 verification artifacts and M24 isolation artifacts continue to be written as before.

## Report fields

M27 reports expose:

```text
long_horizon_session_id
long_horizon_state_path
long_horizon_evidence_path
long_horizon_state_fingerprint
long_horizon_state_revision
long_horizon_checkpoint_count
long_horizon_resumed
```

These make a coding run externally inspectable without embedding the entire durable state inside the normal task report.

## Failure and corruption rules

M27 fails closed for state authority:

- fingerprint mismatch → resume rejected;
- changed task → resume rejected;
- changed acceptance requirements → resume rejected;
- exact checkpoint workspace mismatch → resume rejected unless the operator explicitly allows drift;
- evidence ledger shorter than the authoritative state count → resume rejected;
- duplicate evidence IDs → resume rejected;
- invalid evidence record → resume rejected;
- unknown obligation/decision ID in a model update → that advisory update is rejected.

A malformed advisory model update does not invalidate an otherwise-valid coding action from the same reasoning turn.

## What M27 does not claim

M27 is not infinite memory and is not a global lifetime knowledge base.

It does not yet provide:

- semantic-vector retrieval across years of unrelated projects;
- automatic cross-project procedural skill promotion;
- restored M22 instruction pointer / exact in-flight tool continuation;
- automatic reconstruction of a deleted isolated workspace;
- distributed multi-agent shared project state;
- replacement for source control, tests, browser verification, or the M22 control plane.

Its job is narrower and foundational: make one large autonomous task retain the information needed to remain coherent as the raw interaction history becomes too large to carry in model context.

A subsequent product milestone can build project/lifetime memory and learned procedural reuse on top of this durable task-state substrate rather than mixing those concerns into the first long-horizon implementation.

## Qualification target

Freeze M27 only after exact-head CI demonstrates:

- existing M21–M26 behavior remains green;
- immutable task/acceptance cannot be rewritten by model proposals;
- state tampering is detected;
- append-first crash orphan evidence is reconciled;
- active evidence remains bounded while old evicted evidence is recallable;
- model state update + coding action works in one reasoning turn;
- automatic tool/verification/controller evidence is persisted;
- browser verification composes with M27;
- M24 isolation still preserves the source checkout;
- a second runtime process resumes the same durable session at an exact checkpoint;
- large durable state remains inside the bounded model-context envelope;
- CLI resume/drift constraints are enforced;
- `harness-x --help` and config validation remain green.
