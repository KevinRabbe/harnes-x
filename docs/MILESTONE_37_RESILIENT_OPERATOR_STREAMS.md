# Milestone 37 — Resilient Operator Streams

M37 is stacked directly on frozen M36 and is intentionally narrow. It improves the local operator UI's transport resilience without creating a second lifecycle or causal event truth and without changing any Harness X runtime authority.

## Architecture

M34 owns durable App Server lifecycle state. M35 exposes a verified read-only projection of the authoritative causal `TraceStore`. M36 adds a browser client over those existing APIs. M37 changes only how that browser client survives an interrupted HTTP/SSE connection.

There is still no browser-owned event ledger and no combined synthetic timeline.

The two source cursors remain separate:

```text
lifecycle cursor = durable AppEvent.sequence
trace cursor     = verified source TraceRecord.step
```

The browser may reconnect only from one of those source identities that it has already received and validated.

## Packaged reconnect policy

M37 adds one small dependency-free browser asset:

```text
/ui/stream_policy.js
```

It is loaded before `/ui/app.js` and is served through the same exact static-asset allowlist as the existing M36 assets. It is not a generic script/file endpoint.

The policy defines a bounded consecutive-failure schedule:

```text
250 ms
500 ms
1000 ms
2000 ms
4000 ms
```

After the fifth reconnect attempt, another failure is not scheduled automatically. The stream is shown as disconnected and the operator can explicitly reselect or refresh the session.

A valid newly received source event resets the consecutive-failure count. Merely opening an HTTP connection does not. This prevents a repeatedly empty/broken connection from acquiring an unbounded retry lifetime while still allowing a long-running healthy session to survive separate later network interruptions.

## Cursor integrity

M37 treats an SSE cursor as acceptable only when all of the following hold:

1. the SSE `id` is a positive safe integer;
2. the cursor inside the JSON payload is also a safe integer;
3. the SSE `id` exactly equals the payload cursor;
4. the new cursor is exactly `current_cursor + 1`.

For lifecycle events the payload cursor is `AppEvent.sequence`.

For causal trace events the payload cursor is M35's projected source `step`.

M37 also checks the initial bounded REST pages before streaming. The client derives its starting cursor by walking the events it actually received and requires that value to equal the API page's `next_after`. It does not blindly trust or synthesize a later cursor.

If a stream presents a duplicate, gap, mismatched ID/payload cursor, malformed JSON event, or unexpected causal event type, that connection is treated as failed. Reconnect starts from the last previously accepted cursor, not from the malformed event.

## Reconnect behavior

Lifecycle and causal streams reconnect independently. Losing one does not abort the other.

For each selected session the browser:

1. loads the existing bounded lifecycle and trace pages;
2. validates the source cursor sequence it actually rendered;
3. opens each authenticated SSE request with `after=<last accepted source cursor>`;
4. advances the local cursor only after a valid source event is received;
5. on an unexpected close/error, schedules the bounded reconnect policy from that exact cursor;
6. after a clean stream close, reads the current session snapshot;
7. if the session is terminal, marks that stream closed and does not reconnect;
8. if the session is not terminal, treats the clean close as an interrupted/idle stream and reconnects within the same bounded policy.

A transport failure while a terminal session's remaining evidence is still being drained can still use the bounded cursor resume path. Once the server cleanly drains the terminal stream and the terminal snapshot is observed, reconnect stops permanently for that selection.

HTTP 400/401/403/404 stream failures are treated as non-retriable client/auth/request errors. Transient transport failures and unexpected connection termination use the bounded reconnect policy.

## Trace corruption

M35 remains the causal integrity authority.

If `/trace/stream` emits the existing `trace_error` event for source corruption, M37:

- renders the corruption diagnostic;
- marks the causal stream corrupt;
- does not advance the source cursor for that diagnostic;
- does not reconnect around the corruption.

The UI cannot repair, skip, rewrite, or reinterpret a corrupt complete source trace record.

## Selection and cancellation safety

