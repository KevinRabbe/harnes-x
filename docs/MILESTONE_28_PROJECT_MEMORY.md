# Milestone 28 — Project Memory and Procedural Reuse

M28 adds a durable project-scoped memory layer above M27 long-horizon task state.

M27 answers:

> What must this one long-running task remember across many reasoning turns and process restarts?

M28 answers:

> What verified knowledge from previous tasks is safe and useful enough to reuse on later tasks in the same project?

The two layers are deliberately separate. A current-task obligation is not automatically a project convention, and a model-written summary is not automatically reusable knowledge.

## Architecture

```text
independent coding task
        |
        v
M27 durable task state
        |
        +--> tools / edits / code verification / browser verification
        |
        v
software-owned final task verdict
        |
        +------------------------------+
        |                              |
      failed                         passed
        |                              |
        v                              v
record failed episode          record verified-success episode
reject staged candidates       admit staged project candidates
        |                              |
        +---------------+--------------+
                        |
                        v
               M28 ProjectMemoryStore
                        |
             +----------+-----------+
             |                      |
          candidate               active
        one support          >=2 distinct verified
                              successful supports
             |                      |
             +----------+-----------+
                        |
                 conflict check
                        |
          incompatible same kind/key?
                 /             \
               yes             no
                |               |
           CONFLICTED       reusable memory
                |               |
                +-------+-------+
                        |
                 bounded retrieval
                        |
            ProjectMemoryContextReasoningCore
                        |
                 reasoning model
                        |
            project_memory_recall
```

M28 wraps M27; it does not replace it. The normal coding authority chain remains software-owned verification and controller state. Project memory is advisory input to future reasoning.

## Persistent artifacts

The default in-place project memory root is:

```text
<workspace>/.harness-x/project-memory/
```

For M24 isolated runs the default is source-scoped rather than clone-scoped:

```text
<source-workspace>/.harness-x/project-memory/
```

so independent isolated task workspaces reuse one project knowledge base.

The directory contains:

```text
project-memory.json
project-memory-episodes.jsonl
```

`project-memory.json` is an atomically replaced, fingerprinted active state. `project-memory-episodes.jsonl` is an append-only ledger of completed coding-task episodes.

## Project identity

By default, project identity is derived from the resolved source workspace path.

The CLI can override both storage and logical identity:

```text
--project-memory-root <path>
--project-memory-key <stable-logical-project-id>
```

`--project-memory-key` is useful when a checkout is intentionally moved but should retain the same project memory. A memory root already initialized for a different project key is rejected.

## Task episodes

Every normal M28 task completion records a `ProjectMemoryTaskEpisode`, whether the task passed or failed.

An episode carries bounded references to:

- the task;
- software-owned success/failure;
- coding report identity;
- M27 long-horizon session/state fingerprint;
- the latest exact M27 workspace checkpoint when available;
- changed files from the coding controller;
- typed code-verification run fingerprints;
- browser-verification run fingerprints when browser mode is active.

The episode ledger is append-first and fsynced before the active project state is replaced. If the process crashes after the episode append but before the state replacement, startup conservatively reconciles the monotonic episode count. It does **not** reconstruct or promote a lost model candidate from an orphaned episode.

That is deliberate fail-safe behavior: forgetting a candidate is preferable to inventing reusable project truth after an incomplete closeout.

## Model proposal boundary

The model cannot directly write project memory. It may emit one advisory proposal per reasoning turn:

```json
{
  "kind": "project_memory_update",
  "candidates": [
    {
      "kind": "procedure",
      "key": "python-test-convention",
      "statement": "For small Python changes, run the targeted test before the full suite",
      "steps": [
        "Run the targeted pytest file",
        "Run the full pytest suite before completion"
      ],
      "task_categories": ["python", "testing"]
    },
    {
      "kind": "fact",
      "key": "config-location",
      "statement": "Project configuration lives under configs/",
      "task_categories": ["configuration"]
    }
  ],
  "used_procedure_ids": ["pmem_..."]
}
```

