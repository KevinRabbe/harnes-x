# Milestone 48 — Reload-Resilient Operator Reauthentication

M48 is stacked directly on frozen M47 and closes one explicit operator-availability limitation left by M40/M46: the operator bearer is intentionally kept only in page memory, so a full browser reload loses authentication and otherwise requires a fresh manual token paste or a new `--open-ui` launch.

M48 adds bounded reload reauthentication **without persisting the long-lived App Server bearer**. It introduces one short-lived, single-use opaque reload capability stored only in tab-scoped `sessionStorage`. That capability is accepted only by one exact same-origin redemption endpoint to recover the existing bearer. The recovered bearer is then fed through the already-qualified auth-form listener chain and returns to page-memory-only handling.

This milestone deliberately changes one M40 non-goal: `sessionStorage` may now contain the opaque reload capability. It still must never contain the persistent bearer, report/trace/evidence bytes, or task/runtime state. M48 therefore expands the browser-side threat surface relative to M40, and that expansion is explicit rather than being described as equivalent to pure page-memory authentication.

## Frozen-parent boundary

M48 is based exactly on frozen M47:

```text
f5aace96c883bcb2bd9c88a9b9ed758adba57402
```

The M47 branch/PR is not modified or merged. M48 layers a new operator-server subclass over the frozen M47 server and leaves inherited M47 snapshot export, M45 lifecycle export, M43 evidence manifest, M42 trace export, M41 report export, M40 bootstrap, and M46 stream-recovery implementations unchanged.

## New operator endpoints

M48 adds exactly two POST endpoints:

```text
POST /v1/operator/reload-ticket
POST /v1/operator/reload
```

No caller-selected path, session, role, credential scope, TTL, storage key, or serialization option is accepted.

### `POST /v1/operator/reload-ticket`

Purpose: mint/rotate the short-lived reload capability while the current page already possesses the normal App Server bearer.

Requirements:

- inherited literal-loopback Host validation;
- no query parameters;
- exact same-origin browser `Origin` matching Host;
- the existing persistent bearer in `Authorization: Bearer ...`;
- inherited bounded JSON body parsing;
- exact JSON object shape:

```json
{"previous_ticket": null}
```

or

```json
{"previous_ticket": "<opaque prior capability>"}
```

Unexpected/missing fields or non-string/non-null `previous_ticket` fail before issuance.

Successful response:

```json
{
  "schema_version": "app-operator-reload-ticket-v1",
  "ticket": "<opaque capability>"
}
```

The inherited JSON transport applies `Cache-Control: no-store` and exact content length. The response never includes the persistent bearer.

A pathological failure to generate a unique capability after the bounded retry count returns structured `503 reload_unavailable` rather than exposing an unhandled server exception.

### `POST /v1/operator/reload`

Purpose: redeem one unexpired single-use reload capability after a normal browser reload.

Requirements:

- inherited literal-loopback Host validation;
- no query parameters;
- exact same-origin browser `Origin` matching Host;
- inherited bounded JSON body parsing;
- exact JSON object shape:

```json
{"ticket": "<opaque capability>"}
```

No bearer/cookie/ambient credential is used for redemption.

A successful redemption consumes the capability and returns:

```json
{
  "schema_version": "app-operator-reload-v1",
  "access_token": "<existing App Server bearer>"
}
```

The response is `Cache-Control: no-store`. The bearer is returned only because a valid same-origin one-time reload capability was redeemed; the capability itself is never accepted as the bearer for ordinary App Server APIs.

Unknown, malformed-text, expired, or already-used capabilities all collapse to the same generic:

```text
401 reload_rejected
```

Request-shape, Host, Origin, or query rejection occurs before capability consumption.

## Reload capability store

`ReloadCapabilities` is process-memory-only and stores no raw capability after issuance.

Frozen constants:

```text
random input bytes:       32 bytes (>=256 bits)
default/max TTL:          300 seconds
browser renewal interval: 120 seconds
network retry interval:   30 seconds
default outstanding cap:  32 digests
constructor hard cap:     64 digests
unique-generation tries:  8
```

