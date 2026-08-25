# Milestone 70 — Streaming Work Activity

## Objective

Add a stable, authenticated product projection of existing Harness X execution progress so the everyday Projects/Chats workspace can show meaningful work activity while an M69 execution is running.

M70 must not make UI activity authoritative. Existing App Server session state, trace/evidence records, coding reports, verification results, and conversation-execution bindings remain the sources of truth. Product activity is a deterministic projection over those existing records.

## Exact base

M70 is stacked exactly on frozen M69:

`4f904123b849d1263a161b1940eaa6b22853fad0`

## Scope

M70 may add:

- a versioned product work-event schema;
- a deterministic projection from existing App Server session/events/report state into product work events;
- authenticated work-activity read routes bound to the owning project/chat/execution;
- cursor-based incremental polling suitable for later transport replacement;
- everyday chat rendering for in-progress activity and terminal summaries;
- focused tests for ordering, idempotency, ownership, restart/replay, malformed cursors, and safe rendering.

M70 may make narrow changes to the M69 conversation transport or UI integration when required to expose the projection.

## Explicit non-goals

M70 does not add:

- WebSocket/SSE transport as a new authority boundary;
- rich multi-turn context construction (M71);
- project runtime/settings profiles (M72);
- approval workflows (M73);
- attachments, file browsing, or interactive diffs (M74);
- new reasoning, memory, verification, evidence, promotion, or self-improvement authority;
- direct browser access to raw trace/evidence files;
- UI-authored assistant/system messages;
- cancellation/retry policy expansion beyond the frozen M69 bridge.

## Authority and projection invariants

1. Product work events are projections, never authoritative execution state.
2. Every projected event is tied to one M69 `execution_id` and its bound `app_session_id`.
3. Project/chat ownership must be revalidated on every authenticated activity request.
4. Projection order is deterministic for a fixed authoritative session snapshot/event ledger.
5. Stable source records project to stable event identities so repeated polling/restart does not duplicate UI activity.
6. The API must support incremental reads using an opaque or validated monotonic cursor; invalid/future cursors fail closed.
7. A terminal execution must always project exactly one terminal product event consistent with the authoritative App Session terminal state.
8. The projection may summarize or classify existing execution records, but it must not invent successful verification, file changes, or tool completion that are absent from authoritative records.
9. UI rendering must treat all projected text as data and must not use unsafe HTML injection.
10. Existing Advanced / Local Operator evidence and session inspection remain available and unchanged in authority.

## Initial product event families

The first stable schema should cover only evidence that can be grounded in current App Server records:

- `work_started`
- `status_changed`
- `tool_started`
- `tool_completed`
- `file_changed`
- `verification_started`
- `verification_result`
- `assistant_update`
- `work_completed`
- `work_failed`

If a proposed family cannot be mapped deterministically from current authoritative records, omit it rather than synthesizing it.

## HTTP boundary

The M70 activity surface must remain under the inherited same-origin bearer-authenticated `/v1/...` boundary. The browser must continue to use the existing authenticated request helper and must not gain direct access to bearer/token files, App Server state files, or output-root filesystem paths.

The activity API should allow the client to ask for events for a specific project/chat/execution and an incremental cursor. The response must include schema version, execution identity, stable ordered events, next cursor, and terminal state sufficient for coarse polling.

## UI behavior

While an M69 work turn is active, the everyday chat should display compact activity beneath that turn. Repeated polls must update or append deterministically without duplicating events. Terminal state should stop polling and refresh durable chat history so the software-owned terminal assistant result remains the durable conversational record.

The Local Operator panel remains the advanced/raw inspection surface.

## Qualification

Before freeze:

- focused product-event projection tests pass;
- focused authenticated HTTP ownership/cursor tests pass;
- focused everyday UI rendering/polling tests pass;
- full pytest passes on Ubuntu and Windows;
- `harness-x --help` passes;
- `harness-x validate-config configs/default.yaml` passes;
- inherited Windows .NET restore/build/desktop smoke/publish/artifact gates pass;
- exact base/head compare is audited;
- reviews and review threads are rechecked;
- the final exact-head evidence is recorded in the draft PR body without moving the head.

## Merge policy

Keep the milestone PR draft/open/unmerged. No merge is authorized without explicit operator approval.
