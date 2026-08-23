# Milestone 50 — Immediate Reload-Capability Revocation

M50 is stacked directly on frozen M49 and closes one explicit M48 security limitation: explicit operator lock clears the browser's short-lived reload capability, but the corresponding server-side digest can remain redeemable until its TTL expires.

M50 adds immediate best-effort server revocation for the currently stored reload capability when the operator explicitly locks the UI. The persistent App Server bearer remains page-memory only and remains the sole credential for ordinary operator APIs.

## Scope

M50 adds one authenticated same-origin cleanup endpoint:

`POST /v1/operator/reload-revoke`

The endpoint accepts bounded JSON containing exactly one text `ticket`, accepts no query parameters, requires the persistent bearer, requires exact same-origin `Origin`, and never returns or rotates credential material.

The current M48 in-memory capability store gains one public idempotent `revoke(ticket)` operation that removes a matching unexpired digest if present and otherwise performs a no-op.

The browser's existing M48 lock listener is tightened so it:

1. captures the current page-memory bearer and current stored reload capability;
2. synchronously invalidates local M48 generations, clears page-memory reload-auth state, cancels renewal, and removes the stored capability;
3. only then starts best-effort authenticated same-origin revocation for the captured capability;
4. never restores browser state if the network request fails.

No session, stream, evidence, runtime, verifier, model/tool, memory, budget, controller, or control authority changes in M50.

## Revocation semantics

`ReloadCapabilities.revoke(ticket)`:

- derives only the SHA-256 digest of a text capability;
- prunes expired entries first;
- removes at most one matching outstanding digest under the existing store lock;
- uses constant-time digest comparison consistent with issue/redeem behavior;
- returns whether an entry was removed for internal tests only;
- exposes no stored digests or expiry metadata;
- leaves other tabs' outstanding capabilities unchanged.

The HTTP endpoint is intentionally idempotent at the response boundary. An unknown, expired, already redeemed, or already revoked text ticket receives the same `204 No Content` response as a currently outstanding ticket. The route therefore does not expose a boolean capability-validity result.

