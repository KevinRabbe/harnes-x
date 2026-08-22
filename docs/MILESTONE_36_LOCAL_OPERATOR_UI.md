# Milestone 36 — Local Operator UI

M36 ships the first human-facing Harness X operator surface on top of the frozen M34 App Server and M35 verified causal-trace stream.

The milestone is deliberately a **presentation/client layer**. It does not create a browser-owned model loop, a second event ledger, a second verifier, or a privileged filesystem API.

## Stack

M36 is stacked exactly on frozen M35:

```text
M35 frozen head
8e2858ececc3b4575965e765958ce76bedfac44d
        |
        v
M36 Local Operator UI
```

The runtime/data flow is:

```text
operator browser
      |
      | same-origin static assets
      | authenticated JSON + fetch-streamed SSE
      v
LocalOperatorHTTPServer
      |
      | inherits M34/M35 API handlers
      v
AppServerService
      |
      +---- durable App Server lifecycle ledger
      |
      +---- verified read-only TraceStore projection
      |
      v
existing isolated Harness X coding runtime
```

## UI surface

The installed `harness-x-app-server` command now serves:

```text
http://127.0.0.1:<port>/ui/
```

alongside the existing API.

The CLI startup JSON includes `ui_url` in addition to the existing base URL, token path, server-info path, and process ID.

The UI supports:

- local-server health display;
- explicit bearer-token unlock/lock;
- recent session listing;
- creation of a coding session through the existing `POST /v1/sessions` contract;
- model-profile, verification, memory, budget, and optional browser-plan inputs already supported by `CodingSessionRequest`;
- selected-session status and request metadata;
- cancellation requests through the existing session-cancel endpoint;
- coding-report and trace identity/path display when those durable pointers become available;
- App Server lifecycle history;
- verified causal execution history;
- live lifecycle and causal progress while a task is running.

M36 does not add a new task-request schema. The form is only a browser client for the existing M34/M35 request protocol.

## Browser authentication

The static UI assets contain no session data and no access token, so the three exact packaged assets may be fetched from the loopback origin before authentication:

```text
/ui/
/ui/styles.css
/ui/app.js
```

All session data and mutations remain protected by the existing bearer token.

The browser client deliberately does **not** persist the bearer token in:

- a URL query parameter;
- a URL fragment;
- a cookie;
- `localStorage`;
- `sessionStorage`;
- a server-created browser session.

The operator pastes the existing token from the App Server `access-token` file. JavaScript holds it only in the current page's in-memory state and clears that reference when the operator selects **Forget token** or unloads the page.

This avoids adding a second credential lifecycle in M36.

## Authenticated streaming from the browser

Native browser `EventSource` cannot attach the custom `Authorization: Bearer ...` header required by the App Server.

M36 therefore does **not** weaken SSE authentication and does not place the token in an SSE URL.

Instead the browser opens the existing lifecycle and trace SSE endpoints with authenticated `fetch()` calls and incrementally parses the response body:

```text
GET /v1/sessions/{id}/events/stream?after=<sequence>
Authorization: Bearer <token>
Accept: text/event-stream
```

and:

```text
GET /v1/sessions/{id}/trace/stream?after=<step>
Authorization: Bearer <token>
Accept: text/event-stream
```

The stream cursors retain their existing authority:

- lifecycle stream cursor = App Server event sequence;
- causal stream cursor = authoritative trace step.

Switching sessions or locking the UI aborts the active browser fetch streams.

## No new event truth

M36 stores no lifecycle or causal history of its own.

Initial rendering pages directly from:

```text
GET /v1/sessions/{id}/events
GET /v1/sessions/{id}/trace
```

and live updates come directly from the two M34/M35 streams.

The browser de-duplicates display rows by the source sequence/step only to avoid rendering the same already-paged event again when the live stream begins. That browser display behavior is not persistence and cannot change source history.

## XSS / rendering boundary

Session tasks, paths, lifecycle payloads, trace metadata, components, references, errors, and other runtime-origin strings are treated as untrusted display data.

The M36 client creates DOM elements and assigns data with `textContent`. It does not use `innerHTML`, HTML template injection, `eval`, or dynamically loaded scripts.

Trace projection already bounds and credential-key-redacts metadata at the M35 server boundary; M36 still treats even the bounded projection as untrusted text.

## Static asset boundary

M36 does not expose a generic static-file handler.

`ui_assets.py` contains an exact path allowlist for only:

```text
/ui/
/ui/app.js
/ui/styles.css
```

Assets are loaded from packaged Harness X resources and each asset has a hard 512 KiB maximum. Paths such as `../protocol.py`, arbitrary package files, and arbitrary filesystem paths are not mapped by the UI asset loader.

This keeps M34's “no arbitrary file-serving endpoint” invariant intact.

## Browser response hardening

M36 UI responses are `no-store` and include:

```text
Content-Security-Policy:
  default-src 'none';
  script-src 'self';
  style-src 'self';
  connect-src 'self';
  img-src 'none';
  font-src 'none';
  object-src 'none';
  base-uri 'none';
  form-action 'none';
  frame-ancestors 'none'
```

They also include:

- `Cross-Origin-Opener-Policy: same-origin`;
- `Cross-Origin-Resource-Policy: same-origin`;
- `Permissions-Policy` disabling camera, microphone, and geolocation;
- `Referrer-Policy: no-referrer`;
- `X-Content-Type-Options: nosniff`;
- `X-Frame-Options: DENY`.

The UI has no inline script or inline style requirement, so the CSP does not require `unsafe-inline`.

M34 Host-header validation still runs before M36 serves a UI asset. The server still binds only to literal `127.0.0.1`.

## Session creation authority

The UI can ask the App Server to create a coding session, but it cannot create one directly.

The browser serializes the form into the existing `CodingSessionRequest` shape and sends it to:

```text
POST /v1/sessions
```

The inherited server/service layers still perform Pydantic validation, live-path validation at scheduling time, single-worker scheduling, model-profile resolution, isolation, and all downstream coding-runtime checks.

A value typed into the UI has no authority until those existing software boundaries accept it.

## Cancellation semantics

M36 exposes the existing cancellation request:

```text
POST /v1/sessions/{id}/cancel
```

It does not strengthen the cancellation guarantee.

The M34 truthfulness rule still applies:

- queued work can be cancelled before execution;
- running work can record `cancel_requested`;
- M36 does not claim hard preemption of the existing coding loop.

The button text intentionally says **Request cancel** rather than implying process termination.

## Lifecycle versus causal execution

The UI presents the two streams separately.

### Lifecycle

The App Server ledger answers questions such as:

```text
session created
session started
trace attached
artifact available
cancel requested
session completed/failed/cancelled
```

### Execution

The M35 projection answers questions such as:

```text
reasoning requested/completed
action proposed
tool permission checked
tool execution finished
verification completed
coding phase changed
budget changed
error recorded
```

The UI does not collapse these into a synthetic “master timeline” because they have different source identities and authority semantics.

## Compatibility

`LocalAppHTTPServer` remains available and unchanged as the M34/M35 API-only transport.

M36 adds:

```python
LocalOperatorHTTPServer
```

which subclasses the existing transport and adds only the exact static UI routes before delegating every API route back to the inherited handlers.

The installed `harness-x-app-server` CLI selects `LocalOperatorHTTPServer`.

This keeps programmatic API-only consumers compatible while making the operator CLI product-facing by default.

## Deterministic acceptance

M36 acceptance covers:

- the UI document is reachable without a bearer token but contains no server token;
- `/v1/sessions` remains unauthorized without the bearer token;
- HTML receives the strict CSP and anti-framing/resource/referrer headers;
- the CSP contains no `unsafe-inline`;
- the JavaScript client uses bearer-authenticated `fetch` streaming rather than `EventSource`;
- the JavaScript source does not use `innerHTML`, `localStorage`, `sessionStorage`, or `document.cookie`;
- the exact UI asset allowlist rejects package/path traversal attempts;
- Host-header validation applies to UI routes;
- the M36 transport still successfully delegates authenticated session creation to the existing API/service stack.

The normal repository suite continues to cover all M34/M35 session, lifecycle, restart, corruption, partial-write, trace-stream, and isolated M30 integration contracts.

## Authority boundary

M36 cannot:

- invoke a model except by creating an ordinary validated App Server session;
- execute a tool directly;
- bypass tool permissions or budgets;
- establish a verification verdict;
- repair a corrupt trace;
- mutate TraceStore records;
- append lifecycle events directly;
- write task/project/procedure memory directly;
- promote a procedure revision;
- declare task success independently of the coding runtime/report;
- read arbitrary files through its static asset route;
- weaken bearer authentication for session state or SSE;
- claim hard cancellation of already-running work.

The browser is a local operator console over existing authorities, not an agent authority of its own.

## Current limitations

- The operator must paste the bearer token manually after a page load/reload.
- M36 is a browser UI, not yet a packaged native desktop shell.
- The UI exposes durable artifact paths but does not add arbitrary artifact download/file-serving endpoints.
- Session forms use text paths rather than a privileged native filesystem picker.
- The client is dependency-free JavaScript/CSS with no frontend framework or build pipeline.
- Browser-stream reconnection after a transport interruption is manual; source cursors make a future bounded reconnect safe to implement.
- M34 running-task hard cancellation remains unchanged.
- M35 trace projection remains structured causal metadata rather than private free-form chain-of-thought.

These constraints intentionally keep the first UI small, inspectable, local, and subordinate to the already-qualified Harness X runtime boundaries.
