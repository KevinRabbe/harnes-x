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

`ReloadCapabilities.revoke(ticket)` must:

- derive only the SHA-256 digest of a text capability;
- prune expired entries first;
- remove at most one matching outstanding digest under the existing store lock;
- use constant-time digest comparison consistent with issue/redeem behavior;
- return whether an entry was removed for internal tests only;
- expose no stored digests or expiry metadata;
- leave other tabs' outstanding capabilities unchanged.

The HTTP endpoint is intentionally idempotent from the caller's perspective. A syntactically valid text ticket that is unknown, expired, already redeemed, or already revoked receives the same successful no-content response as a currently outstanding ticket. This avoids turning the route into a capability-validity oracle.

Malformed request shape, missing/cross-origin `Origin`, missing/invalid bearer, query parameters, and oversized/invalid JSON remain fail-closed under the existing App Server error envelope.

## Browser lock boundary

The browser must clear local state before awaiting network revocation. Explicit lock therefore remains immediate even when the server is unavailable.

Revocation is best-effort because a browser/process crash, abrupt tab close, network failure, or storage failure can prevent the cleanup request. In those cases M48's existing one-time/TTL/server-restart protections remain the fallback boundary.

M50 does not attempt revocation during ordinary page reload, because the stored capability is precisely what enables M48 reload recovery. It also does not revoke on every visibility change, unload event, or stream disconnect.

The request uses:

- `POST /v1/operator/reload-revoke`;
- `Authorization: Bearer <page-memory token>`;
- `Content-Type: application/json`;
- `Accept: application/json`;
- `credentials: "omit"`;
- `cache: "no-store"`;
- exact body `{ "ticket": <captured capability> }`.

No bearer or capability is written to URLs, logs, localStorage, cookies, IndexedDB, Cache Storage, or server durable state.

## Multi-tab boundary

M48 permits multiple outstanding capabilities so multiple operator tabs can coexist. M50 revokes only the exact capability captured from the tab being explicitly locked.

Locking tab A must not revoke tab B's independent outstanding capability. Browser tab duplication can temporarily copy the same capability; revoking or redeeming that shared capability invalidates it for all copies, which is consistent with M48's single-use semantics.

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

M50 cannot guarantee revocation if the browser or process disappears before the request is transmitted. The existing short TTL and one-time redemption semantics remain the fallback.

M50 does not add a global "revoke all tabs" operation, enumerate outstanding capabilities, expose capability counts over HTTP, or add durable revocation state.

M50 does not change M49 selected-session restoration or any stream cursor behavior.

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
- valid unknown/already-consumed tickets are indistinguishable from valid outstanding tickets at the HTTP response boundary;
- a revoked capability can no longer redeem through `/v1/operator/reload`;
- a different tab's capability remains redeemable after revoking the first;
- browser lock removes local capability and page-memory reload-auth state before starting network cleanup;
- browser lock uses the captured bearer only in the Authorization header and the captured capability only in the JSON body;
- failed revocation never restores local credential state or blocks explicit lock;
- no revocation is attempted on ordinary reload/unload;
- M48 issuance/redemption/renewal and M49 selection restoration remain compatible;
- no backend session/store/service/protocol/runtime/evidence/verifier/model/tool/memory/budget/controller/control authority changes;
- exact M49→M50 diff remains narrow and source-audited;
- exact-head Linux CI passes including installed `harness-x --help` and `harness-x validate-config configs/default.yaml`.