Each capability is generated with `secrets.token_urlsafe(32)` and represented server-side only by:

```text
SHA-256(capability ASCII bytes) + monotonic expiration
```

The raw capability is not written to disk, session/lifecycle state, server-info metadata, URLs, or logs by M48.

Properties:

- one-time redemption;
- exact expiry at/after the monotonic deadline;
- expiry pruning before issuance/redemption/count inspection;
- bounded outstanding digest count;
- multiple independent tickets can coexist for ordinary multi-tab use;
- server restart drops the complete reload-capability set while the existing durable bearer remains unchanged;
- invalid `previous_ticket` input cannot block an otherwise authenticated issuance;
- a supplied valid prior ticket is retired only when a unique replacement has successfully been generated;
- if unique generation fails, the old still-unexpired prior ticket remains valid;
- generated digest collisions with the supplied prior ticket or any outstanding ticket are rejected and regenerated;
- capacity overflow evicts the earliest-expiring outstanding digest, preserving a hard server-memory bound.

The collision rules are intentionally explicit even though a 256-bit random collision is extraordinarily unlikely. Without them, duplicate digest entries could make the same raw capability redeemable more than once in the collision case.

## Browser storage boundary

The exact persistent browser key introduced by M48 is:

```text
harness-x.operator.reload-ticket.v1
```

Only the reload capability may be stored under that key in `sessionStorage`.

The following remain forbidden:

- persistent bearer in `sessionStorage`;
- bearer in `localStorage`, IndexedDB, Cache Storage, cookie, URL, fragment, or query;
- reload capability in `localStorage`, IndexedDB, Cache Storage, cookie, URL, fragment, or query;
- evidence/report/trace/lifecycle/snapshot bytes in browser persistent storage;
- a second long-lived bearer or durable refresh credential;
- durable server-side browser-login state.

M48 does not add Service Workers or SharedWorkers.

### `sessionStorage` duplication nuance

Browsers generally scope `sessionStorage` to a top-level browsing context, but a newly duplicated/opened tab may initially receive a copy of the opener's `sessionStorage` depending on browser behavior. M48 therefore does **not** claim that a reload capability can never be copied client-side.

The server-side capability remains single-use. If two tab contexts temporarily hold the same copied capability, only the first successful redemption can recover the bearer; the other copy becomes rejected. Once each authenticated tab performs normal capability issuance/renewal, independent outstanding digests can coexist.

## Manual/bootstrap authentication flow

M48 loads `/ui/reload_auth.js` before `app.js` so its auth-submit listener sees the token before the existing main listener synchronously clears the password field.

Final qualified listener/script ordering is intended to remain:

```text
stream_policy.js
report.js
report_export.js
trace_export.js
evidence_manifest.js
lifecycle_export.js
snapshot_export.js
reload_auth.js
app.js
stream_recovery.js
bootstrap.js
```

Therefore every pre-M48 report/evidence/snapshot client still captures the same bearer before `app.js` clears the field.

On a manual or M40-bootstrap submit:

1. existing listeners capture their page-memory bearer copies as before;
2. M48 increments its auth generation and copies the bearer into M48 page memory;
3. M48 starts authenticated same-origin reload-ticket issuance;
4. `app.js` captures the bearer and clears the password field as before;
5. successful issuance stores only the opaque reload capability;
6. M48 schedules renewal while the tab stays unlocked.

An invalid submitted bearer cannot mint a reload capability because `/reload-ticket` independently requires the real persistent bearer.

## Normal reload recovery

At script evaluation M48 records whether the page initially contained an M40 `#bootstrap=...` fragment. This is captured **before** `bootstrap.js` later scrubs the fragment, so an explicit fresh `--open-ui` bootstrap cannot race stored reload recovery.

On an ordinary reload with no fresh bootstrap fragment:

1. read the exact `sessionStorage` reload-capability key;
2. locally reject/remove malformed values;
3. remove the valid capability from `sessionStorage` **before** the redemption request;
4. wait until `DOMContentLoaded`, ensuring all deferred auth listeners are registered;
5. POST the ticket to `/v1/operator/reload` with `credentials: "omit"` and `cache: "no-store"`;
6. validate `app-operator-reload-v1` and a non-empty returned bearer;
7. generation-check the response so a newer manual auth attempt wins over stale reload completion;
8. put the bearer into the existing password field only long enough to synchronously call `authForm.requestSubmit()`;
9. the normal qualified listeners capture it and `app.js` clears the password field;
10. M48's own submit listener mints the next reload capability;
11. temporary response/DOM bearer references are cleared as far as JavaScript permits.

There is no infinite reload retry loop. A failed/expired redemption leaves the existing manual token form available.

## Active capability renewal

A 300-second ticket issued only once would expire during a long-running operator tab, so M48 rotates it every 120 seconds while its page-memory bearer remains present.

Renewal sends the currently stored old ticket, if any, only to the bearer-authenticated `/reload-ticket` endpoint. Server-side replacement is atomic as described above.

Browser failure behavior is deliberately split:

- network/transport failure retains the currently stored ticket and schedules another issuance attempt after 30 seconds;
- an HTTP/schema/invalid-ticket issuance response clears the stored ticket and stops renewal;
- issuance `401` also clears M48's page-memory bearer;
- inability to write `sessionStorage` clears local capability state and stops renewal.

This avoids destroying a still-valid ticket merely because one network request failed while still failing closed on an explicit server rejection.

Background-tab throttling or prolonged suspension can still allow the 300-second capability to expire before renewal. M48 provides bounded active-tab reload resilience, not an indefinite login session.

## Lock behavior

The M48 lock listener runs through the existing `lock-button` action and synchronously:

- increments auth/mint generations;
- clears M48's page-memory bearer reference;
- cancels its renewal timer;
- removes the `sessionStorage` capability.

M48 intentionally does not add a separate revocation endpoint. An issuance request already in flight when lock occurs can leave an unreachable server-side digest until its short expiry; generation guards prevent the response from restoring it locally. That residual maximum lifetime is part of the explicit bounded-threat model.

## Browser security behavior

`reload_auth.js` uses:

- exact same-origin relative endpoints;
- `credentials: "omit"` for issuance and redemption;
- `cache: "no-store"`;
- exact ticket shape validation (`43` URL-safe characters for the 32-byte token-url-safe representation);
- auth/mint generation counters for stale-result suppression;
- one bounded renewal timer;
- `textContent` for reload failure display;
- no `localStorage`, cookies, IndexedDB, Cache Storage, Service Worker, SharedWorker, query parameter, or URL credential surface.

The existing M40 bootstrap fragment remains the explicit-launch precedence path.

## Same-origin / request-shape boundary

Both endpoints inherit literal-loopback Host enforcement and require exact HTTP same-origin `Origin` matching the request Host.

Both reject query parameters and bodies larger than the inherited App Server JSON limit. Exact field sets are required after parsing.

Tests prove that missing/cross-origin Origin, query rejection, extra fields, and oversized bodies cannot consume an already valid reload capability.

## Authority boundary

M48 changes only local operator credential continuity across browser reload.

It does **not**:

- bypass the persistent bearer requirement on ordinary stateful APIs;
- make reload capabilities valid normal API bearer tokens;
- create/cancel/retry/resume Harness X tasks;
- change App Server session/lifecycle store or service authority;
- change report/trace/evidence provenance;
- alter M46 stream cursor/recovery policy;
- execute models or tools;
- change runtime/verifier completion decisions;
- mutate memory, budgets, controller, or control policy;
- grant remote network access;
- introduce user identities, roles, sessions, or authorization policy.

The persistent App Server bearer remains the sole credential for ordinary operator APIs. The reload capability is only a bounded one-time bridge to recover that bearer at one exact same-origin endpoint.

## Security limitation

M48 has a strictly larger browser-side secret lifetime than M40. A same-origin script compromise or local actor able to read the tab's `sessionStorage` during the validity window can steal a reload capability and race to redeem it for the bearer. The same-origin requirement does not protect against a same-origin script compromise.