Successful response headers are bounded and non-cacheable: `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, connection close, and exact zero content length.

Malformed request shape, missing/cross-origin `Origin`, missing/invalid bearer, query parameters, and oversized/invalid JSON remain fail-closed under the existing App Server error envelope and are rejected before `revoke()` is called.

## Browser lock boundary

The browser clears local state before starting network revocation. Explicit lock therefore remains immediate even when the server is unavailable.

The revocation request uses:

- `POST /v1/operator/reload-revoke`;
- `Authorization: Bearer <captured page-memory token>`;
- `Content-Type: application/json`;
- `Accept: application/json`;
- `credentials: "omit"`;
- `cache: "no-store"`;
- exact body `{ "ticket": <captured capability> }`.

A failed revocation request never recreates the capability, restores the page-memory bearer, or restarts renewal.

M50 does not attempt revocation during ordinary page reload, because the stored capability is precisely what enables M48 reload recovery. It also does not revoke on visibility change, unload, pagehide, or stream disconnect.

No bearer or capability is written to URLs, logs, localStorage, cookies, IndexedDB, Cache Storage, or server durable state.

## In-flight issuance race

Source audit identified a lock/renewal race after the first integrated implementation: an M48 `reload-ticket` request can already be in flight when the operator locks. The server may replace known ticket T with newly issued ticket U after the browser captured only T. Revoking T alone would then leave U outstanding until TTL expiry.

M50 closes the successful-response form of that race using the existing M48 generation boundary rather than adding durable tab identity:

1. lock increments `mintGeneration`, clears the page-memory bearer state, and revokes the currently known ticket;
2. a later successful `reload-ticket` response is stale because its captured generation no longer matches;
3. if that stale response contains a valid M48 ticket, the client clears the response field and best-effort revokes that returned ticket with the bearer captured by the original mint call;
4. the stale ticket is never persisted or made current.

The same stale-response cleanup also prevents overlapping authenticated mint attempts from silently leaving successful superseded tickets outstanding.

There remains an unavoidable best-effort limit: if the server successfully mints U but the transport fails before the browser receives U, the browser does not know U's value and cannot target it for revocation. M48's five-minute TTL, single-use semantics, outstanding-count bound, and process-restart invalidation remain the fallback for that case.

## Multi-tab boundary

M48 permits multiple outstanding capabilities so multiple operator tabs can coexist. M50 revokes only the exact capability captured from the tab being explicitly locked, plus any successful stale replacement returned to that tab's invalidated mint generation.

Locking tab A must not revoke tab B's independent outstanding capability. Browser tab duplication can temporarily copy the same capability; revoking or redeeming that shared capability invalidates it for all copies, which is consistent with M48's single-use semantics.

M50 does not add a global revocation namespace, tab identifier, revocation list, or cross-tab synchronization channel.

## Layering / compatibility boundary

The route is implemented in an M50 `LocalOperatorHTTPServer` subclass layered over the frozen M48 transport. Existing `/v1/operator/reload-ticket` and `/v1/operator/reload` handling remains inherited unchanged.

The public App Server package and `harness-x-app-server` CLI change only their `LocalOperatorHTTPServer` import to the M50 subclass. M49's browser-only selection restoration remains unchanged.

The existing M48 browser client is tightened in place because explicit-lock capability ownership already lives there; M50 does not add a second credential-state machine or a new browser storage key.

## Authority boundary

M50 changes credential cleanup only. It does **not**:

- allow a reload capability to authenticate ordinary App Server APIs;
- allow an unauthenticated holder of a reload capability to revoke other state;
- mint or redeem capabilities through the revocation endpoint;
- persist the bearer or capability server-side beyond M48's existing digest/TTL store;
- mutate sessions, lifecycle events, report/trace/snapshot/manifest evidence, verifier outcomes, runtime state, memory, model/tool behavior, budgets, controller, or control policy;
- add remote access, user identity, roles, cookies, OAuth, or multi-user authorization.

The persistent bearer remains the sole credential authorizing the revocation request and every ordinary operator API.

## Non-goals / limitations

M50 cannot guarantee revocation if the browser/process disappears before a cleanup request is transmitted, or if a successful server-side issuance loses its response before the browser learns the new ticket value. Existing M48 TTL/one-time/restart protections remain the fallback.

M50 does not add a global "revoke all tabs" operation, enumerate outstanding capabilities, expose capability counts over HTTP, add durable revocation state, or persist tab identity.

M50 does not change M49 selected-session restoration or any lifecycle/trace stream cursor behavior.

## Deterministic acceptance

Before freeze, M50 must prove:

- exact frozen M49 base `128b8b9300b08d8a5fc4f0095f301f65e532803c`;
- this scope document is the first M50 commit;
- M49 PR #56 remains unchanged, draft, open, and unmerged;
- `ReloadCapabilities.revoke()` removes one matching unexpired digest and leaves unrelated digests intact;
- revoke is idempotent for unknown, expired, redeemed, and already-revoked text capabilities;
- the route is exactly `POST /v1/operator/reload-revoke`;
- query parameters are rejected before revocation;
- exact same-origin `Origin` and the persistent bearer are required before revocation;
- malformed/oversized request bodies are rejected before revocation;
- valid unknown/already-consumed tickets receive the same HTTP status/body as valid outstanding tickets;
- a revoked capability can no longer redeem through `/v1/operator/reload`;
- a different tab's capability remains redeemable after revoking the first;
- browser lock removes local capability and page-memory reload-auth state before starting network cleanup;
- browser lock uses the captured bearer only in the Authorization header and the captured capability only in the JSON body;
- failed revocation never restores local credential state or blocks explicit lock;
- a successful mint response made stale by lock is immediately best-effort revoked and never persisted;
- no revocation is attempted on ordinary reload/unload/pagehide;
- M48 issuance/redemption/renewal and M49 selection restoration remain compatible;
- no backend session/store/service/protocol/runtime/evidence/verifier/model/tool/memory/budget/controller/control authority changes;
- exact M49→M50 diff remains narrow and source-audited;
- exact-head Linux CI passes including installed `harness-x --help` and `harness-x validate-config configs/default.yaml`.
