# Milestone 45 — Portable Lifecycle Ledger

M45 is stacked directly on frozen M44 and closes one narrow offline-evidence gap that M44 leaves explicit: the portable verifier can validate the M43 manifest, report bytes, and causal trace, but it cannot independently reconstruct and verify the App Server lifecycle event chain named by the manifest because no deterministic terminal lifecycle-ledger file exists.

M45 adds one authenticated terminal-only lifecycle export plus optional offline verification of that export. It does not export the full App Server session snapshot, create a ZIP/session bundle, sign evidence, establish remote trust, add a generic artifact browser, or change task/runtime authority.

## Scope

M45 adds one authenticated operator endpoint:

```text
GET /v1/sessions/{session_id}/lifecycle/export
```

The endpoint accepts no query parameters and no caller-selected event range, path, filename, directory, artifact, or ledger source.

It generates one deterministic JSON document from the current validated terminal App Server lifecycle state.

The fixed download filename is:

```text
session-lifecycle-ledger.json
```

M45 also extends the installed M44 offline verifier with one optional explicit local input:

```text
harness-x verify-evidence MANIFEST [--lifecycle PATH] [--report PATH] [--trace PATH]
```

Omitting `--lifecycle` preserves the M44 partial-verification behavior. Supplying it activates M45 lifecycle-chain verification and reports `lifecycle=verified` on success.

## Terminal-only export

A lifecycle export is available only when the selected App Server session is terminal (`succeeded`, `failed`, or `cancelled`).

Running lifecycle state remains available through the existing M34/M35 paginated and SSE event interfaces. M45 does not create a mutable running-session evidence snapshot.

## Export model

The generated document uses schema version:

```text
app-lifecycle-ledger-export-v1
```

It contains only the lifecycle correlation data needed to verify the durable event chain against the M43 manifest:

- `session_id`;
- terminal session status;
- snapshot revision;
- snapshot fingerprint already produced by the durable `AppSessionSnapshot` contract;
- lifecycle event count;
- ledger head hash;
- ledger head kind;
- session created/completed timestamps;
- the complete ordered tuple of `app-event-v1` event objects.

The export deliberately does **not** include the complete `AppSessionSnapshot` or `CodingSessionRequest`.

That omission avoids turning an offline evidence export into a new disclosure surface for workspace roots, task text, verification commands, project-memory locations, browser-plan paths, output roots, report paths, trace paths, or other request/session fields that are unnecessary for lifecycle-chain verification.

Because the complete snapshot is not exported, M45 does not claim that an offline verifier can independently recompute the M43 `snapshot_fingerprint`. The fingerprint remains a correlation value shared by the M43 manifest and M45 lifecycle export. The event ledger itself is independently verifiable offline.

## Server-side lifecycle validation

Before rendering an export, M45 must validate the supplied terminal snapshot and complete event sequence rather than merely serializing `store.events()` output.

The server-side validator must require:

- terminal session status and non-null completion timestamp;
- at least one lifecycle event;
- every event belongs to the requested session;
- contiguous event sequence numbers beginning at 1;
- exact `previous_hash` linkage;
- every event recomputes to its recorded `event_hash`;
- event count equals `snapshot.event_count`;
- final event hash equals `snapshot.latest_event_hash`;
- the final event kind/hash/count agree with the generated export metadata;
- the snapshot round-trips through `AppSessionSnapshot` and its stored fingerprint equals the independently recomputed snapshot fingerprint.

A corrupt or contradictory lifecycle cannot be exported as a valid portable ledger.

M45 validates the lifecycle independently of report/trace availability. A missing or corrupt report/trace must not prevent export of an otherwise valid terminal App Server lifecycle ledger.

## Deterministic rendering

The validated lifecycle export is serialized once to strict UTF-8 JSON plus one trailing newline.

No generation timestamp, nonce, random identifier, request-specific field, or caller-controlled metadata enters the export. Identical validated lifecycle state therefore produces identical response bytes.

The exact generated response byte sequence is described by:

```text
X-Harness-X-Lifecycle-SHA256: <sha256 of exact response body>
X-Harness-X-Lifecycle-Events: <event count>
X-Harness-X-Lifecycle-Head-Hash: <ledger head hash>
```

The digest is an integrity identifier, not a signature or origin-authentication mechanism.

## HTTP contract

Successful export uses:

```text
Content-Type: application/json; charset=utf-8
Content-Disposition: attachment; filename="session-lifecycle-ledger.json"
Content-Length: <exact generated response bytes>
Cache-Control: no-store
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
X-Harness-X-Lifecycle-SHA256: <exact response SHA-256>
X-Harness-X-Lifecycle-Events: <event count>
X-Harness-X-Lifecycle-Head-Hash: <ledger head hash>
```

Bearer authentication remains mandatory and inherited from the M34 App Server boundary.

Query parameters and extra path segments are rejected. M45 exposes no caller-selected filesystem or ledger-source surface.

## Operator UI

M45 adds one `Download lifecycle ledger` action beside the existing lifecycle/evidence-manifest controls for a terminal selected session.

The dependency-free browser client must:

- capture the existing page-memory bearer through the qualified auth-listener ordering;
- enable only for terminal selected sessions;
- perform authenticated same-origin `GET` with `cache: "no-store"` and `credentials: "omit"`;
- require the expected content type and fixed attachment filename;
- read exactly one `ArrayBuffer`;
- require `Content-Length` equality;
- recompute SHA-256 with Web Crypto and require equality with `X-Harness-X-Lifecycle-SHA256`;
- parse the exact downloaded bytes as strict UTF-8 JSON;
- require schema `app-lifecycle-ledger-export-v1`, selected session ID, terminal status, valid event count/head hash, and matching response event-count/head-hash headers;
- save only through a temporary same-page object URL using fixed filename `session-lifecycle-ledger.json`;
- revoke the object URL immediately;
- abort in-flight download on session selection change, lock, or unload;
- generation-guard completion/status so one session cannot present under another;
- render status/errors with `textContent` only;
- store neither bearer nor lifecycle bytes in cookies, `localStorage`, or `sessionStorage`.

M40 bootstrap continues to use the same auth-submit path. Existing M41/M42/M43 download clients remain unchanged unless script ordering requires an explicit M45 insertion before `app.js`.

## Offline lifecycle verification

When `--lifecycle` is supplied, M45 reuses the M44 explicit local-file boundary: lexical normalization without resolving symlinks, leaf/intermediate symlink rejection, regular-file checks before and after open, `O_NOFOLLOW` when available, bounded one-descriptor read, and verification over the exact retained bytes.

The lifecycle file has a 4 MiB hard ceiling.

The offline verifier must require:

- strict UTF-8 JSON object input;
- duplicate JSON object keys rejected at every object level;
- exact `app-lifecycle-ledger-export-v1` schema with extra fields forbidden;
- lifecycle export session ID equals the M43 manifest session ID;
- status, snapshot revision/fingerprint, event count, ledger head hash/kind, and created/completed timestamps equal the M43 manifest lifecycle section;
- at least one event;
- every event is exact `app-event-v1` schema;
- every event belongs to the manifest session;
- sequences are contiguous from 1;
- every `previous_hash` exactly matches the preceding event hash;
- every event recomputes to its recorded `event_hash` using the App Server canonical hash contract;
- final sequence/count/head hash/head kind equal both the export metadata and M43 manifest lifecycle evidence.

A lifecycle file with internally recomputed metadata but a broken event chain must still fail.

M45 does not independently recompute `snapshot_fingerprint`, because the complete snapshot is intentionally not present. It requires exact equality between the M43 manifest and M45 export and states this weaker boundary explicitly.

## CLI result contract

M44 success output remains deterministic and begins with `valid:`.

M45 adds one lifecycle state field:

- `lifecycle=not_supplied` when `--lifecycle` is omitted;
- `lifecycle=verified` when the supplied export passes all M45 checks.

Existing report/trace status semantics remain unchanged.

Invalid lifecycle syntax/schema/session identity/manifest correlation/event-chain integrity/metadata/path boundary must fail visibly with a nonzero argparse error and no `valid:` line.

## Authority and provenance boundary

M45 verifies and transports the App Server lifecycle event chain only. It cannot:

- authenticate who generated the export or manifest;
- prove either file came from a particular App Server;
- independently recompute the full `AppSessionSnapshot` fingerprint without the intentionally omitted snapshot fields;
- establish task success, verifier success, report semantic correctness, or causal-trace semantic correctness;
- replace the durable App Server lifecycle ledger;
- write, repair, append, reorder, or mutate lifecycle events;
- execute models or tools;
- create or cancel sessions;
- bypass permissions, budgets, lifecycle transitions, or control policy;
- provide signatures, public-key authenticity, timestamp authority, transparency logs, or remote trust.

The App Server store remains authoritative for lifecycle state. M39 remains historical report-byte attestation authority when available. `TraceStore` remains causal execution authority. Existing coding runtime/verifier remain task/completion authorities.

## Non-goals / limitations

M45 does not add the complete session snapshot to portable evidence, signatures/public-key verification, certificates, remote trust, timestamping, ZIP/session bundles, generic artifact browsing, arbitrary event-range export, running lifecycle snapshots, evidence repair, or semantic evaluation.

A fully rewritten internally self-consistent lifecycle ledger plus a correspondingly rewritten self-fingerprinted M43 manifest can still form a new internally consistent portable set. M45 is an offline consistency/integrity mechanism, not origin authentication.

## Deterministic acceptance

Before freeze, M45 must prove:

- exact frozen M44 base;
- terminal-only authenticated lifecycle export;
- no query parameters or caller-selected ledger/path surface;
- server-side terminal snapshot revalidation and stored-vs-recomputed snapshot fingerprint agreement;
- complete event sequence/session/previous-hash/event-hash/count/head verification before export;
- lifecycle validation does not depend on report/trace availability;
- deterministic generated JSON bytes and fixed filename;
- exact response byte count/SHA-256/event-count/head-hash headers;
- browser terminal gating, authenticated cookie-independent fetch, response length/SHA/header/schema/session verification, fixed filename, temporary object URL revocation, and page-memory-only bearer;
- browser cancellation/generation guards on session change, lock, and unload;
- installed `harness-x verify-evidence` help exposes optional `--lifecycle`;
- omission of `--lifecycle` preserves M44 verification behavior;
- explicit lifecycle verification requires manifest/export session and lifecycle metadata equality;
- duplicate-key/schema/path-boundary failures remain fail-closed;
- event sequence, previous-hash, event-hash, count, head hash, and head kind are independently verified offline;
- snapshot fingerprint is correlated exactly but not overstated as independently recomputed offline;
- report/trace M44 verification behavior remains unchanged;
- existing M34/M35 paginated/SSE lifecycle interfaces remain unchanged;
- no App Server lifecycle mutation/runtime/task/verifier/model/tool/memory/control authority changes;
- exact M44→M45 diff remains confined to lifecycle export/transport/UI, offline verifier extension, focused tests, and this document;
- exact-head Linux CI passes.