M36 already added a `selectionGeneration` guard after source review found a stale-selection presentation race. M37 preserves that guard and extends cancellation to reconnect work.

Changing the selected session, locking the operator view, or unloading the page:

- aborts every active stream `AbortController`;
- clears every pending reconnect timer;
- invalidates stale selection generations;
- prevents old stream callbacks or reconnect timers from rendering into the new selection.

This is presentation safety only. It does not cancel the Harness X coding task itself.

## Authentication and browser storage

M37 does not change M36 authentication.

Every stateful JSON request and every stream request still sends:

```text
Authorization: Bearer <token>
```

The token remains in the page's in-memory JavaScript state only. M37 does not introduce:

- token query parameters;
- URL fragments containing credentials;
- cookies;
- `localStorage`;
- `sessionStorage`;
- IndexedDB credential persistence;
- a second browser-session token.

Reconnect URLs contain only the session identifier and numeric authoritative `after` cursor.

## Deterministic qualification

The shipped reconnect policy is executable as plain JavaScript without a framework or build step. When Node is available, tests execute the exact packaged `stream_policy.js` and require:

- exactly five reconnect attempts;
- exact delays `[250, 500, 1000, 2000, 4000]` ms;
- no sixth automatic delay;
- valid contiguous cursor progression;
- mismatched SSE ID/payload cursor rejection;
- cursor-gap rejection;
- duplicate-cursor rejection;
- invalid-current-cursor rejection;
- exact terminal/nonterminal session classification.

CI also runs `node --check` against both the packaged policy and packaged application client when Node is available.

HTTP/UI tests continue to require:

- public static UI assets but bearer-authenticated session APIs;
- strict CSP/security headers;
- exact static-asset allowlisting;
- no arbitrary package/filesystem reads;
- in-memory bearer handling;
- authenticated `fetch()` streaming rather than `EventSource`;
- safe DOM rendering without `innerHTML`;
- Host-header validation;
- inherited authenticated session creation.

The existing M34/M35 tests remain responsible for durable session/event semantics, source-trace validation, live SSE behavior, corruption, partial writes, restart reconciliation, and the real isolated coding integration.

## Authority boundary

M37 cannot:

- write or synthesize App Server lifecycle events;
- write, repair, skip, or synthesize causal trace records;
- convert UI connection state into task state;
- declare verification success;
- declare task completion;
- execute a tool directly;
- bypass permissions or budgets;
- mutate project/procedure memory;
- promote revisions;
- hard-cancel a running coding task;
- persist the bearer token;
- resume from an unobserved or non-contiguous source cursor.

Reconnect is transport recovery only. Runtime and verification authority remain exactly where they were before M37.

## Non-goals and current limitations

M37 does not add a desktop shell, arbitrary artifact/file serving, privileged filesystem pickers, hard running-task cancellation, WebSockets, a frontend framework, or any new model/tool/verification/memory/completion authority.

Additional limitations remain intentional:

- the operator still pastes the bearer token manually after page load/reload;
- reconnect state is tab-local and is not persisted across a page reload;
- only the currently selected session has live streams;
- after the bounded reconnect budget is exhausted, recovery is explicit through reselect/refresh rather than infinite background polling;
- the UI still exposes artifact paths rather than arbitrary artifact download endpoints;
- M34 running-task cancellation semantics remain unchanged;
- M35 exposes structured causal metadata, not private free-form chain-of-thought.

## Freeze gates

M37 is ready to freeze only when:

- reconnect behavior is bounded and deterministic;
- reconnect resumes from the last actually received authoritative cursor;
- cleanly drained terminal streams stop reconnecting;
- source corruption is not retried around;
- selection changes/locking abort active streams and pending reconnect timers;
- stale stream generations cannot render into a newly selected session;
- bearer authentication remains header-only and in-memory;
- deterministic tests cover reconnect policy/cursor safety;
- packaged JavaScript syntax passes qualification;
- M36→M37 diff is confined to intended UI/tests/docs;
- exact-head Linux CI passes.
