# Milestone 46 — Explicit Operator Stream Recovery

M46 is stacked directly on frozen M45 and closes one narrow operator-availability gap left by M37: lifecycle and causal-trace SSE clients already use bounded automatic reconnect with authoritative cursors, but after the fixed reconnect schedule is exhausted the selected stream remains `Disconnected` until the operator reselects or reloads the session.

M46 adds an explicit operator-controlled recovery action for exhausted **retriable** live streams. It does not change server APIs, stream protocol, authentication, persistence, task/runtime authority, evidence semantics, or the M37 automatic reconnect schedule.

## Scope

M46 is a browser/operator-UI milestone only.

It adds one explicit action:

```text
Reconnect live streams
```

The action is available only while the operator view is unlocked, a non-terminal session is selected, and at least one lifecycle/trace stream has exhausted the existing bounded automatic reconnect schedule after a retriable interruption.

M46 does not create a server endpoint or store a reconnect request in App Server state.

## Existing M37 behavior preserved

The existing stream policy remains authoritative:

```text
250 ms
500 ms
1000 ms
2000 ms
4000 ms
```

Automatic reconnect remains bounded by `streamPolicy.maxReconnectAttempts` and uses the existing `reconnectDelayMs()` contract.

Existing cursor integrity remains unchanged:

- lifecycle resumes from the last accepted event `sequence`;
- trace resumes from the last accepted causal-trace `step`;
- SSE message ID must equal the authoritative payload cursor;
- the cursor must advance contiguously by exactly one;
- a received event resets consecutive automatic-failure count;
- selection-generation checks prevent one session's stream from presenting under another.

M46 must not replay from zero merely to recover a disconnected stream.

## Recoverable stream state

M46 tracks recovery eligibility independently for lifecycle and trace streams in page memory.

A stream becomes manually recoverable only when all of the following are true:

- the selected session/generation is still current;
- the session is not known terminal;
- the interruption was retriable under the existing M37 classification;
- automatic reconnect has exhausted the existing bounded schedule;
- the stream is not already live or waiting on an automatic reconnect timer;
- the stream has a retained last authoritative cursor.

Recovery state must record the exact cursor associated with the exhausted stream and must be cleared when the stream becomes live again, closes terminally, encounters a non-retriable error, reports trace corruption, the selected session changes, the operator locks, or the page unloads.

No reconnect state is persisted in cookies, `localStorage`, or `sessionStorage`.

## Explicit recovery action

When `Reconnect live streams` is activated, the client must:

1. require an unlocked page-memory bearer and current selected session;
2. capture the current selection generation;
3. fetch the selected session snapshot through the existing authenticated API helper before restarting anything;
4. abort without starting streams if the selection/generation changed during that refresh;
5. render the refreshed snapshot through the existing operator projection;
6. if the session is terminal, clear manual recovery eligibility and leave lifecycle/trace states `Closed`;
7. otherwise restart only streams currently marked manually recoverable;
8. resume each restarted stream from that stream's retained authoritative cursor;
9. reset that restarted stream's automatic reconnect failure count to zero so the normal bounded M37 schedule applies again after a later interruption;
10. never restart an unaffected/live stream merely because the peer stream is disconnected.

The button must disable immediately while a manual recovery attempt is in flight so repeated clicks cannot start duplicate stream loops for the same exhausted state.

## Non-retriable and corruption boundaries

M46 does not weaken M37 failure classification.

HTTP 400, 401, 403, and 404 remain non-retriable stream errors. They must not create manual-recovery eligibility.

Trace corruption remains fail-closed. Once the trace client reports `trace_corruption`/the existing corruption path, M46 must not expose that trace stream as manually recoverable and must not restart it from the recovery action.

Terminal closure remains terminal closure, not disconnection. A successful selected-session refresh that finds `succeeded`, `failed`, or `cancelled` must clear recovery state and leave both stream pills `Closed` as appropriate.

## Independent stream recovery

Lifecycle and trace recovery state is independent.

If only lifecycle exhausts reconnect attempts, the recovery action restarts lifecycle only from its last lifecycle sequence and leaves a still-live trace stream alone.

If only trace exhausts, it restarts trace only from its last trace step.

If both exhaust, one operator action may restart both, each from its own retained cursor.

## Selection, lock, and unload safety

Existing generation/abort semantics remain authoritative.

Changing the selected session must:

- abort current stream controllers;
- cancel reconnect timers;
- clear all manual recovery eligibility/cursors from the previous selection;
- increment selection generation before any new stream work can present.

Locking the operator view must clear manual recovery state together with the existing page-memory bearer and stream state.

Page unload must not persist recovery state.

A stale manual recovery request for session A may never start or update UI for later-selected session B.

## UI contract

M46 adds one button near the lifecycle/trace live-state controls:

```text
Reconnect live streams
```

The button is hidden or disabled unless manual recovery is currently meaningful. It must not imply that the underlying task/runtime is being retried; it only reconnects read-only operator event streams.

Lifecycle and trace pills continue to represent their own states. Exhausted retriable streams remain visibly `Disconnected` until explicit recovery is initiated or the selection changes.

No bearer or reconnect metadata is exposed in URL/query/fragment or persistent browser storage.

## Authority boundary

M46 changes only operator observation/recovery behavior. It cannot:

- create, transition, cancel, retry, or resume a Harness X task;
- change lifecycle/event/trace content;
- write App Server or TraceStore state;
- bypass authentication;
- alter model/tool/budget/controller/control policy;
- repair corrupt lifecycle or trace evidence;
- convert a non-retriable HTTP error into a successful stream;
- make browser state survive a full reload.

The App Server remains lifecycle/session authority. `TraceStore` remains causal-trace authority. M37's stream protocol/cursor rules and bounded automatic retry policy remain authoritative.

## Non-goals / limitations

M46 does not add reload-resilient authentication, session-selection persistence, Service Workers, cookies/storage credentials, background polling, server-side reconnect state, infinite retry, new SSE endpoints, generic network diagnostics, desktop-shell behavior, or evidence-format changes.

A full page reload still loses the page-memory bearer under the M40 security model.

## Deterministic acceptance

Before freeze, M46 must prove:

- exact frozen M45 base;
- M46 scope document is the first branch commit;
- no backend/App Server API/protocol/store/service/runtime changes;
- existing reconnect delay schedule and `maxReconnectAttempts` remain unchanged;
- explicit recovery eligibility arises only after bounded retriable exhaustion;
- HTTP 400/401/403/404 do not become manually recoverable;
- trace corruption does not become manually recoverable;
- lifecycle and trace recovery eligibility/cursors are independent;
- retained cursors are the last authoritative accepted sequence/step and recovery never restarts from zero;
- manual recovery refreshes the current session before restarting streams;
- stale selection-generation recovery is suppressed;
- terminal refresh clears recovery and does not restart streams;
- only exhausted streams restart; unaffected/live peer streams are left untouched;
- restarted streams begin with automatic failure count zero and retain the existing bounded schedule thereafter;
- repeated recovery clicks cannot start duplicate loops while recovery is in flight;
- selection change, lock, and unload clear/cancel recovery state without persistence;
- button/status rendering uses existing safe DOM/text patterns;
- no cookies, `localStorage`, `sessionStorage`, URL token/reconnect state, or new credential path;
- exact M45→M46 diff remains confined to this document, operator UI code/markup, and focused tests unless qualification demonstrates a narrower supporting change is required;
- exact-head Linux CI passes including existing `harness-x --help` and `validate-config` gates.
