# Milestone 47 — Portable Session Snapshot

M47 is stacked directly on frozen M46 and closes one narrow offline-evidence gap left explicit by M45: Harness X can independently verify the terminal lifecycle event chain, report bytes, and causal trace, but the portable evidence set still cannot independently recompute the `AppSessionSnapshot` fingerprint recorded by the M43 manifest and M45 lifecycle export because the complete snapshot/request preimage is absent.

M47 adds one authenticated terminal-only deterministic session-snapshot export plus optional offline verification of that export. It does not create a ZIP/session bundle, generic artifact browser, caller-selected path endpoint, signature authority, remote trust root, running-session checkpoint, or task/runtime mutation surface.

## Scope

M47 adds one authenticated operator endpoint:

```text
GET /v1/sessions/{session_id}/snapshot/export
```

The endpoint accepts no query parameters and no caller-selected path, filename, directory, artifact, snapshot revision, request field, or serialization option.

The fixed download filename is:

```text
session-snapshot.json
```

M47 also extends the installed portable verifier with one optional explicit local input:

```text
harness-x verify-evidence MANIFEST [--snapshot PATH] [--lifecycle PATH] [--report PATH] [--trace PATH]
```

Omitting `--snapshot` preserves M45 verification behavior except for one explicit deterministic `snapshot=not_supplied` result field. Supplying it activates independent snapshot-fingerprint recomputation and manifest/lifecycle correlation.

## Terminal-only export

Snapshot export is available only after the App Server session is terminal (`succeeded`, `failed`, or `cancelled`).

M47 does not create a point-in-time running-session evidence snapshot. Running status remains available through the existing authenticated session projection and M35/M37/M46 observation surfaces.

## Exported snapshot material

The export contains the complete persisted `app-session-snapshot-v1` JSON object, including its embedded `app-coding-session-request-v1` request and the stored `fingerprint` field.

This is intentionally the complete fingerprint preimage required for independent offline recomputation. The snapshot therefore includes fields that M45 deliberately omitted from the lifecycle-ledger export, including:

- task text;
- model profile;
- verification commands and optional verification-plan path;
- workspace root and output root;
- optional project-memory root/key;
- reasoning/tool/output-token budgets;
- baseline-verification setting;
- optional browser application/verification plan paths and headed flag;
- report path, trace path/ID, failure reason, cancellation state, timestamps, revision, event count, and lifecycle head hash.

M47 does not redact, normalize, hash, replace, or selectively omit those fields because doing so would change the snapshot fingerprint preimage.

## Disclosure boundary

The full session snapshot can contain sensitive local operational data. In particular, task text, verification commands, project-memory identifiers, filesystem paths, browser-plan paths, and failure text may disclose information the operator would not want to share externally.

This disclosure is explicit and necessary for independent fingerprint verification. The export remains:

- loopback-only under the existing App Server threat model;
- bearer authenticated;
- terminal only;
- initiated by an explicit operator download action;
- fixed-name and non-browsable;
- unavailable through generic path/file selection.

M47 does not claim that `session-snapshot.json` is privacy-redacted evidence.

## Server-side snapshot validation

Before rendering an export, M47 must validate the current terminal `AppSessionSnapshot` rather than blindly serialize an object supplied by a caller.

It requires:

- terminal status;
- non-null completion timestamp;
- exact requested session ID agreement;
- complete snapshot round-trip through the existing `AppSessionSnapshot` model;
- stored fingerprint equal to the independently rederived fingerprint;
- trace ID/path presence parity retained by the existing protocol model;
- schema version exactly `app-session-snapshot-v1`;
- nested request schema exactly `app-coding-session-request-v1`.

M47 does not reopen workspace/report/trace paths merely to export the session snapshot. This endpoint transports the durable App Server projection only; report and trace source validation remain owned by M39/M41/M42/M43.

## Deterministic rendering

The validated snapshot is serialized once to strict UTF-8 JSON plus one trailing newline.

No generation timestamp, nonce, random identifier, request-specific field, alternate formatting option, or caller metadata is added.

The rendered body is capped at 2 MiB. The exact retained bytes are described by:

```text
X-Harness-X-Snapshot-SHA256: <sha256 of exact response body>
X-Harness-X-Snapshot-Fingerprint: <stored and revalidated snapshot fingerprint>
X-Harness-X-Snapshot-Revision: <snapshot revision>
```

