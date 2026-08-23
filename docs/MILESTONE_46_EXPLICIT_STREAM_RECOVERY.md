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

## Layered implementation

M46 deliberately leaves the qualified M37 stream engine untouched:

```text
/ui/stream_policy.js   unchanged
/ui/app.js             unchanged
```

A new exact-allowlisted classic script is loaded after the existing app client and before M40 bootstrap:

```text
/ui/app.js
/ui/stream_recovery.js
/ui/bootstrap.js
```

`stream_recovery.js` captures the existing `scheduleReconnect` and `abortStreams` functions and installs narrow wrappers around those existing global bindings.

The reconnect wrapper delegates to the original M37 function on every call. Only when `streamPolicy.reconnectDelayMs(consecutiveFailures - 1)` returns `null`—the same boundary at which the existing client renders `Disconnected`—does M46 retain a manual recovery cursor for that stream.

The abort wrapper delegates to the original abort behavior while additionally clearing M46 recovery entries, any in-flight manual recovery identity, and the recovery status line. Existing selection changes, stream restarts, and operator locking already call `abortStreams`, so they inherit this cleanup without a parallel lifecycle mechanism.

M46 does not register a second authentication submit handler. The client reuses `state.token` and the existing authenticated `api()` helper after `app.js` has unlocked the operator view. M40 bootstrap therefore remains on its existing qualified auth-submit path.

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

Recovery state stores only `{sessionId, cursor, generation}` independently for lifecycle and trace. It is not persisted.

A newly exhausted stream replaces any stale success text with an explicit status explaining that automatic reconnect is exhausted and manual reconnect is available.

Recovery state is cleared when streams are restarted through the normal selection path, the selected session changes, the operator locks, a terminal refresh closes streams, a non-retriable recovery refresh is rejected, or the page unloads. A manually restarted stream consumes its recovery entry before `runLifecycleStream` / `runTraceStream` is invoked; a later retriable interruption must therefore exhaust the normal M37 schedule again before becoming manually recoverable again.

No reconnect state is persisted in cookies, `localStorage`, or `sessionStorage`.

## Explicit recovery action

When `Reconnect live streams` is activated, the client:

1. requires an unlocked page-memory bearer and current selected session;
2. captures the current selection generation;
3. creates one in-flight recovery identity for duplicate-click suppression;
4. fetches the selected session snapshot through the existing authenticated `api()` helper before restarting anything;
5. aborts without starting streams if the selection/generation changed during that refresh;
6. renders the refreshed snapshot through the existing operator projection;
7. if the session is terminal, invokes the existing abort path to stop any remaining stream controller/timer, clears recovery state, and renders lifecycle/trace states `Closed`;
8. otherwise reads the currently recoverable lifecycle and trace entries independently;
9. restarts only those recoverable streams from their retained cursors;
10. passes `0` as the restarted stream's consecutive-failure count, restoring the normal bounded M37 schedule for future interruptions;
11. never restarts an unaffected/live peer stream merely because the other stream is disconnected.

The button disables while a manual recovery attempt is in flight. The in-flight state stores an object identity tied to the captured session/generation. Finalization clears the in-flight flag only if that same attempt object is still current, so stale asynchronous completion cannot unlock or interfere with a later selection's recovery attempt.

A transient failure of the pre-reconnect snapshot refresh leaves the exhausted recovery entries available for another explicit operator attempt. A 400/401/403/404 refresh rejection clears eligibility and remains fail-visible.

## Non-retriable and corruption boundaries

M46 does not weaken M37 failure classification.

HTTP 400, 401, 403, and 404 remain non-retriable stream errors. In unchanged `app.js`, both lifecycle and trace catch paths return on those statuses before calling `scheduleReconnect`, so the M46 wrapper never receives those failures and cannot mark them manually recoverable.

Trace corruption remains fail-closed. The unchanged trace client returns from its corruption path before reconnect scheduling. A manually restarted trace also consumes its recovery entry before the stream starts; if that resumed stream reports corruption, no M46 recovery entry remains and no reconnect is scheduled.

Terminal closure remains terminal closure, not disconnection. A successful selected-session refresh that finds `succeeded`, `failed`, or `cancelled` clears recovery state, aborts any remaining observation stream activity, and leaves both stream pills `Closed`.

## Independent stream recovery

Lifecycle and trace recovery state is independent.

If only lifecycle exhausts reconnect attempts, the recovery action restarts lifecycle only from its last lifecycle sequence and leaves a still-live trace stream alone.

If only trace exhausts, it restarts trace only from its last trace step.

If both exhaust, one operator action restarts both, each from its own retained cursor.

