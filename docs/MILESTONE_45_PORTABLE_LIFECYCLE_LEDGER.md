# Milestone 45 — Portable Lifecycle Ledger

M45 is stacked directly on frozen M44 and closes one narrow offline-evidence gap that M44 leaves explicit: the portable verifier can validate the M43 manifest, report bytes, and causal trace, but it cannot independently reconstruct and verify the App Server lifecycle event chain named by the manifest because no deterministic terminal lifecycle-ledger file exists.

M45 adds one authenticated terminal-only lifecycle export plus optional offline verification of that export. It does not export the complete App Server session snapshot/request, create a ZIP/session bundle, sign evidence, establish remote trust, add a generic artifact browser, or change task/runtime authority.

## Scope

M45 adds one authenticated operator endpoint:

```text
GET /v1/sessions/{session_id}/lifecycle/export
```

The endpoint accepts no query parameters and no caller-selected event range, path, filename, directory, artifact, or ledger source.

It generates one deterministic JSON document from the current validated terminal App Server lifecycle state. The fixed download filename is:

```text
session-lifecycle-ledger.json
```

M45 also extends the installed M44 offline verifier with one optional explicit local input:

```text
harness-x verify-evidence MANIFEST [--lifecycle PATH] [--report PATH] [--trace PATH]
```

Omitting `--lifecycle` preserves M44 partial verification. Supplying it activates M45 lifecycle-chain verification and reports `lifecycle=verified` on success.

## Terminal-only export

A lifecycle export is available only after the selected App Server session is terminal (`succeeded`, `failed`, or `cancelled`). Running lifecycle state continues to use the existing paginated/SSE interfaces. M45 does not create a mutable running-session evidence snapshot.

## Export model

The generated document uses schema version:

```text
app-lifecycle-ledger-export-v1
```

It contains:

- `session_id`;
- terminal status;
- snapshot revision;
- snapshot fingerprint already produced by `AppSessionSnapshot`;
- lifecycle event count;
- ledger head hash and kind;
- created/completed timestamps;
- the complete ordered tuple of exact `app-event-v1` objects.

The export deliberately does **not** include the complete `AppSessionSnapshot` or `CodingSessionRequest`. It therefore does not directly package the task text, verification commands, model profile, project-memory configuration, browser-plan configuration, or the other request/snapshot fields as a second portable session description.

### Event-payload disclosure boundary

Independent offline recomputation of each AppEvent hash requires the exact payload that participated in that hash. M45 therefore exports AppEvent payloads without redaction or normalization.

Depending on the session history, those payloads can contain local operational information already committed to the durable lifecycle ledger, including:

- session `output_root`;
- canonical coding-report path;
- causal-trace path and trace ID;
- terminal failure text;
- M39 artifact attestation metadata and, when capture failed, bounded `attestation_error` diagnostic text;
- other future fields legitimately committed to an `app-event-v1` payload by the App Server.

This disclosure is intentional and necessary for independent event-hash verification. The endpoint remains bearer-authenticated, loopback-only under the existing App Server threat model, terminal-only, and invoked through an explicit operator download action.

M45 does not claim that omitting the full snapshot makes the lifecycle file path-free or diagnostic-free. Its minimization is narrower: it exports only lifecycle correlation metadata plus exact durable event preimages, rather than the entire session request/snapshot.

Because the complete snapshot is not exported, the offline verifier cannot independently recompute the M43 `snapshot_fingerprint`. That fingerprint remains a correlation value shared by the M43 manifest and M45 lifecycle export. The AppEvent chain itself is independently verifiable offline.

## Server-side validation

Before rendering an export, M45 validates the supplied terminal snapshot and complete event sequence rather than merely serializing `store.events()` output. It requires:

- terminal status and non-null completion timestamp;
- snapshot round-trip through `AppSessionSnapshot`;
- stored snapshot fingerprint equal to the independently rederived fingerprint;
- at least one lifecycle event;
- every event belongs to the requested session;
- contiguous sequences beginning at 1;
- exact `previous_hash` linkage;
- every event recomputes to its stored `event_hash`;
- event count equals `snapshot.event_count`;
- final event hash equals `snapshot.latest_event_hash`.