These values are integrity/correlation identifiers, not signatures or origin-authentication mechanisms.

## HTTP contract

Successful response:

```text
Content-Type: application/json; charset=utf-8
Content-Disposition: attachment; filename="session-snapshot.json"
Content-Length: <exact generated bytes>
Cache-Control: no-store
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
X-Harness-X-Snapshot-SHA256: <exact response SHA-256>
X-Harness-X-Snapshot-Fingerprint: <snapshot fingerprint>
X-Harness-X-Snapshot-Revision: <revision>
```

Bearer authentication remains mandatory. Query parameters and extra path segments must fail. No arbitrary local or package file reader is introduced.

## Offline fingerprint verification

When `--snapshot` is supplied, M47 reuses the M44/M45 bounded explicit-input file boundary.

The verifier reads one exact local regular file with:

- lexical absolute normalization without caller-controlled discovery;
- leaf symbolic-link rejection;
- strict resolved-path equality to reject intermediate symbolic-link substitution;
- `O_NOFOLLOW` when available;
- pre-open `lstat` and post-open `fstat` regular-file checks;
- a 2 MiB hard ceiling checked before and during one descriptor read.

The verifier requires strict UTF-8 JSON, rejects duplicate object keys at every level, requires a JSON object root, and rejects unknown/missing/ill-typed snapshot/request fields through an M47 portable schema mirroring `app-session-snapshot-v1` while treating persisted path strings as data rather than re-resolving them against the verifier machine.

### Raw canonical fingerprint recomputation

The existing `CodingSessionRequest` model intentionally resolves filesystem paths when a live App Server request is created/revalidated. An offline verifier must not reinterpret downloaded path strings through a different machine's filesystem and then claim it verified the original fingerprint.

M47 therefore recomputes the snapshot fingerprint from the exact parsed JSON values supplied in the downloaded snapshot:

1. retain the untrusted supplied `fingerprint` string before any model normalization;
2. remove only the top-level `fingerprint` field from the parsed JSON object;
3. canonicalize the remaining JSON with the exact App Server canonicalization contract (`sort_keys=True`, compact separators, UTF-8, no ASCII escaping);
4. compute SHA-256 over that canonical JSON;
5. require exact equality with the supplied fingerprint;
6. structurally validate the snapshot/request through an M47 portable schema that does not resolve path values.

This independently proves that the downloaded complete snapshot material recomputes to its claimed App Server fingerprint without consulting the local verifier filesystem.

## Manifest correlation

A verified snapshot must exactly match the M43 manifest lifecycle section for:

- session ID;
- terminal status;
- snapshot revision;
- snapshot fingerprint;
- event count;
- lifecycle head hash;
- created timestamp;
- completed timestamp.

The nested snapshot/request data gives the missing fingerprint preimage, but M47 does not alter the M43 manifest schema.

## Lifecycle-export correlation

If both `--snapshot` and `--lifecycle` are supplied, each is independently checked against the manifest by its own verifier path. Because both must match the same manifest session/revision/fingerprint/event-count/head/timestamps, disagreement between them fails closed.

M47 does not make `--lifecycle` mandatory merely because `--snapshot` is supplied and does not make `--snapshot` mandatory for legacy M44/M45 portable evidence verification.

## Operator UI

M47 adds `Download session snapshot` as a terminal-only evidence action.

The dependency-free client must:

- capture the existing page-memory bearer through the already-qualified auth-listener ordering;
- enable only for terminal selected sessions;
- perform authenticated same-origin `GET` with `cache: "no-store"` and `credentials: "omit"`;
- require exact JSON content type and fixed attachment filename;
- read one `ArrayBuffer`;
- require exact `Content-Length` equality;
- recompute response SHA-256 with Web Crypto and compare it with `X-Harness-X-Snapshot-SHA256`;
- validate fingerprint/revision response headers;
- parse exact bytes as fatal UTF-8 JSON;
- require `app-session-snapshot-v1`, selected session ID, terminal status, matching fingerprint header, matching numeric revision header, and nested `app-coding-session-request-v1`;
- save only through a temporary object URL with fixed filename `session-snapshot.json`;
- revoke the object URL immediately;
- abort in-flight download on selection change, lock, or unload;
- generation-guard completion/status;
- render status/errors only through `textContent`;
- store neither bearer nor snapshot bytes in cookies, `localStorage`, or `sessionStorage`.

