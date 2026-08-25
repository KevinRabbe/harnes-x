# Milestone 69 — Conversation to Harness X Execution Bridge

## Authority

M69 is stacked exactly on frozen M68:

`d8a45a6b3b94e17fde7d0e22ae483a75ca6adb51`

M69 connects an authenticated everyday chat turn to the existing Harness X App Server coding-session runtime without merging the identity or authority of chats and execution sessions.

## Objective

A user should be able to submit one work turn from an active Project/Chat and have Harness X:

1. durably record the user message;
2. create one distinct software-owned App Server execution session for that turn;
3. durably link the chat turn and execution session;
4. execute against the Project's registered workspace using the existing coding runtime and existing model-selection/verification boundaries;
5. on terminal completion, append one software-owned assistant or system result record back into the same chat that identifies the linked execution outcome;
6. preserve the full existing session/report/trace/evidence records as the authoritative execution evidence.

A Chat is a long-lived conversation container. An App Session is one bounded execution attempt. A chat may own zero, one, or many execution sessions. A session belongs to at most one chat turn under this bridge.

## Hard boundaries

M69 does **not**:

- make chat history authoritative execution evidence;
- replace AppSessionStore, TraceStore, coding reports, verification, or evidence manifests;
- allow the browser to forge assistant/system messages or execution links;
- infer success from model prose;
- bypass verification requirements;
- add live reasoning/tool streaming to chat (M70);
- build rich context from all prior chats/files/memory (M71);
- add user-editable runtime/settings profiles (M72);
- add approval/decision workflows (M73);
- add attachments/diffs/artifact UX (M74);
- modify self-improvement promotion authority.

## Design constraints

### Identity

The bridge must preserve independent opaque identities:

- `project_*`
- `chat_*`
- `msg_*`
- `app_*`

No identifier is derived from another. Links are explicit durable records.

### Submission ownership

The authenticated browser submits only the operator-authored work text. Server software owns:

- the execution request material derived from Project defaults;
- App Session creation;
- execution-link creation/update;
- assistant/system completion records.

The browser must not be able to choose an arbitrary `app_*` id or append an assistant result directly.

### Atomicity and recovery

Submission crosses two already-durable stores (Project/Chat and App Session). M69 must define a recoverable ordering rather than pretending cross-file-system operations are transactional.

The durable link record is the reconciliation source for bridge recovery. Restart logic must handle at least:

- user message durable but session creation not completed;
- session durable but link finalization interrupted;
- terminal session durable but chat result append interrupted;
- repeated/retried reconciliation without duplicate result messages.

No recovery path may silently create two sessions for the same accepted bridge submission.

### Runtime request derivation

The Project's canonical `workspace_root` is the execution workspace.

M69 must use an explicit deterministic default execution policy until M72 introduces user-facing runtime/settings profiles. That policy must be documented in the implementation and represented in the bridge link/request projection so later settings changes cannot rewrite historical intent.

The bridge may use existing model-profile defaults, verification-plan conventions, or a narrowly introduced software-owned default policy, but it must not silently weaken the coding runtime's existing verification requirement.

### Completion projection

Terminal chat output is a product projection, not evidence truth. It must be derived from software-owned App Session state and/or the validated durable coding report. The result record must identify the execution session so the UI can later expose trace/report/evidence details without ambiguity.

M69 does not stream intermediate work into chat. Until M70, the everyday UI may show coarse execution states (`queued`, `running`, terminal) using polling of the bridge/session projection.

## API/UI target

M69 should expose one authenticated bridge submission endpoint under the existing Project/Chat hierarchy and one read projection for linked work, for example conceptually:

- submit work for a specific active Project/Chat;
- list/read execution links for that chat/turn;
- return the created `app_*` session identity and current status.

Exact route and schema names are fixed by implementation tests, not by this conceptual sketch.

The M68 composer should switch from 'save message only' to this bridge submission endpoint only after the server-side recovery semantics are implemented and tested.

## Qualification

Focused qualification must establish at least:

1. Chat and App Session identities remain independent.
2. One accepted bridge submission creates one user message, one execution session, and one durable link.
3. The execution request uses the registered Project workspace and deterministic M69 execution defaults.
4. Wrong-project, archived-project, archived-chat, malformed, and unauthorized submissions fail closed.
5. The browser still cannot append assistant/system messages directly.
6. Terminal success and failure each append an appropriate software-owned chat result exactly once.
7. Completion text does not claim success when software-owned session state failed.
8. Restart/reconciliation closes all defined append/session/link/result crash windows without duplicate sessions or duplicate terminal chat records.
9. A chat can own multiple sequential execution sessions while each session is linked to at most one turn.
10. Existing direct `/v1/sessions` behavior remains available under Advanced and is not redefined as chat identity.
11. M68 UI switches its normal composer to the bridge only after the server contract is stable.
12. Full Ubuntu and Windows suites, CLI/config gates, and Windows desktop restore/build/smoke/publish/artifact gates pass.

## Freeze rule

M69 freezes one exact head only after exact-head dual-platform CI, compare/diff audit, and final review/thread audit. The PR remains draft/open/unmerged unless the operator explicitly authorizes a merge.