The proposal is consumed by `ProjectMemoryContextReasoningCore` and staged in process. It does not itself mutate the disk store and does not count as coding progress. The model should combine a memory proposal with a concrete coding action when work remains.

Only after the underlying M27/M25/M26 runtime has produced its authoritative final report does M28 close out project memory.

## Promotion rule

A successful coding task can support a project-memory candidate.

A candidate becomes automatically reusable only after **at least two distinct software-verified successful task episodes** support the same candidate identity.

```text
support #1 from verified task A -> CANDIDATE
support #2 from verified task B -> ACTIVE
```

Calling support twice with the same episode ID does not increase the support count twice.

Failed tasks are still useful episodes for history and procedure-usage failure accounting, but they cannot support or promote new project-memory candidates.

### Candidate identity

The identity fingerprint includes:

- entry kind (`fact` or `procedure`);
- stable project-memory key;
- statement;
- procedure steps when applicable.

Presentation-only case and whitespace differences are normalized for identity.

`task_categories` are retrieval metadata, not semantic identity. Different verified tasks can therefore attach additional categories to the same procedure without falsely creating contradictory variants.

This is intentionally still conservative. M28 does not use a model to decide that substantially different natural-language statements are semantically equivalent.

## Conflict suspension

If verified task evidence produces incompatible content under the same `kind + key`, M28 does not use last-write-wins.

Instead all non-invalidated variants for that key become:

```text
CONFLICTED
```

and are excluded from automatic active retrieval.

```text
active variant A
      +
verified incompatible variant B
      |
      v
A -> CONFLICTED
B -> CONFLICTED
      |
      v
not injected as reusable guidance
```

M28 does not yet implement automatic conflict resolution. This is deliberate. Later evidence, operator review, or a future bounded resolution mechanism can decide what to do; the current system fails closed rather than silently picking a winner.

## Procedure reuse and feedback

Only `ACTIVE`, conflict-free procedure IDs may be declared as used.

A task may emit:

```json
{
  "kind": "project_memory_update",
  "used_procedure_ids": ["pmem_..."]
}
```

M28 records outcome statistics after the task:

- `usage_count`;
- `success_count`;
- `failure_count`;
- bounded known failure modes.

Usage is recorded before newly learned candidates are admitted at closeout. Therefore if a task validly used an active procedure and also discovers evidence that conflicts with it, the historical use/outcome is retained before the procedure becomes suspended.

M28 does **not** automatically invalidate a procedure after one failed reuse. Failure history is evidence for later controller/improvement logic, not sufficient on its own to prove that the procedure is globally wrong.

Usage attribution currently depends on an explicit model declaration. Software validates that the referenced procedure is active and conflict-free at proposal time, but it cannot prove from first principles that the model psychologically "used" the procedure. Treat usage statistics as grounded task-outcome metadata with an explicit attribution boundary, not as perfect causal attribution.

## Retrieval

M28 injects a small, query-relevant set of active entries automatically into a `project_memory` context section.

Retrieval is bounded lexical overlap over:

- key;
- statement;
- steps;
- task categories;
- known failure modes.

The model also receives a read-only tool:

```text
project_memory_recall
```

Inputs:

```json
{
  "query": "python test convention",
  "kinds": ["procedure"],
  "include_candidates": false,
  "limit": 12
}
```

By default recall returns only active, conflict-free entries. `include_candidates=true` is available for diagnosis; it does not make those entries reusable authority.

The tool uses `workspace.read`, has no side effects, and does not mutate support or usage counts.

## Bounded model context

Project memory does not grow the reasoning prompt linearly with project history.

`ProjectMemoryContextReasoningCore` has an explicit total wrapper bound (76,000 characters by default). It initially selects at most 12 relevant active memories, then deterministically reduces the projection if the combined context is too large:

1. reduce selected memories;
2. compact selected statements/steps;
3. fall back to project identity/counts plus `project_memory_recall`.

The full persistent store remains outside the prompt.

This is the M28 equivalent of the M27 design rule:

```text
more accumulated experience
!=
more raw history in every reasoning turn
```

Instead:

```text
more accumulated experience
-> structured external project memory
-> evidence-gated activation
-> bounded selection / recall
-> fixed-size reasoning envelope
```

