# Milestone 31 — Bounded Procedure-Improvement Campaigns

M31 adds a persistent orchestration layer around the verified procedure-learning stack built in
M28–M30. It does not introduce a new promotion authority. Its job is narrower: when M29 has
suspended a degraded procedure, M31 can spend a bounded number of isolated coding tasks to ask
for replacement candidates and validate them through the existing M30 machinery.

The governing invariant is:

> Campaign orchestration may choose the next experiment, but it cannot decide that an experiment
> succeeded or that a procedure is safe to reuse.

M29 remains the reliability authority. M30 remains the revision-validation and promotion
authority. M25/M26 remain the software verification authorities for code/application outcomes.

## Control flow

```text
M28 historically active procedure
        |
        v
verified reuse failures
        |
        v
M29 SUSPENDED
        |
        v
M31 bounded campaign
        |
        +-- proposal task (isolated, M30 trial authority disabled)
        |       |
        |       v
        |    M30 candidate
        |       |
        +-------+
                |
                v
        validation task (isolated, M30 trial authority enabled)
                |
         software verification
          /             \
       fail             pass
        |                 |
        v                 v
 M30 failure support   M30 success support
        |                 |
 rejected after          second distinct success
 policy threshold              |
        |                       v
        |                 candidate READY
        |                       +
        |                 M28 replacement ACTIVE
        |                       |
        +------> next           v
          candidate       M30 PROMOTED
          if budget             |
          remains               v
                          M31 PROMOTED
```

## Explicit resource budget

The default campaign policy is deliberately small:

- at most **3 candidate-proposal tasks**;
- at most **6 isolated validation tasks**.

The operator may lower or raise these within the schema bounds using:

```text
--max-candidate-proposals N
--max-trial-tasks N
```

A proposal or trial unit is consumed before the task is launched. This ordering is intentional.
An ambiguous crash therefore cannot create an unaccounted autonomous retry.

The campaign has explicit terminal states:

- `promoted` — M30 promoted a verified replacement;
- `exhausted` — the configured proposal/trial budget was consumed without promotion;
- `superseded` — the original parent recovered through M29 or another valid M30 transition made
  the campaign obsolete;
- `cancelled` — software/operator cancellation;
- `active` — more bounded work is permitted.

A terminal campaign for the same exact M29 suspension revision is not silently reopened.

## Persistent campaign state

M31 stores project-scoped orchestration state in:

```text
.harness-x/project-memory/procedure-improvement-campaigns.json
```

The state is fingerprinted and replaced atomically. It records:

- campaign ID;
- parent procedure ID and content fingerprint;
- exact M29 reliability revision that opened the campaign;
- suspension reason;
- proposal/trial budgets and counters;
- candidate IDs owned by the campaign;
- a single persistent pending step;
- terminal status/reason;
- promoted candidate ID when applicable.

M31 does not duplicate M28 memory, M29 reliability evidence, or M30 validation evidence. Those
stores remain the authoritative histories for their respective claims.

## Crash ordering and `pending_step`

Before launching an autonomous proposal/trial task M31 atomically records a `pending_step` with:

- step ID and kind;
- candidate ID for a trial;
- M30 revision-state revision before launch;
- M30 validation count before launch;
- candidate IDs or success/failure counts before launch;
- start time.

The proposal/trial counter is incremented in the same transition.

If the process crashes, restart does **not** automatically replay the pending experiment. M31
reopens the durable M28/M29/M30 stores, reconciles any M30 evidence that reached disk, clears the
pending marker, and continues from the remaining budget.

This is intentionally conservative:

```text
ambiguous crash
    -> may consume one campaign budget unit
    -> never grants a free duplicate autonomous experiment
```

### READY → PROMOTED crash window

M30 already owns startup reconciliation for a candidate that has reached both:

```text
M30 candidate READY
M28 hidden replacement ACTIVE
```

but crashed before the final M30 `PROMOTED` state write.

M31 invokes that existing M30 startup reconciliation **before choosing another campaign step**.
It does not reimplement the promotion test. Therefore restart can terminate the campaign from
M30's repaired promotion without spending another proposal/trial budget unit or calling the model.

## Candidate proposal authority

Proposal tasks run in isolated workspaces with:

```text
allow_revision_trials = false
```

The model may propose at most the bounded M30 `procedure_revision_update` candidate shape. It
cannot validate the candidate by proposing it, and coding changes made in the proposal workspace
are experimental only.

M30 still requires the parent to be:

- an M28 active/conflict-free procedure;
- M29 suspended;
- not already replaced by a promoted revision;
- materially different from the candidate.

## Validation authority

Validation tasks run in new isolated task workspaces with:

```text
allow_revision_trials = true
```

The model may declare that one existing M30 candidate materially informed the task. This does not
make the trial successful. The final M25/M26 software-owned verification result determines the
M30 validation outcome.

