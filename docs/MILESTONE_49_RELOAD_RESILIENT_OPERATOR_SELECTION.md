# Milestone 49 — Reload-Resilient Operator Selection

M49 is stacked directly on frozen M48 and closes the next explicit operator-continuity gap: M48 can recover authentication after a same-tab browser reload, but the selected Harness X session remains page-memory state and must be manually reselected.

M49 restores only the selected session identifier. It does **not** persist stream cursors, lifecycle/trace evidence, snapshots, report/trace bytes, task state, or the persistent bearer. After successful unlock, the existing frozen operator selection path refetches the authoritative session snapshot and evidence pages from the App Server and derives fresh stream cursors from those server responses before starting SSE.

## Scope

M49 adds one exact-allowlisted browser client layered over the frozen M48 UI.

The client may persist exactly one additional non-secret value in tab-scoped `sessionStorage`:

```text
selected app-session id
```

The value must match the canonical `app_[0-9a-f]{32}` identifier shape. Malformed values are removed and never used for a request.

No backend route, App Server store/service/protocol/runtime state, evidence format, session schema, stream protocol, report/trace authority, verifier, model/tool execution, memory, budget, controller, or control-policy implementation changes in M49.

## Restore contract

On ordinary session selection:

1. the existing `selectSession(sessionId)` path remains authoritative;
2. M49 records only the canonical selected session id in one exact tab-scoped `sessionStorage` key;
3. no cursor, snapshot, event, trace record, task text, workspace path, or evidence payload is persisted by M49.

On successful operator unlock:

1. the frozen M48/M40 auth listener chain completes normally;
2. the existing `unlockOperator(token)` loads the authoritative session list;
3. M49 reads the persisted selected session id only after that successful load;
4. if the id is still present in the loaded session map, M49 invokes the existing `selectSession()` path;
5. that path refetches `/v1/sessions/{id}`, reloads lifecycle and trace pages from `after=0`, validates declared page cursors, and starts the existing SSE streams from the derived cursors;
6. M49 itself never synthesizes or persists a stream cursor.

If the selected session no longer exists in the authoritative loaded page, M49 clears the stored id and leaves the operator unlocked with no selection.

## Fresh bootstrap precedence

A fresh M40 `#bootstrap=...` launch represents an explicit new operator launch and takes precedence over stale same-tab selection context.

M49 therefore captures whether a bootstrap fragment existed at initial script evaluation. If present, M49 clears any stored selected-session id and does not auto-restore it during that unlock. The existing M40 bootstrap authentication flow remains otherwise unchanged.

## Lock boundary

Explicit operator lock clears the stored selected-session id synchronously in addition to the frozen M48 reload capability cleanup.

Because the frozen `app.js` lock-button listener already holds its original `lockOperator` function reference, M49 must not rely on replacing that reference after registration. It must attach its own narrow lock cleanup listener or otherwise prove the stored id is cleared on the actual lock-button event.

## Storage boundary

M49 intentionally permits one additional tab-scoped non-secret context value: the canonical selected session id.

The following remain forbidden:

- persistent App Server bearer in any browser storage;
- lifecycle or trace cursors in browser persistent storage;
- lifecycle/trace/report/manifest/snapshot evidence in browser persistent storage;
- task text, workspace/output paths, failure text, model profile, budgets, or request bodies persisted by M49;
- localStorage, cookies, IndexedDB, Cache Storage, URLs, fragments, or query parameters for selected-session restoration;
- server-side durable browser-selection state.

M48 remains the credential-continuity authority. M49 stores no credential and cannot recover authentication by itself.

## Stream boundary

M49 does not change the M35/M37/M46 stream implementation or reconnect policy.

A restored selection must go through the existing selection path so that:

- lifecycle and trace sets are cleared before reload;
- authoritative pages are requested from `after=0`;
- page cursors are validated by the existing `projectPageCursor` logic;
- SSE starts from the exact returned cursors;
- selection generation guards still suppress stale results;
- M46 retry-exhaustion recovery remains unchanged.

This means reload may transfer more evidence than persisting a live cursor would, but it avoids making browser storage an authority for event position.

## Race boundary

M49 must preserve existing generation semantics.

If the operator manually selects a different session while automatic restore is in flight, the newer selection must win. Stale restore completion must not overwrite or restart streams for the newer selection.

If unlock fails, M49 must not issue a selected-session request.

If storage access is unavailable, M49 must fail open to the existing manual-selection UI without breaking authentication or streams.

## Multi-tab boundary

`sessionStorage` remains tab-scoped under normal browser behavior. Browser tab duplication may copy the selected id into the duplicated tab; that is acceptable because the id is not a credential and every restoration still revalidates through the authenticated authoritative App Server APIs.

No cross-tab synchronization, BroadcastChannel, SharedWorker, persistent tab identity, or global selected-session authority is introduced.

## Authority boundary

M49 changes only local browser convenience after successful authentication. It cannot:

- authenticate the operator;
- bypass the persistent bearer on any API;
- create/cancel/retry/resume tasks;
- mutate App Server session/lifecycle state;
- change report/trace/evidence provenance;
- change verifier/completion decisions;
- execute models or tools;
- mutate memory, budgets, controller, or control policy;
- persist or restore stream cursors as authority;
- add remote access, user identity, roles, or multi-user authorization.

The App Server remains authoritative for session existence, snapshot content, evidence pages, and stream cursors. The stored id is only a hint telling the existing authenticated UI which session to reselect.

## Non-goals / limitations

M49 does not preserve DOM scroll positions, open disclosure panels, in-flight download state, form contents, stream reconnect failure counters, or exact live event cursors across reload.

A restored running session rebuilds its displayed lifecycle/trace projection from the bounded page endpoints before reconnecting to SSE. Very large histories remain subject to the existing page limits and behavior; M49 does not introduce a new paging or archival mechanism.

M49 does not make selection survive browser/process restart, explicit lock, storage clearing, a fresh M40 bootstrap launch, or session deletion.

## Deterministic acceptance

Before freeze, M49 must prove:

- exact frozen M48 base `0b6cf9da99c0dc3d7e34ab20dd29c6d6bb2ec717`;
- this scope document is the first M49 commit;
- M48 PR #55 remains unchanged, draft, open, and unmerged;
- exactly one canonical selected-session id may be stored in tab-scoped `sessionStorage`;
- malformed stored ids are removed without network use;
- ordinary selection stores the selected id;
- explicit lock clears the stored id on the actual lock-button event;
- fresh M40 bootstrap clears/suppresses stale selection restoration;
- restoration occurs only after successful unlock and authoritative session-list load;
- missing/deleted stored session ids are cleared without selection request;
- valid stored id restoration reuses the existing `selectSession()` path;
- no lifecycle/trace cursor is persisted;
- existing `loadSessionEvidence(... after=0 ...)`, page-cursor validation, selection-generation guards, `startStreams`, and M46 reconnect/recovery behavior remain unchanged;
- manual selection racing automatic restore wins through existing generation semantics;
- storage exceptions leave manual selection/authentication usable;
- no bearer/evidence/task/request/path data is added to browser persistent storage;
- no localStorage/cookie/IndexedDB/Cache Storage/URL surface is introduced;
- no backend route/store/service/protocol/runtime/evidence/verifier/model/tool/memory/budget/controller/control authority changes;
- exact M48→M49 diff is confined to scope/client/allowlist/script-order/tests;
- exact-head Linux CI passes including installed `harness-x --help` and `validate-config`.
