# Milestone 75 — Everyday Reliability

## Status

Implementation milestone stacked exactly on frozen M74
`165399c224782ad526489badb18ff0d0187b0e3f`.

This document is the first M75 branch commit and defines the milestone boundary before implementation.

## Objective

Make the ordinary Project + Chat workflow resilient enough for daily local use: recover after browser reload,
App Server restart, desktop restart, and bounded transport interruption; reconcile interrupted work visibly;
and provide explicit stop/retry/continue controls without creating a second task, lifecycle, verification,
approval, or evidence authority.

M75 composes the existing durable Product/Conversation/App Server state and the already-qualified M37/M46/
M48/M49 recovery primitives. Recovery is reconstruction from authoritative server records plus explicit user
actions. Browser or desktop convenience state must never become execution truth.

## Required behavior

M75 should:

1. restore the last valid non-archived Project/Chat after successful everyday unlock using the existing durable
   product restoration state, without persisting chat content, execution state, or credentials as browser
   authority;
2. reconstruct the latest conversation execution for the restored/selected chat from authoritative M69–M74
   projections and resume read-only work-activity observation from server-derived state;
3. distinguish ordinary terminal failure from a session that was interrupted by App Server restart, and present
   that interruption explicitly rather than implying the prior runtime continued;
4. add explicit user-controlled `Stop`, `Retry`, and `Continue` actions with deterministic ownership and
   idempotency rules;
5. bound automatic everyday activity reconnect/poll retry and expose an explicit reconnect/refresh action after
   exhaustion instead of retrying forever;
6. preserve exact submission identity across ambiguous transport failure while a submission result is unknown,
   so a retry cannot duplicate one accepted work turn;
7. clear stale retry/recovery state on project/chat change, lock, fresh bootstrap, authoritative terminal
   reconciliation, and identity mismatch;
8. provide practical keyboard shortcuts only for existing visible everyday actions, with focus/form guards so a
   shortcut cannot accidentally approve, cancel, submit, or repeat sensitive work;
9. persist only non-sensitive desktop window geometry/state in the native shell, validate it against usable
   screens and minimum bounds, and fall back safely when stale/corrupt/off-screen;
10. make startup, reload, reconnect, empty, interrupted, terminal-failure, and unavailable-server states
    fail-visible with safe DOM text and explicit user actions; and
11. keep all restart/reconnect/reconciliation behavior deterministic and covered by focused backend, browser,
    and Windows desktop tests before full inherited qualification.

## Recovery model

### Durable execution facts

M69 already commits immutable conversation plans before crossing into chat/session state, binds the durable user
message/session/result records append-only, and reconciles crash windows from deterministic anchors. M75 does
not replace that protocol.

The App Server already performs restart recovery:

- durable `created` sessions may be revalidated and re-enqueued;
- pre-restart `running` or `cancel_requested` sessions become durable `failed` sessions with
  `app_server_restart_interrupted_running_session`;
- instruction/runtime state is not pretended to resume after process death.

M75 should project these facts into the everyday UI and provide explicit next actions.

### Everyday observation reconnect

The current everyday execution bridge polls bounded activity pages but retries transport failures indefinitely.
M75 should use a small deterministic bounded policy analogous to, but separate from, the already-qualified M37
operator SSE schedule. The everyday client may resume only from authoritative execution/activity identity it has
already validated. After bounded automatic retries are exhausted, it must stop automatic work and expose an
explicit `Reconnect activity`/refresh action.

A successful authoritative activity response resets the consecutive transport-failure count. HTTP/auth/shape,
ownership, corruption, or archived-resource failures are non-retriable and remain visible.

No browser-owned event ledger, synthetic task state, or persisted activity cursor is introduced.

## Stop / Retry / Continue semantics

### Stop

`Stop` is available only for the currently selected non-terminal conversation execution with a bound App Session.
It delegates to the existing App Server cancellation authority for that exact owned session. It is a cancellation
request, not a hard process kill. Existing App Server semantics remain authoritative: queued work may become
`cancelled`; already-running work may finish before the cancellation request can take effect.