If a previously live peer stream also reaches exhaustion while the pre-reconnect snapshot refresh is in flight, it becomes independently recoverable and may be included when the action reads the current recovery entries after that refresh. No stream is restarted unless it has actually reached the exhausted-retry boundary.

## Selection, lock, and unload safety

Existing generation/abort semantics remain authoritative.

Changing the selected session already calls `abortStreams` before incrementing selection generation. Because M46 wraps that function, selection change also clears all manual recovery entries and any in-flight identity before the new selection begins.

Locking the operator view likewise uses the wrapped abort path before the page-memory bearer/session state is cleared.

The original M37 `beforeunload` handler still aborts stream controllers/timers. M46 adds only a second unload listener that clears its page-memory recovery entries and in-flight identity; no unload persistence is introduced.

A stale manual recovery request for session A may never start or update UI for later-selected session B. Generation checks run immediately after the awaited snapshot refresh, and the attempt-identity check in `finally` prevents stale completion from mutating a newer attempt's in-flight state.

## UI contract

M46 adds one session-level button:

```text
Reconnect live streams
```

The button starts hidden and disabled and becomes visible/enabled only when the current page-memory selection has at least one manually recoverable stream, the bearer is present, the selected snapshot is not already known terminal, and no recovery is in flight.

A recovery status line is rendered with `textContent` under the selected-session metadata so it does not alter the existing `.session-header` flex-child layout. It reports exhausted automatic retry, pre-reconnect checking, successful lifecycle/trace restart, terminal closure, or fail-visible refresh rejection/failure.

The action does not imply that the underlying task/runtime is being retried; it reconnects only read-only operator event streams.

Lifecycle and trace pills continue to represent their own states. Exhausted retriable streams remain visibly `Disconnected` until explicit recovery is initiated or the selection changes.

No bearer or reconnect metadata is exposed in URL/query/fragment or persistent browser storage.

## Qualification strategy

Focused M46 tests preserve both structural and behavioral boundaries.

Static/package checks require:

- exact UI allowlist behavior;
- `app.js < stream_recovery.js < bootstrap.js` ordering;
- unchanged M37 reconnect-delay literal and `maxReconnectAttempts` derivation;
- unchanged 400/401/403/404 classification and trace-corruption short-circuit in `app.js`;
- no storage/cookie/URL/EventSource surface in the recovery overlay;
- Node syntax validity when Node is available.

A Node behavior harness stubs the existing qualified app globals and executes the packaged `stream_recovery.js`. It proves lifecycle-only recovery leaves trace untouched, lifecycle/trace cursors remain independent, restarted failure count is zero, duplicate recovery calls produce one snapshot refresh, stale generation cannot restart a stream, terminal refresh does not restart and renders both streams closed, non-retriable refresh rejection clears eligibility, and unload cleanup removes eligibility.

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

Manual reconnect is intentionally operator-triggered only after automatic retries are exhausted. It is not an automatic infinite-retry mechanism.

## Deterministic acceptance

Before freeze, M46 must prove:

- exact frozen M45 base;
- M46 scope document is the first branch commit;
- no backend/App Server API/protocol/store/service/runtime changes;
- `app.js` and `stream_policy.js` remain absent from the M45→M46 diff;
- existing reconnect delay schedule and `maxReconnectAttempts` remain unchanged;
- explicit recovery eligibility arises only after bounded retriable exhaustion;
- HTTP 400/401/403/404 do not become manually recoverable;
- trace corruption does not become manually recoverable;
- lifecycle and trace recovery eligibility/cursors are independent;
- retained cursors are the last authoritative accepted sequence/step and recovery never restarts from zero;
- manual recovery refreshes the current session before restarting streams;
- stale selection-generation recovery is suppressed;
- stale asynchronous finalization cannot alter a newer recovery attempt;
- terminal refresh clears recovery, stops remaining observation activity, and does not restart streams;
- only exhausted streams restart; unaffected/live peer streams are left untouched;
- restarted streams begin with automatic failure count zero and retain the existing bounded schedule thereafter;
- repeated recovery clicks cannot start duplicate loops while recovery is in flight;
- selection change, lock, and unload clear/cancel recovery state without persistence;
- button/status rendering uses existing safe DOM/text patterns and does not require a CSS change;
- no cookies, `localStorage`, `sessionStorage`, URL token/reconnect state, or new credential path;
- exact M45→M46 diff remains confined to this document, one new recovery client, operator markup, one exact static allowlist entry, and focused tests;
- exact-head Linux CI passes including existing `harness-x --help` and `validate-config` gates.