## M24 isolation

For isolated runs, M28 project memory belongs to the source project, while code changes belong to the task clone.

```text
source project
  |-- source code ------------------------ read/snapshot only for task
  `-- .harness-x/project-memory ---------- controller-owned persistent metadata
             ^
             |
      isolated task A
      isolated task B
      isolated task C
```

The model never receives a raw filesystem handle to this memory. It sees bounded context and can call the read-only recall tool.

Creating/updating `.harness-x/project-memory` is intentional Harness X metadata persistence; M24 still keeps model-selected code edits away from the operator source checkout and exports task deltas through the existing isolation result.

## M26 browser composition

Browser/application verification remains independent completion authority.

The closeout order is:

```text
model work
  -> M25 code verification
  -> M26 browser verification (when configured)
  -> M27 exact long-horizon checkpoint/report
  -> M28 task episode + usage feedback + successful candidate admission
```

A candidate is therefore never promoted merely because the model believed the UI worked.

## CLI

Normal use requires no extra project-memory flag; M28 is the default `harness-x-code` runtime.

Example:

```powershell
harness-x-code D:\projects\site `
  --task "Fix the dashboard filters" `
  --verification-plan .\verification.json `
  --output D:\runs\dashboard-filter-fix
```

Explicit persistent identity/storage:

```powershell
harness-x-code D:\projects\site `
  --task "Fix the dashboard filters" `
  --verification-plan .\verification.json `
  --project-memory-root D:\harness-memory\site `
  --project-memory-key site-main `
  --output D:\runs\dashboard-filter-fix
```

M27 resume flags continue to work for in-place retained task workspaces. Project memory and task resume are orthogonal: M27 resumes one interrupted task; M28 is automatically available to new independent tasks in the same project.

## Report fields

M28 report schemas add:

- `project_memory_project_id`;
- `project_memory_root`;
- `project_memory_state_path`;
- `project_memory_state_fingerprint`;
- `project_memory_state_revision`;
- `project_memory_episode_id`;
- active/candidate/conflicted entry counts;
- entry IDs admitted from the just-completed successful task.

These fields make reuse and learning state externally inspectable without asking the model what it remembers.

## Safety and authority boundary

M28 does not change the fundamental Harness X authority model:

- model output remains untrusted proposal data;
- the model cannot rewrite task verification outcomes;
- failed tasks cannot promote new memory;
- one successful task cannot activate new memory;
- conflict uses suspension rather than overwrite;
- memory recall is read-only;
- project memory cannot directly invoke tools;
- browser state remains evidence rather than authority;
- M24 host process/browser execution still is not an OS/container security sandbox.

## Current limitations

M28 intentionally does not solve every form of lifelong learning.

- Natural-language candidate equivalence is conservative; meaningfully rephrased but equivalent candidates can conflict rather than merge.
- Repeated verified task success is evidence that a memory is useful in those tasks, not proof of universal truth.
- Conflict resolution is not automatic yet.
- Procedure usage attribution depends on explicit model declaration, although the referenced ID and task outcome are software-validated.
- Usage failures are recorded but do not yet drive automatic invalidation or strategy changes.
- Project memory is local file-backed state; distributed/multi-writer coordination is outside M28.
- M28 does not train model weights from project memory.

Those limitations are preferable to silently granting the model self-authored persistent authority.

## M28 acceptance target

M28 is qualified when deterministic tests demonstrate all of the following:

1. a first verified successful task creates only a candidate;
2. a second independent verified successful task can activate identical reusable content;
3. a failed task cannot support a new candidate;
4. the same episode cannot count twice;
5. contradictory verified variants suspend reuse;
6. a later independent task receives and can explicitly use an active procedure;
7. successful/failed procedure reuse is persisted;
8. large project memory remains outside a bounded reasoning envelope;
9. M24 isolation keeps model-selected code edits out of source while sharing source-scoped project memory;
10. M26 browser verification still gates browser-aware task success;
11. state fingerprint/identity checks reject corrupted or mismatched stores;
12. exact-head CI remains green.
