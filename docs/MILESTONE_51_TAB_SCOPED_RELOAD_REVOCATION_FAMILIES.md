# Milestone 51 — Tab-Scoped Reload Revocation Families

M51 is stacked directly on frozen M50 and closes the remaining successful-issuance race that M50 documents explicitly: if the App Server creates a replacement reload capability but the transport fails before the browser learns that capability value, ticket-by-value cleanup cannot target the unknown replacement.

M51 introduces a browser-generated, tab-scoped reload **family identifier** that is known before issuance, is not itself an authentication credential, and is persisted only in `sessionStorage` alongside M48's reload capability. Family-aware issuance and family revocation share one in-process store lock, so an explicit lock can retire a tab's entire reload lineage even when the current ticket value was never delivered to the browser.

The persistent App Server bearer remains page-memory only and remains the sole credential authorizing ordinary operator APIs and family cleanup.

## Scope

M51 adds two authenticated same-origin operator endpoints layered over frozen M50:

- `POST /v1/operator/reload-family-ticket`
- `POST /v1/operator/reload-family-revoke`

The existing M48 `/v1/operator/reload-ticket` and `/v1/operator/reload` routes and the M50 `/v1/operator/reload-revoke` route remain available and unchanged for compatibility. The M51 browser client uses the family-aware issuance route and uses family revocation in addition to M50's known-ticket cleanup on explicit lock.

No session, stream, evidence, runtime, verifier, model/tool, memory, budget, controller, or control authority changes in M51.

## Family identifier

The browser creates one family identifier before the first family-aware issuance for a tab:

- 32 random bytes from `crypto.getRandomValues`;
- base64url without padding;
- exactly 43 ASCII characters matching `[A-Za-z0-9_-]{43}`;
- stored only in tab-scoped `sessionStorage` under `harness-x.operator.reload-family.v1`;
- never placed in a URL, cookie, localStorage, IndexedDB, Cache Storage, log, lifecycle event, session record, report, trace, manifest, snapshot, or other durable App Server artifact.

The family identifier is a revocation namespace, not an authentication capability. Possession of a family identifier alone cannot mint, redeem, revoke, or authorize anything: issuance and revocation require the persistent bearer and exact same-origin request checks; redemption still requires the one-time reload ticket itself.

A normal page reload preserves the family identifier so the newly redeemed bearer can rotate the next ticket inside the same family. Explicit operator lock removes both the ticket and family identifier from browser storage before network cleanup begins.

Browser tab duplication can copy `sessionStorage`; copied tabs may therefore temporarily share the same family exactly as M48/M50 already allow them to share a copied one-time ticket. Once either copy rotates or revokes that family, the shared lineage is affected for both copies. M51 does not add cross-tab identity or synchronization.

## Family-aware store model

`ReloadCapabilities` retains its existing ticket digest list and adds bounded process-memory-only family metadata:

- a mapping from family-aware ticket digest to family digest;
- a bounded family registry recording whether each observed family is active or revoked.

Only SHA-256 digests of ticket/family values are retained server-side. Raw family identifiers are not retained after request handling.

The default/max family registry bound is deliberately finite. Revoked families remain tombstoned for the lifetime of the App Server process rather than expiring on the capability TTL. This gives a simple ordering guarantee for delayed in-process issuance: once a family is revoked, no later family-aware issuance for that family can succeed before process restart. Process restart already invalidates every outstanding reload ticket, so retaining revocation tombstones beyond restart is unnecessary.

If the family registry reaches its configured hard bound, new family registration fails visibly rather than evicting a revocation tombstone and silently reopening an issuance-after-lock race. Existing ordinary bearer authority remains usable; manual authentication remains possible even if reload-family issuance is unavailable.

## Family-aware issuance

`POST /v1/operator/reload-family-ticket`:

- accepts no query parameters;
- requires exact same-origin `Origin`;
- requires the persistent App Server bearer;
- accepts bounded JSON containing exactly `previous_ticket` (text or null) and `family` (canonical 43-character family id);
- returns only the newly issued one-time reload ticket under a distinct M51 schema;
- uses `Cache-Control: no-store` and the inherited bounded JSON/error envelope.

Under the store lock, `issue_for_family(previous_ticket, family)` must:

1. reject a family already tombstoned as revoked;
2. register a previously unseen family only if bounded registry capacity remains;
3. prune expired ticket entries;
4. remove the supplied previous ticket if still outstanding;
5. remove any other currently outstanding ticket already associated with the same family;
6. generate one fresh non-colliding M48-format ticket;
7. associate only that new ticket digest with the family digest;
8. retain all other families' tickets unchanged.

There is therefore at most one current family-aware ticket per family after a successful issuance. Retrying the same family after a response-loss event atomically replaces the unknown prior family ticket instead of accumulating multiple unknown credentials.