A corrupt or contradictory lifecycle cannot be exported as valid portable evidence.

Lifecycle validation is deliberately independent of report/trace source availability. A missing or corrupt report/trace does not prevent export of an otherwise valid App Server event chain.

## Deterministic rendering

The validated model is serialized once to strict UTF-8 JSON plus one trailing newline. The rendered body is capped at 4 MiB.

No generation timestamp, nonce, random identifier, request-specific field, or caller-controlled metadata is added. Identical validated lifecycle state therefore yields identical response bytes.

The exact response body is described by:

```text
X-Harness-X-Lifecycle-SHA256: <sha256 of exact response body>
X-Harness-X-Lifecycle-Events: <event count>
X-Harness-X-Lifecycle-Head-Hash: <ledger head hash>
```

These are integrity/correlation identifiers, not signatures or origin-authentication mechanisms.

## HTTP contract

A successful response uses:

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

Bearer authentication remains mandatory. Query parameters and extra path segments are rejected. No caller-selected filesystem or ledger-source surface is added.

## Operator UI

M45 adds `Download lifecycle ledger` beside the existing lifecycle/evidence-manifest controls for terminal selected sessions.

The dependency-free client:

- captures the existing page-memory bearer through the qualified auth-listener ordering;
- enables only for terminal selected sessions;
- performs authenticated same-origin `GET` with `cache: "no-store"` and `credentials: "omit"`;
- requires exact JSON content type and fixed attachment filename;
- reads one `ArrayBuffer`;
- requires `Content-Length` equality;
- recomputes SHA-256 with Web Crypto and compares it with the response digest header;
- requires valid event-count/head-hash headers;
- parses the exact bytes as fatal UTF-8 JSON;
- requires `app-lifecycle-ledger-export-v1`, the selected session ID, a terminal status, matching event count, and final event hash equal to the declared head;
- saves only through a temporary same-page object URL with fixed filename `session-lifecycle-ledger.json`;
- revokes the object URL immediately;
- aborts in-flight download on session change, lock, or unload;
- generation-guards completion/status;
- renders only with `textContent`;
- stores neither bearer nor lifecycle bytes in cookies, `localStorage`, or `sessionStorage`.

Script ordering is:

```text
report.js
report_export.js
trace_export.js
evidence_manifest.js
lifecycle_export.js
app.js
bootstrap.js
```

so all evidence-download clients capture the page-memory bearer before `app.js` clears the password field, and M40 bootstrap continues to drive the same qualified form-submit path.

## Offline lifecycle verification

When `--lifecycle` is supplied, M45 reuses the M44 explicit local-file boundary: `expanduser`, lexical normalization with `os.path.abspath()` without symlink resolution, leaf/intermediate symlink rejection through `lstat` plus strict resolved-path equality, `O_NOFOLLOW` when available, post-open regular-file validation, size checks before and during one descriptor read, and all verification over those retained bytes.

The lifecycle file has a 4 MiB hard ceiling.

The offline verifier requires:

- strict UTF-8 JSON object input;
- duplicate JSON keys rejected at every object level;
- exact `app-lifecycle-ledger-export-v1` schema with extra fields forbidden;
- export session ID equal to the M43 manifest session ID;
- status, snapshot revision/fingerprint, event count, ledger head hash/kind, and created/completed timestamps exactly equal to the M43 manifest lifecycle section;
- event array length equal to event count;
- exact `app-event-v1` parsing without replacement of stored `event_hash`;
- every event belongs to the manifest session;
- contiguous sequences from 1;
- exact `previous_hash` linkage;
- every event's stored `event_hash` equals the SHA-256 recomputed from its downloaded canonical AppEvent material;
- final sequence/count/head hash/head kind agree with both export metadata and M43 manifest lifecycle evidence.

A lifecycle file with updated outer metadata but a broken event chain still fails.