The M47 client must be exact-allowlisted and must preserve the qualified M40 bootstrap listener ordering.

## CLI result contract

Successful portable verification remains deterministic and begins with `valid:`.

M47 adds:

- `snapshot=not_supplied snapshot_revision=none` when `--snapshot` is omitted;
- `snapshot=verified snapshot_revision=<N>` when the supplied snapshot passes fingerprint/schema/manifest correlation.

Existing lifecycle/report/trace status meanings remain unchanged.

Malformed snapshot JSON, duplicate keys, schema mismatch, stale fingerprint, session/status/revision/event-count/head/timestamp disagreement, non-regular/symlink/oversized file input, or unexpected path normalization behavior must fail visibly with no `valid:` result.

## Authority and provenance boundary

M47 verifies and transports the App Server session projection only. It cannot:

- authenticate who generated the snapshot or manifest;
- prove either file came from a particular App Server;
- sign evidence or establish public-key/remote trust;
- establish task success, verifier success, semantic correctness, or report quality;
- replace or mutate the App Server store;
- write, repair, redact, normalize, or update a session snapshot;
- create/cancel/retry/resume a task;
- execute models/tools;
- bypass permissions, budgets, lifecycle transitions, memory authority, or control policy;
- verify report or trace source bytes unless their existing M44 inputs are separately supplied.

The App Server store remains session/lifecycle authority. M39 remains historical report-byte attestation authority when available. `TraceStore` remains causal execution authority. Existing coding runtime/verifier remain task/completion authorities.

A fully rewritten internally self-consistent snapshot, lifecycle ledger, manifest, report, and trace can still form a new internally consistent portable set. M47 provides stronger independent consistency verification, not origin authentication.

## Non-goals / limitations

M47 does not add signatures, certificates, remote trust, timestamping, transparency logs, ZIP/session bundles, generic artifact browsing, arbitrary snapshot revision export, running snapshots, evidence repair, redaction, desktop packaging, or semantic evaluation.

The exported snapshot intentionally contains the complete request/projection preimage and should be handled as potentially sensitive local evidence.

## Deterministic acceptance

Before freeze, M47 must prove:

- exact frozen M46 base;
- M47 scope document is the first branch commit;
- authenticated terminal-only fixed-name snapshot export;
- query parameters and extra path segments rejected;
- no caller-selected path/revision/field/format surface;
- server independently revalidates snapshot fingerprint before export;
- deterministic UTF-8 JSON plus newline, 2 MiB cap, exact length/SHA/fingerprint/revision headers;
- export does not reopen report/trace/workspace paths;
- disclosure of complete task/request/path/project-memory/failure material is explicit and tested/documented rather than silently redacted;
- browser terminal gating, authenticated cookie-independent fetch, response length/SHA/header/schema/session checks, fixed filename, temporary object URL revocation, and page-memory-only bearer;
- browser cancellation/generation guards on session change, lock, and unload;
- exact UI asset allowlisting and qualified script ordering;
- installed `harness-x verify-evidence --help` exposes optional `--snapshot`;
- omitting `--snapshot` preserves M45 verification behavior except the explicit new snapshot summary field;
- supplied snapshot uses the existing bounded regular-file/symlink-resistant input boundary;
- strict UTF-8/JSON object input and duplicate-key rejection;
- raw supplied fingerprint is retained and independently recomputed from exact parsed JSON values excluding only `fingerprint`;
- portable schema validation does not resolve downloaded path strings through the verifier machine;
- snapshot session/status/revision/fingerprint/event-count/head/created/completed metadata exactly match the manifest;
- supplying both snapshot and lifecycle requires both independent verifier paths to agree with the same manifest;
- lifecycle/report/trace M45/M44 verification remains unchanged;
- no App Server mutation/runtime/task/verifier/model/tool/memory/controller/control authority changes;
- exact M46→M47 diff remains confined to snapshot export/transport/UI, offline verifier extension, focused tests, and this document;
- exact-head Linux CI passes including `harness-x --help` and `validate-config`.
