# Milestone 48 — Reload-Resilient Operator Reauthentication

M48 is stacked directly on frozen M47 and closes one explicit operator-availability limitation left by M40/M46: the operator bearer is intentionally kept only in page memory, so a full browser reload loses authentication and requires a fresh manual token paste or a new `--open-ui` launch.

M48 adds bounded same-tab reload reauthentication without persisting the long-lived App Server bearer. It introduces one short-lived single-use **reload capability** kept only in tab-scoped `sessionStorage`. The capability is not accepted by normal App Server APIs; it can only be redeemed through one loopback same-origin operator endpoint to recover the existing bearer, after which the bearer again flows through the already-qualified auth form listeners and remains page-memory only.

This milestone deliberately changes one M40 non-goal: `sessionStorage` may now contain the opaque reload capability. It still must never contain the persistent bearer, report/trace/evidence bytes, or task/runtime state. The security expansion is explicit and bounded rather than being described as equivalent to M40 page-memory-only authentication.

## Scope

M48 adds two operator endpoints layered over the frozen M47 operator server:

```text
POST /v1/operator/reload-ticket
POST /v1/operator/reload
```

`/v1/operator/reload-ticket` requires the existing bearer and same-origin browser `Origin`. It issues a new reload capability and may replace the previously stored capability supplied by the same tab.

`/v1/operator/reload` does not require the bearer because its only purpose is to redeem one reload capability. It still requires literal-loopback Host validation, an exact same-origin `Origin`, no query parameters, a bounded exact JSON request shape, and a valid unexpired one-time capability.

Successful redemption returns the existing persistent bearer once in a `Cache-Control: no-store` response. The bearer is never placed in `sessionStorage`, a URL, cookie, localStorage, server-info metadata, lifecycle state, or logs.

## Reload capability contract

A reload capability:

- contains at least 256 bits of cryptographic randomness;
- is accepted only by `/v1/operator/reload`;
- is single-use;
- has a fixed maximum lifetime of five minutes;
- is stored server-side only as SHA-256 digest plus expiration metadata;
- is never written to disk, lifecycle/session state, server-info, logs, or URLs;
- is stored browser-side only under one exact tab-scoped `sessionStorage` key;
- is removed from `sessionStorage` **before** redemption is attempted;
- becomes useless after server restart because the server-side digest set is process-memory only;
- cannot authorize ordinary `/v1/sessions...` routes directly.

The server keeps only a small bounded set of outstanding reload-capability digests so multiple operator tabs do not invalidate one another. Expired entries are pruned. Issuance with a previous capability removes that matching previous digest when present before issuing the replacement, keeping normal renewal bounded without requiring a persistent tab identifier.

Malformed/unknown/expired/used capabilities fail with the same generic rejection and do not expose whether a particular digest ever existed.

## Active-tab renewal

A capability valid for only five minutes would not provide useful reload behavior if it were issued once and allowed to expire while the operator remained active. The M48 browser client therefore renews the capability periodically while the view remains unlocked, using the existing page-memory bearer.

Renewal:

- occurs on a bounded interval shorter than the server capability lifetime;
- sends the currently stored previous capability, if any, only to the authenticated same-origin issuance endpoint so the server can replace it;
- stores only the newly returned reload capability;
- stops when the operator locks;
- clears local capability state on authenticated issuance failure;
- never copies the bearer into persistent browser storage.

Background-tab throttling or long suspension can still allow the capability to expire. In that case reload recovery fails closed and the existing manual bearer unlock / explicit `--open-ui` flow remains available. M48 therefore provides bounded active-tab reload resilience, not an indefinite browser login session.

## Browser reload flow

A new exact-allowlisted client is loaded before `app.js` so it can capture the submitted token before the existing main listener synchronously clears the password field, while still deferring reload redemption until all qualified auth listeners have registered.

On normal manual or M40-bootstrap authentication:

1. existing report/export/snapshot listeners retain their page-memory copies as before;
2. the M48 listener captures the submitted bearer only for the authenticated reload-ticket issuance request;
3. `app.js` captures the bearer and clears the password field as before;
4. M48 stores only the returned opaque reload capability in `sessionStorage`;
5. periodic capability renewal may continue while unlocked.

On full page reload:

1. M48 reads the exact reload-capability key from `sessionStorage`;
2. it removes the key immediately before any network request;
3. it rejects malformed local values without network redemption;
4. after all deferred UI scripts have registered their existing auth listeners, it POSTs the capability to `/v1/operator/reload` with `credentials: "omit"`;
5. successful redemption returns the existing bearer in a no-store response;
6. M48 places that bearer into the existing password input only long enough to synchronously call `requestSubmit()`;
7. all existing qualified listeners capture it in their normal order and `app.js` clears the field;
8. the M48 submit listener mints a fresh one-time reload capability for the next reload;
9. response/local temporary bearer references and the DOM field are cleared as far as JavaScript permits.

If an M40 `#bootstrap=...` fragment is present, the explicit bootstrap flow remains authoritative. M48 must not race a stored reload capability against a fresh bootstrap launch.

## Same-origin and request-shape boundary

Both M48 endpoints are loopback-only inherited operator routes and require exact same-origin `Origin` matching the current Host.

Issuance additionally requires the existing bearer. Redemption accepts no cookies or ambient browser credentials.