The source checkout is never the revision experiment workspace.

M30's normal promotion rule remains unchanged:

1. distinct verified successful validation episodes satisfy the M30 success threshold;
2. those successes independently support the hidden replacement procedure in M28;
3. that replacement becomes M28 `ACTIVE` and conflict-free;
4. the original parent is still M29 `SUSPENDED`;
5. M30 software promotes the replacement.

M31 merely observes the resulting M30 state and terminates its campaign.

## Failed candidate fallback

A rejected candidate does not automatically end a campaign. If proposal and trial budget remain,
M31 may request a different bounded candidate for the same suspension event.

Deterministic coverage exercises:

```text
candidate A
    -> failed verified trial
    -> failed verified trial
    -> M30 REJECTED
candidate B
    -> verified success
    -> verified success
    -> M30 PROMOTED
campaign
    -> PROMOTED
```

This is bounded search, not open-ended self-modification.

## Browser/application campaigns

`ProcedureImprovementBrowserCampaignRunner` uses the same campaign state machine but executes each
experiment with the M30 isolated browser composition.

A successful browser-backed trial therefore requires:

```text
isolated workspace
    + M25 code verification
    + M26 browser/application verification
    + post-browser M25 freshness
    + M30 validation/promotion rules
```

M31 cannot turn a browser observation, screenshot, model completion, or application startup into a
successful validation by itself.

The normal Playwright installation/containment requirements from M26 still apply. Standard CI
continues to use the deterministic fake browser provider and does not download Chromium.

## Model context

M31 does not dump persistent campaign state into every reasoning turn.

Each proposal/trial is an ordinary bounded M30 coding task with a concise software-generated
instruction identifying:

- the suspended parent;
- its current statement/steps;
- the verified suspension reason;
- the candidate under trial when applicable;
- the concrete validation task;
- the M30 proposal/trial protocol.

The underlying M30 reasoning context retains its existing hard **76,000-character** composed
bound. Campaign history remains external software state rather than accumulated chat context.

## Operator command

M31 is deliberately **not** triggered implicitly by normal `harness-x-code` execution.

The operator runs a campaign explicitly:

```powershell
harness-x-improve-procedure D:\repo `
  --parent-procedure-id pmem_... `
  --task "Reproduce and repair the failure this procedure should handle" `
  --verification-plan D:\plans\verification.json `
  --project-memory-root D:\repo\.harness-x\project-memory `
  --output D:\runs\procedure-improvement
```

Repeatable required verification commands may be used instead of, or together with, a plan:

```powershell
harness-x-improve-procedure D:\repo `
  --parent-procedure-id pmem_... `
  --task "Repair the regression" `
  --verify "python -m pytest tests/test_target.py" `
  --verify "python -m pytest"
```

Browser-backed campaigns additionally take the M26 inputs:

```powershell
harness-x-improve-procedure D:\repo `
  --parent-procedure-id pmem_... `
  --task "Repair the UI workflow" `
  --verification-plan D:\plans\code-verification.json `
  --application-spec D:\plans\application.json `
  --browser-verification-plan D:\plans\browser-verification.json
```

The campaign command supports the same local Transformers or OpenAI-compatible reasoning-core
construction used by the coding CLI. Providing an API key or remote endpoint changes reasoning
transport, not Harness X authority.

## Artifacts

Top-level campaign output includes:

```text
procedure-improvement-campaign-report.json
```

Each launched experiment has a deterministic directory under:

```text
campaigns/<campaign-id>/step-NNN-<kind>[-candidate-id]/
```

and contains the normal M30 task/isolation/verification artifacts for that experiment.

The source project memory root contains the durable M28/M29/M30/M31 ledgers and state.

## Deliberate limitations

M31 is not a general self-modification scheduler.

It currently does **not**:

- invent its own validation task;
- change verification plans during a campaign;
- expand its own proposal/trial budget;
- automatically start from ordinary coding runs;
- train or activate model adapters;
- mutate source code outside isolated experiment workspaces;
- resolve semantic conflicts in M28;
- override M29 reliability;
- bypass M30 candidate policy or promotion;
- guarantee that a verification plan captures every real-world regression.

The operator remains responsible for choosing a meaningful suspended parent and a validation task
whose software verification is strong enough to justify procedure learning.

## Acceptance boundary

M31 is qualified only when the exact branch head passes the complete repository test suite plus
CLI/config smoke checks, including deterministic coverage for:

- autonomous proposal → validation → M30 promotion;
- proposal budget exhaustion;
- failed candidate → bounded next candidate;
- parent recovery supersession;
- pending-step restart without replay;
- M30 READY→PROMOTED crash reconciliation before new campaign work;
- browser-isolated validation/promotion;
- explicit operator CLI construction and budget propagation;
- source-checkout preservation.

The exact frozen SHA and final CI run are recorded in PR #38 after qualification.