The everyday browser must not accept or submit a raw caller-selected App Session ID as authority. Any M75 product
route must resolve the execution -> session binding server-side and revalidate project/chat/execution ownership.
Repeated stop requests must be deterministic/idempotent with the inherited terminal-state rules.

### Retry

`Retry` is an explicit new work attempt after a terminal failed/cancelled/interrupted execution. It must create a
new conversation execution identity and must not mutate or reopen the prior immutable M69–M74 plan, messages,
resource snapshot, approval record, settings snapshot, trace, report, or evidence.

The retry request should be reconstructed server-side from the prior accepted execution's immutable user task and
eligible frozen configuration/resource inputs rather than trusting hidden browser-cached request material. Any
setting/resource that cannot be safely reused under its existing immutable contract must fail closed instead of
being silently approximated.

### Continue

`Continue` is distinct from replaying an interrupted runtime. It creates a new explicit conversation turn after a
terminal execution, grounded by the normal M71 context lineage. The user remains the author of the new text; M75
must not synthesize a continuation instruction or resurrect pre-crash model/tool state.

For an interrupted execution, the UI may make `Continue` prominent, but it still means “start a new turn from the
current durable project/chat state,” not “resume the old process from its instruction pointer.”

## Ambiguous submission recovery

The existing M69 submission ID is already idempotent. M75 should preserve that property across ordinary network
loss:

- while a POST outcome is unknown, keep the exact pending submission identity in page memory;
- retry only the exact same project/chat/text/resource request under that identity;
- if authoritative execution listing shows that submission already exists, bind the UI to that execution instead
  of issuing a second work turn;
- any change to project, chat, text, selected resources, or frozen request material invalidates that pending retry
  identity;
- no automatic retry is allowed for a different mutation or for sensitive approval actions.

M75 does not persist arbitrary composer text or request bodies across process/browser restart.

## Last Project/Chat restoration

M66–M68 already persist durable `last_opened_project_id` and per-project `last_opened_chat_id`. M75 treats those
server records as the restoration authority.

After successful authenticated workspace load:

- restore only identifiers still present, owned, and non-archived;
- fall back deterministically to another active Project/Chat or an empty state;
- do not use browser storage as a competing project/chat restoration source;
- restored selection must run the normal M68/M69/M74 selection chain so execution, approval, settings, resources,
  diffs, and artifacts are rebuilt from current authenticated APIs.

A fresh bootstrap or explicit lock may clear transient browser recovery state without deleting the durable product
last-opened records.

## Keyboard shortcut boundary

M75 may add only practical shortcuts for ordinary visible product actions, for example:

- focus composer;
- send the current composer through the existing form submission path;
- create chat;
- switch between Project/Chat navigation regions;
- explicit refresh/reconnect where the corresponding visible action is enabled.

Shortcuts must ignore modified/IME/composition cases and respect focused editable controls. No shortcut may bypass
button disabled state, confirmation/approval UI, M72 sensitive-action approval, or any server authority. `Stop`
must never be bound to a broad accidental keystroke such as plain Escape while text editing.

## Native desktop reliability

The Windows shell may persist a small versioned local window-state record containing only non-sensitive UI
geometry/state such as restored bounds and maximized state.

Requirements:

- no bearer, reload capability, Project/Chat/execution ID, workspace path, task text, evidence, or model/config
  content in the native window-state record;
- bounded integer dimensions/coordinates and known enum state only;
- atomic write where practical;
- restore only when the saved rectangle intersects a currently usable display and respects the existing minimum
  form size;
- corrupt/unknown-version/off-screen values fall back to the current safe centered default;
- do not persist minimized state as the startup state.

M75 may make WebView2/App Server startup/process-failure presentation actionable, but it must not silently spawn
unbounded restart loops or weaken the existing local-navigation policy.

## Authority and safety invariants

The following remain authoritative:

- M66–M68 Product store for Project/Chat lifecycle and restoration state;
- M69 durable conversation plan/binding reconciliation and submission idempotency;
- M70 activity projection over App Server/trace sources;
- M71 bounded context construction and provenance;
- frozen M72 sensitive-action approval identity and decisions;
- frozen M73 project-settings snapshots and named runtime profiles;
- frozen M74 resource snapshots, read-only diffs, and registered-artifact containment/digest checks;
- AppServerService/AppSessionStore for session lifecycle and cancellation semantics;
- coding/tool permission and side-effect checks;
- verifier outputs for verification truth;
- trace/evidence manifests, signatures, receipts, and capsules for evidence authority;
- M37/M46 stream cursor/reconnect rules for the Advanced operator surface;
- M48/M49 reload-auth/operator-selection security boundaries.

M75 must not:

- claim to resume a killed running model/tool process from memory;
- rewrite an interrupted/failed execution to running/succeeded;
- mutate prior immutable conversation plans, settings snapshots, approvals, resources, traces, reports, evidence,
  memory, or improvement records;
- automatically replay side-effecting work after an ambiguous failure under a new identity;
- make browser connection state authoritative task state;
- add infinite automatic reconnect/restart polling;
- persist the long-lived bearer, reload capability outside its inherited M48 key, chat text, task text, resources,
  activity cursors, approvals, evidence, or arbitrary request bodies as M75 browser storage;
- bypass M72 approval for a retry/recovery action that reaches a sensitive action in the new execution;
- hard-kill arbitrary OS processes, add shell/Git command surfaces, broaden filesystem access, or expose caller-
  selected session/workspace/output roots;
- change verification success, evidence trust, promotion, memory, model-routing, or self-improvement authority;
- add cloud sync, remote access, multi-user sessions, installer/runtime distribution, or background service
  management.

## Qualification plan

Before freeze, one exact M75 head must demonstrate:

- exact frozen M74 base and this document as commit 1;
- durable Project/Chat restoration still uses authoritative product state and rejects missing/archived identities;
- App Server restart converts pre-restart running work to explicit interrupted terminal state and M75 projection
  reports it without pretending to resume;
- created-session restart recovery and conversation reconciliation remain deterministic and duplicate-free;
- everyday activity transport retry is bounded, resets only after a validated response, stops after exhaustion,
  and supports explicit recovery without persisting an activity cursor;
- ambiguous execution-submit retry reuses the exact submission ID and cannot duplicate an accepted turn;
- Stop resolves execution ownership server-side and delegates only to inherited cancellation authority;
- Retry creates a new execution and preserves the prior execution immutably;
- Continue creates a normal new user-authored turn and does not resurrect runtime state;
- selection/lock/navigation generation guards prevent stale recovery callbacks from affecting a new Project/Chat;
- keyboard shortcuts invoke only existing qualified visible action paths and respect focus/disabled/approval state;
- window-state persistence contains only bounded non-sensitive geometry/state and safely handles corrupt/off-screen
  values;
- loading/error/empty/interrupted/reconnect states render through safe text/DOM paths;
- no new bearer/request/evidence/browser-storage authority is introduced;
- focused backend/UI/desktop tests pass;
- full inherited pytest, `harness-x --help`, and default config validation pass on Ubuntu and Windows;
- inherited Windows desktop restore/build/smoke/publish/artifact qualification passes; and
- final source/diff/review audit records the exact qualified M75 head and synthetic merge in the PR body only.

## Non-goals

M75 does not implement resumable model/tool checkpoints, crash-safe continuation inside a running coding runtime,
Service Workers, offline mode, WebSockets, remote/server failover, background OS services, cloud synchronization,
multi-user recovery, generic command retries, repository history management, rich document extraction, installer
packaging, or any new verification/evidence/memory/promotion/self-improvement authority.

M76 remains the planned Improvement Observatory and must not begin until this everyday reliability checkpoint is
independently qualified and frozen.