Both endpoints reject:

- query parameters;
- missing or cross-origin Origin;
- malformed/non-object JSON;
- unexpected fields;
- oversized bodies;
- invalid Host.

Rejected request-shape/origin attempts must not consume a valid reload capability.

## Storage boundary

M48 intentionally permits exactly one new browser-persistent secret: the opaque reload capability in tab-scoped `sessionStorage`.

The following remain forbidden:

- persistent bearer in `sessionStorage`, localStorage, IndexedDB, Cache Storage, cookies, URL, fragment, or query;
- reload capability in localStorage, cookies, IndexedDB, Cache Storage, URL, fragment, or query;
- report/trace/manifest/lifecycle/snapshot evidence in browser persistent storage;
- a refresh token or second long-lived bearer;
- server-side durable browser-login state.

Locking the operator clears the tab-scoped capability synchronously and stops renewal. An already issued server-side digest may remain until its short expiration if the page cannot explicitly revoke it; that residual lifetime is part of the bounded capability model and must be documented/tested rather than hidden.

## Multi-tab boundary

M48 must not make one tab's ordinary renewal invalidate every other tab. The reload-capability store therefore supports a small bounded set of independent outstanding digests rather than the M40 bootstrap store's single replacement-invalidating slot.

No user identity, tab identity, session cookie, or authorization role is introduced. This remains a single-user loopback operator surface.

## Authority boundary

M48 changes only local operator credential continuity across a browser reload. It cannot:

- bypass the persistent bearer requirement on normal stateful APIs;
- create/cancel/retry/resume Harness X tasks;
- change App Server lifecycle/session authority;
- change report/trace/evidence provenance;
- execute models or tools;
- change verifier/completion decisions;
- mutate memory, budgets, controller, or control policy;
- grant remote network access;
- create multi-user identity or authorization.

The App Server bearer remains the sole credential for normal operator APIs. The reload capability is a short-lived one-time bridge back to that credential through an exact same-origin endpoint.

## Security limitation

M48 expands the browser-side threat surface compared with M40 because a secret capable of recovering the bearer now survives page reload in `sessionStorage` for a bounded period. A same-origin script compromise or local process able to read that tab's browser storage during the validity window may steal the reload capability. This is not claimed to be equivalent to pure page-memory bearer handling.

The mitigating boundaries are: tab-scoped storage, five-minute maximum lifetime, active rotation, single use, digest-only server memory, server-restart invalidation, exact same-origin redemption, no ambient cookies, and manual fallback after expiration.

## Non-goals / limitations

M48 does not add cookies, OAuth, remote login, TLS/remote bind, indefinite login sessions, Service Workers, SharedWorkers, localStorage credentials, desktop-shell secrets, OS keychain integration, selected-session persistence, stream cursor persistence, background polling, generic deep links, or multi-user identity.

M48 restores authentication after a normal same-tab reload when a valid capability is present. It does not promise recovery after browser/process restart, server restart, capability expiry, storage clearing, private-mode policy restrictions, or prolonged background suspension.

Selected session and live-stream cursors remain page-memory state and may need to be reselected/rebuilt after reload; M48 concerns credential continuity only.

## Deterministic acceptance

Before freeze, M48 must prove:

- exact frozen M47 base;
- this scope document is the first M48 commit;
- M47 branch/PR remains unchanged and unmerged;
- reload capabilities contain >=256 bits entropy, are digest-only server memory, max five-minute TTL, single-use, bounded-count, expiry-pruned, and server-restart-ephemeral;
- multiple outstanding capabilities can coexist without ordinary cross-tab invalidation;
- authenticated renewal can replace a supplied prior capability;
- invalid prior replacement input cannot prevent an authenticated caller from receiving a new capability;
- `/v1/operator/reload-ticket` requires bearer + same-origin Origin and rejects query/malformed/extra/oversized requests;
- `/v1/operator/reload` requires same-origin Origin and rejects query/malformed/extra/oversized requests;
- rejected Host/origin/request-shape attempts do not consume valid capability;
- expired/used/unknown/malformed capability failures are generic;
- normal App Server routes still reject reload capabilities used as bearer tokens;
- successful redemption returns only the existing bearer and consumes the capability;
- browser stores only reload capability under one exact `sessionStorage` key and never stores bearer;
- browser removes capability before redemption;
- reload redemption waits until existing auth listeners are registered, then reuses `requestSubmit()`;
- M40 bootstrap fragment takes precedence over reload recovery;
- successful manual/bootstrap/reload auth mints/rotates the next reload capability;
- renewal is bounded and stops on lock;
- lock clears local reload capability synchronously;
- reload failure/expiry leaves manual unlock available and does not loop infinitely;
- no localStorage/cookie/IndexedDB/Cache Storage/URL credential surface is introduced;
- existing report/trace/manifest/lifecycle/snapshot clients still capture bearer before `app.js` clears the field;
- M46 stream recovery behavior remains unchanged;
- no App Server store/service/protocol/runtime/task/verifier/model/tool/memory/budget/controller/control authority changes;
- exact M47→M48 diff is confined to reload-auth transport/client/ticket store/routing/tests/docs;
- exact-head Linux CI passes including installed `harness-x --help` and `validate-config`.