M45 does **not** independently recompute `snapshot_fingerprint`, because the complete snapshot/request is intentionally absent. It requires exact equality between M43 and M45 and states this weaker boundary explicitly.

## CLI result contract

M44 success remains deterministic and begins with `valid:`. M45 adds:

- `lifecycle=not_supplied lifecycle_events=none` when `--lifecycle` is omitted;
- `lifecycle=verified lifecycle_events=<N>` when the supplied export passes.

Existing report/trace states remain unchanged. Invalid lifecycle syntax/schema/session identity/manifest correlation/event-chain integrity/metadata/path boundary fails visibly through the argparse nonzero error path and produces no `valid:` result.

## Authority and provenance boundary

M45 verifies and transports the App Server lifecycle event chain only. It cannot:

- authenticate who generated the export or manifest;
- prove either file came from a particular App Server;
- independently recompute the full `AppSessionSnapshot` fingerprint without the omitted snapshot/request fields;
- establish task success, verifier success, report semantic correctness, or causal-trace semantic correctness;
- replace the durable App Server lifecycle ledger;
- write, repair, append, reorder, redact, or mutate lifecycle events;
- execute models/tools;
- create/cancel sessions;
- bypass permissions, budgets, lifecycle transitions, or control policy;
- provide signatures, public-key authenticity, timestamp authority, transparency logs, or remote trust.

The App Server store remains authoritative for lifecycle state. M39 remains historical report-byte attestation authority when available. `TraceStore` remains causal execution authority. Existing coding runtime/verifier remain task/completion authorities.

## Non-goals / limitations

M45 does not add the complete session snapshot/request, signatures/public-key verification, certificates, remote trust, timestamping, ZIP/session bundles, generic artifact browsing, arbitrary event-range export, running lifecycle snapshots, evidence repair, or semantic evaluation.

A fully rewritten internally self-consistent lifecycle ledger plus a correspondingly rewritten self-fingerprinted M43 manifest can form a new internally consistent portable set. M45 is an offline consistency/integrity mechanism, not origin authentication.

The exported lifecycle ledger is intentionally not a privacy-redacted audit view. Operators should treat it as local evidence that can contain paths and diagnostic text already present in durable event payloads.

## Deterministic acceptance

Before freeze, M45 must prove:

- exact frozen M44 base;
- terminal-only authenticated lifecycle export;
- no query parameters or caller-selected ledger/path surface;
- server-side terminal snapshot revalidation and stored-vs-recomputed snapshot fingerprint agreement;
- complete event session/sequence/previous-hash/event-hash/count/head verification before export;
- lifecycle validation independent of report/trace availability;
- exact event payloads are preserved, with their path/diagnostic disclosure documented rather than silently redacted;
- full request/snapshot fields are not separately exported;
- deterministic generated JSON bytes, 4 MiB cap, and fixed filename;
- exact response length/SHA/event-count/head-hash headers;
- browser terminal gating, authenticated cookie-independent fetch, response length/SHA/header/schema/session verification, fixed filename, temporary object URL revocation, and page-memory-only bearer;
- browser cancellation/generation guards on session change, lock, and unload;
- installed `harness-x verify-evidence --help` exposes optional `--lifecycle`;
- omission of `--lifecycle` preserves M44 behavior except for the explicit added `lifecycle=not_supplied` summary field;
- explicit lifecycle verification requires M43/M45 session and lifecycle metadata equality;
- duplicate-key/schema/path-boundary failures remain fail-closed;
- downloaded stored AppEvent hashes are independently recomputed offline rather than silently replaced by model validation;
- event sequence, previous hash, event hash, count, head hash, and head kind are independently verified;
- snapshot fingerprint is correlated exactly but not overstated as independently recomputed offline;
- report/trace M44 verification remains unchanged;
- existing paginated/SSE lifecycle interfaces remain unchanged;
- no App Server lifecycle mutation/runtime/task/verifier/model/tool/memory/control authority changes;
- exact M44→M45 diff remains confined to lifecycle export/transport/UI, offline verifier extension, focused tests, and this document;
- exact-head Linux CI passes.