Mitigations are bounded, not absolute:

- 300-second maximum lifetime;
- 120-second active rotation;
- one-time server redemption;
- digest-only process memory;
- collision/duplicate rejection;
- atomic replacement;
- server-restart invalidation;
- exact same-origin endpoint;
- no ambient cookies;
- remove-before-redeem client behavior;
- manual fallback after failure/expiry.

M48 is therefore a usability/security tradeoff and must not be described as preserving M40's exact page-memory-only browser threat surface.

## Non-goals / remaining limitations

M48 does not add cookies, OAuth, remote login, TLS/remote bind, indefinite login sessions, Service Workers, SharedWorkers, localStorage credentials, OS keychain integration, desktop-shell secret storage, selected-session persistence, stream cursor persistence, running-session state restoration, generic deep links, or multi-user identity.

M48 does not promise automatic recovery after:

- browser/process restart;
- App Server restart;
- capability expiry;
- storage clearing/restriction;
- prolonged background throttling;
- copied-tab one-time capability losing a redemption race.

Selected session and live-stream cursors remain page-memory state and may need to be reselected/rebuilt after a full reload. M48 concerns credential continuity only.

## Source-audit findings kept fail-visible

Two security hardenings were added after the first integrated candidate:

1. **Capability digest collision:** the initial bounded store allowed duplicate generated digests. Although a 256-bit collision is extraordinarily unlikely, duplicate entries could make one raw ticket redeemable twice. M48 now regenerates on collision with any outstanding or prior digest and fails closed after a bounded number of attempts.
2. **Non-atomic rotation failure:** the first collision fix retired `previous_ticket` before successfully generating its replacement. A pathological RNG failure could therefore destroy an otherwise usable prior capability. Rotation now generates/validates the replacement first, then retires the old digest atomically. Regression coverage proves failed rotation preserves the prior ticket.

These are source-audit findings, not hidden behind green CI.

## Deterministic acceptance

Before freeze, M48 must prove:

- exact frozen M47 base and first-commit scope document;
- M47 remains unchanged/unmerged;
- >=256-bit cryptographic capability generation;
- digest-only server memory, 300-second max TTL, expiry pruning, single-use redemption, bounded outstanding count, and server-restart invalidation;
- generated/prior/outstanding digest collision rejection with bounded retries;
- failed atomic rotation preserves the still-valid prior ticket;
- independent multiple outstanding tickets and bounded-capacity behavior;
- authenticated prior-ticket replacement and invalid-prior tolerance;
- bearer + same-origin requirement for `/reload-ticket`;
- same-origin requirement for `/reload`;
- exact JSON shapes, no query parameters, inherited bounded bodies;
- rejected Origin/query/shape/oversized requests do not consume a valid ticket;
- malformed-text/unknown/expired/used tickets share generic `reload_rejected`;
- pathological capability-generation failure returns structured `reload_unavailable`;
- reload ticket cannot authorize normal App Server routes;
- redemption returns the existing bearer once and consumes the ticket;
- browser persists only one exact reload capability key and never persists the bearer;
- remove-before-redeem ordering;
- bootstrap-fragment precedence captured before M40 fragment scrubbing;
- `DOMContentLoaded` restoration after listener registration;
- auth-generation suppression of stale reload completion;
- `requestSubmit()` reuse of the qualified auth chain;
- successful manual/bootstrap/reload auth rotates the next capability;
- 120-second renewal and 30-second network retry behavior;
- lock clears local capability and timer synchronously;
- no localStorage/cookie/IndexedDB/Cache Storage/Service Worker/SharedWorker/URL credential surface;
- existing report/trace/manifest/lifecycle/snapshot auth listener ordering preserved;
- M46 stream recovery remains unchanged;
- no App Server store/service/protocol/runtime/task/verifier/model/tool/memory/budget/controller/control authority changes;
- exact M47→M48 diff confined to reload-auth transport/store/client/routing/tests/docs;
- exact-head Linux CI passes with installed `harness-x --help` and `validate-config`.
