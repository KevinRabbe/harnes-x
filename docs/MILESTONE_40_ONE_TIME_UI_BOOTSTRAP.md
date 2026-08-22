# Milestone 40 — One-Time Local UI Bootstrap

M40 is stacked directly on frozen M39 and closes the oldest remaining operator-ergonomics gap from M36: opening the local operator UI currently requires manually reading and pasting the persistent bearer token.

M40 removes that copy/paste step only for an explicit local launch flow. It does not weaken the M34 bearer boundary, persist browser credentials, add remote authentication, or move any task/model/tool/verification authority.

## Scope

M40 adds an opt-in CLI launch path:

```text
harness-x-app-server --open-ui
```

The long-lived App Server bearer token never appears in a URL. Instead, the operator server issues one high-entropy, short-lived, single-use bootstrap ticket held only in server memory. The browser is opened to:

```text
http://127.0.0.1:<port>/ui/#bootstrap=<one-time-ticket>
```

The fragment is not transmitted in the initial HTTP request. A packaged bootstrap script reads the ticket, immediately removes the fragment with `history.replaceState`, exchanges the ticket through one loopback same-origin POST, then routes the returned existing bearer through the already-qualified manual-auth form listeners. The report viewer captures it first; the main client captures it and synchronously clears the password field before its asynchronous authenticated session load. Both clients then retain their bearer copy only in page memory.

Manual bearer-token unlock remains supported as a fallback and compatibility surface.

## Bootstrap ticket contract

A bootstrap ticket:

- is generated from at least 256 bits of cryptographic randomness;
- is never persisted to disk, `server-info.json`, session state, lifecycle events, or logs;
- has a fixed short lifetime;
- is single-use;
- invalidates any previously outstanding ticket when a new ticket is issued;
- is stored by the server only as a SHA-256 digest plus expiration metadata, not as the raw ticket;
- uses constant-time digest comparison on redemption;
- returns the same generic rejection for expired, used, malformed, and unknown tickets.

The bootstrap ticket is an ephemeral local capability. It is not the persistent App Server bearer token and cannot be used on normal `/v1/sessions...` endpoints.

## Exchange endpoint

M40 adds exactly one unauthenticated-by-bearer operator bootstrap endpoint:

```text
POST /v1/operator/bootstrap
```

This endpoint is still constrained by the existing literal-loopback bind and Host validation. It additionally requires a same-origin `Origin` header matching the request Host and accepts only a bounded JSON object containing exactly one `ticket` field. Query parameters are rejected.

Successful redemption returns the existing App Server bearer once in a `Cache-Control: no-store` JSON response. The endpoint does not create a second credential, session cookie, refresh token, or browser login state.

All other App Server state/read/write/stream routes retain the existing bearer requirement unchanged.

## Browser handling

The bootstrap browser client:

1. accepts only the exact `#bootstrap=<ticket>` fragment shape;
2. copies the ticket into a local JavaScript variable;
3. immediately replaces the current history entry with `/ui/` before network exchange;
4. POSTs the ticket as JSON to `/v1/operator/bootstrap` with browser credentials omitted;
5. receives the persistent bearer only in the no-store response body;
6. places the bearer into the existing password field only long enough to synchronously call `requestSubmit()`; the already-loaded report listener captures it, then the main listener captures and clears the field immediately;
7. clears its response-object bearer property and the DOM field, while the existing clients retain only their page-memory copies;
8. retains no ticket or bearer in the URL, query string, cookies, `localStorage`, or `sessionStorage`;
9. leaves the normal manual unlock UI available if exchange fails.

The bootstrap script is loaded after `report.js` and `app.js`, preserving the existing M38 requirement that the report auth listener is registered before the main listener clears the password field. The existing CSP continues to allow scripts and connections only from the same origin. No third-party script receives the capability or bearer.

## CLI boundary

`--open-ui` is explicit rather than the default so headless/operator scripting behavior is not changed unexpectedly.

The CLI must never print the bootstrap URL or raw ticket. Startup JSON may state whether automatic UI opening was requested/succeeded, but may contain only the normal public `ui_url` and existing token-file path. If the platform browser opener reports failure or raises, the outstanding bootstrap ticket is immediately invalidated, the server continues running, and manual bearer unlock remains available.

## Authority

M40 changes only local credential bootstrap ergonomics. It cannot:

- bypass the persistent bearer requirement on session/report/trace APIs;
- create sessions without ultimately possessing the bearer;
- execute tools or models;
- change verification/completion decisions;
- mutate report/trace/memory/revision state;
- grant remote network access;
- create multi-user identity or authorization;
- persist browser credentials.

## Non-goals / limitations

M40 does not add OAuth, cookies, refresh tokens, remote login, TLS, remote bind, multiple users, desktop-shell packaging, clipboard token copying, generic deep links, or filesystem permissions beyond existing App Server behavior.

A local process capable of observing or controlling the launched browser process or reading its memory is outside this single-user loopback threat boundary. The bootstrap ticket minimizes exposure by being short-lived and single-use; the long-lived bearer still never enters the URL.

## Deterministic acceptance

Before freeze, M40 must prove:

- tickets are cryptographically random, digest-only in server memory, short-lived, single-use, and replacement-invalidating;
- expired/used/unknown tickets fail identically;
- `/v1/operator/bootstrap` rejects invalid Host, missing/cross-origin Origin, query parameters, malformed JSON, extra fields, and bad tickets;
- rejected origin/request-shape attempts do not consume a valid ticket;
- normal stateful routes still reject unauthenticated requests;
- successful exchange returns the existing bearer and immediately consumes the ticket;
- server-info/startup metadata never persist or print the ticket/bootstrap URL;
- a failed browser open immediately invalidates the outstanding ticket;
- the browser fragment is scrubbed before exchange;
- no bearer is placed in URL/query/fragment/cookie/localStorage/sessionStorage;
- bootstrap success reuses the main operator and report-viewer manual auth listeners;
- manual token unlock still works;
- packaged bootstrap JavaScript has valid syntax when Node is available;
- exact M39→M40 diff remains confined to operator bootstrap/UI/CLI/tests/docs;
- exact-head Linux CI passes.