The legacy M48 `issue()` behavior remains available to the legacy route and retains its established semantics.

## Family revocation

`POST /v1/operator/reload-family-revoke`:

- accepts no query parameters;
- requires exact same-origin `Origin`;
- requires the persistent App Server bearer;
- accepts bounded JSON containing exactly one canonical text `family` field;
- never mints, redeems, rotates, or returns credential material;
- returns the same `204 No Content` success for a registered active family, an already-revoked family, or a previously unseen family when registry capacity permits;
- uses `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, connection close, and exact zero content length.

Under the same store lock used by family issuance, `revoke_family(family)` must atomically:

1. register/tombstone the family as revoked, or leave an existing tombstone in place;
2. remove every current ticket mapped to that family;
3. leave every unrelated family and legacy ungrouped ticket unchanged.

This intentionally supports revocation-before-issuance. A family-revoke request that acquires the lock first tombstones the family, so a delayed in-flight family issuance cannot subsequently create a ticket. If issuance acquires the lock first, revocation subsequently removes the issued ticket and tombstones the family. Either lock ordering leaves no redeemable ticket in that family once successful family revocation completes.

Malformed request shape, invalid family syntax, missing/cross-origin `Origin`, missing/invalid bearer, query parameters, oversized/invalid JSON, and exhausted family-registry capacity fail visibly before reporting successful cleanup.

## Browser lock boundary

M51 tightens the existing `reload_auth.js` state machine rather than introducing a second credential client.

On authentication, the browser ensures one canonical family id exists before family-aware issuance. On successful reload redemption, the same family remains available so the recovered bearer rotates a new ticket in the same lineage.

On explicit lock the browser:

1. captures the page-memory bearer, current stored ticket, and current stored family;
2. advances the existing auth/mint generations;
3. clears the page-memory bearer state;
4. cancels renewal;
5. removes the stored ticket and family synchronously;
6. starts M50 ticket-by-value revocation for the known ticket when present;
7. starts M51 family revocation for the captured family when present;
8. never restores local credential state if either cleanup request fails.

The M50 ticket cleanup remains useful as a compatibility/fallback path. Family cleanup is what closes the unknown-ticket response-loss race when its request reaches the server.

No family revocation is attempted during ordinary reload, unload, pagehide, visibility change, stream disconnect, or session selection changes.

## Response-loss and retry semantics

M51 specifically closes successful server-side issuance whose ticket response is lost while the tab/process remains available:

- the browser knew the family before sending the request;
- a transport exception leaves that family in `sessionStorage` and schedules the existing bounded renewal retry;
- an issuance retry with that same family replaces any unknown prior family ticket atomically;
- explicit lock can revoke the family even if it never learned the current ticket;
- revocation-before-issuance is safe because the family tombstone blocks delayed issuance;
- issuance-before-revocation is safe because family revocation removes the issued ticket.

A successful HTTP response whose body is unreadable, has the wrong schema, or otherwise fails the M51 ticket-response contract is handled differently from a transport exception because the server may already have issued a ticket. For a current-generation successful-but-invalid response, the browser clears local ticket/family state, cancels renewal, revokes any canonical ticket value it did receive through M50's exact-ticket route, and best-effort revokes the known family. Ordinary bearer authority remains page-memory available; only reload recovery for that retired family is disabled.

Generation-stale successful responses are intentionally **ticket-scoped**, not family-scoped. A stale response can mean an older overlapping issuance was superseded by a newer valid issuance in the same family, not only that the operator locked. Therefore the browser revokes the exact stale returned ticket through M50 but does not independently tombstone the family from the stale-response branch. Explicit lock already sends the authoritative family-revoke request after clearing local state.

M51 additionally reconciles adverse same-family response ordering. Because the store permits exactly one current ticket per family, an older browser request can be processed last by the server after a newer browser generation has already stored its response. When a stale successful response arrives while the **same bearer and same family are still current**, the browser coalesces an immediate zero-delay family-aware renewal after exact stale-ticket cleanup. That renewal rotates whatever ticket is actually server-current and restores one browser-current ticket before returning to the normal two-minute cadence. If lock or a family change caused the stale response, reconciliation is suppressed because the original bearer/family are no longer current.

M51 still cannot guarantee network cleanup if the browser/process disappears before family revocation is transmitted or if the family-revoke request itself never reaches the server. In those cases M48's five-minute ticket TTL, single-use redemption, bounded outstanding-ticket set, and process-restart invalidation remain the fallback. M51 does not claim crash-proof or network-independent revocation.

## Multi-tab boundary

Independent tabs with independently generated families remain isolated. Revoking family A must not revoke family B's ticket.

A duplicated tab may copy both family and ticket from the source tab. Such copies intentionally share one revocation lineage until one copy establishes a new family through explicit lock followed by later authentication. M51 does not add BroadcastChannel, SharedWorker, Service Worker, cookies, server-side tab identity, or a global revoke-all-tabs operation.

## Layering / compatibility boundary

M51 uses a new `LocalOperatorHTTPServer` subclass layered over frozen M50. It intercepts only the two M51 family routes and delegates every other route to the frozen M50 stack.

The public App Server package and `harness-x-app-server` CLI change only their `LocalOperatorHTTPServer` import to the M51 subclass.

The existing M48/M50 browser `reload_auth.js` is tightened in place because it already owns the reload credential lifecycle. M49 selected-session restoration and M46 stream recovery remain unchanged.

## Authority boundary

M51 changes reload-credential cleanup and grouping only. It does **not**:

- make a family identifier an authentication credential;
- allow family/ticket holders without the persistent bearer to issue or revoke family credentials;
- allow a reload ticket or family id to authenticate ordinary App Server APIs;
- persist the bearer server-side beyond the existing access-token file or in browser storage;
- add durable family/revocation records across App Server restart;
- mutate sessions, lifecycle events, report/trace/lifecycle/snapshot/manifest evidence, verifier outcomes, runtime state, memory, models/tools, budgets, controller, or control policy;
- add remote access, user accounts, roles, cookies, OAuth, or multi-user authorization.

The persistent bearer remains the sole credential authorizing issuance/revocation requests and all ordinary operator APIs.

## Source-audit hardening

The first integrated candidate passed CI but was not frozen because source audit found a successful-response ambiguity: a `200 OK` with an unreadable or invalid body can still follow server-side issuance. M51 therefore retires the current family on an invalid successful current-generation response rather than treating that case like a simple non-success response.

A later audit found an overcorrection: family-revoking every generation-stale successful response can invalidate a newer valid ticket in the same family when two issuance attempts overlap. The client was narrowed to ticket-only stale cleanup. A final compatibility audit then found that one-current-ticket family semantics plus adverse server/response ordering could leave the browser holding a superseded ticket after stale cleanup. The final client therefore schedules same-family reconciliation only while that original bearer/family remain current. These findings are preserved fail-visibly rather than treating earlier green CI as a freeze gate.

## Non-goals / limitations

M51 does not guarantee cleanup after browser/process crash or a cleanup request that never reaches the loopback server.

M51 does not globally revoke every tab, enumerate families/tickets, expose family validity or counts over HTTP, persist revocation state across process restart, or synchronize duplicated tabs.

M51 does not change M49 selected-session restoration, M46 cursor/reconnect behavior, evidence exports, or task/runtime authority.

## Deterministic acceptance

Before freeze, M51 must prove:

- exact frozen M50 base `128d4d2b56dc8fa44d3cdf6169eb0252fc5aefa4`;
- this scope document is the first M51 commit;
- M50 PR #57 remains unchanged, draft, open, and unmerged;
- family identifiers are 32-byte browser-random base64url values and persist only in tab-scoped `sessionStorage`;
- a family id alone cannot authorize issuance, revocation, redemption, or ordinary App Server APIs;
- `issue_for_family()` maintains at most one current family ticket and leaves unrelated families unchanged;
- a retry with the same family replaces an unknown prior family ticket;
- `revoke_family()` removes the family's current ticket, leaves unrelated families unchanged, and permanently tombstones that family for the server process lifetime;
- revoke-before-issue blocks later family issuance and issue-before-revoke leaves no family ticket after revocation;
- family registry exhaustion fails visibly without evicting revocation tombstones;
- family-aware issuance is exactly `POST /v1/operator/reload-family-ticket` with bearer + same-origin enforcement and exact request shape;
- family revocation is exactly `POST /v1/operator/reload-family-revoke` with bearer + same-origin enforcement, exact request shape, and idempotent 204 success when capacity permits;
- query parameters, malformed/oversized bodies, invalid family syntax, invalid origin, and invalid bearer are rejected before family mutation;
- legacy M48 issuance/redemption and M50 ticket revocation remain compatible;
- browser lock clears bearer/ticket/family locally before network cleanup;
- browser lock performs family cleanup even when no current ticket value is known;
- a current-generation invalid successful issuance response retires the family and exact returned ticket when available;
- a generation-stale successful issuance response cannot tombstone a family that may contain a newer valid ticket;
- a stale successful response for the still-current bearer/family schedules an immediate family-aware reconciliation, while lock/family changes suppress reissue;
- ordinary reload preserves the family and performs no family revocation;
- failed cleanup never restores local credential state or blocks explicit lock;
- no backend session/store/service/protocol/runtime/evidence/verifier/model/tool/memory/budget/controller/control authority changes;
- exact M50→M51 diff remains narrow and source-audited;
- exact-head Linux CI passes including installed `harness-x --help` and `harness-x validate-config configs/default.yaml`.
