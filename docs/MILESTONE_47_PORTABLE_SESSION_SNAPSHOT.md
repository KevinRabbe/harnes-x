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

Omitting `--snapshot` delegates through the frozen M45 verifier path directly. M47 adds only the explicit deterministic `snapshot=not_supplied snapshot_revision=none` result fields in that case; it performs no M47-specific manifest read or snapshot correlation. Supplying `--snapshot` activates independent snapshot-fingerprint recomputation and manifest correlation.

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

The endpoint receives the authoritative typed `AppSessionSnapshot` already held by the App Server store. M47 validates that current terminal projection before serialization; it does not accept a caller-supplied snapshot object.

It requires:

- terminal status;
- non-null completion timestamp;
- exact requested session ID agreement;
- stored fingerprint equal to SHA-256 of the canonical JSON material produced by `snapshot.model_dump(mode="json", exclude={"fingerprint"})`;
- trace ID/path presence parity retained by the existing protocol object;
- schema version exactly `app-session-snapshot-v1` and nested request schema exactly `app-coding-session-request-v1`, as already enforced by the typed store projection.

The validation deliberately does **not** instantiate a fresh `AppSessionSnapshot` or `CodingSessionRequest`. The live request model resolves path fields during validation, so revalidating historical request material at export time could reinterpret persisted path strings after filesystem changes. M47 instead recomputes the fingerprint directly from the already-authoritative snapshot JSON values. A regression physically replaces a persisted workspace path with a symlink after terminalization and proves export retains the committed path string/fingerprint rather than resolving it again.

M47 also does not reopen workspace/report/trace paths merely to export the session snapshot. Report and trace source validation remain owned by M39/M41/M42/M43.

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

Bearer authentication remains mandatory. Query parameters and extra path segments fail. No arbitrary local or package file reader is introduced.

The M47 route is layered in a subclass over the frozen M46 `LocalOperatorHTTPServer`. Existing report, trace, manifest, lifecycle, UI, bootstrap, session, and stream routes continue through the inherited qualified implementation.

## Offline fingerprint verification

When `--snapshot` is supplied, M47 reuses the M44/M45 bounded explicit-input file boundary.

The verifier reads one exact local regular file with:

- lexical absolute normalization without caller-controlled discovery;
- leaf symbolic-link rejection;
- strict resolved-path equality to reject intermediate symbolic-link substitution;
- `O_NOFOLLOW` when available;
- pre-open `lstat` and post-open `fstat` regular-file checks;
- a 2 MiB hard ceiling checked before and during one descriptor read.

The verifier requires strict UTF-8 JSON, rejects duplicate object keys at every level, requires a JSON object root, and rejects unknown/missing/ill-typed snapshot/request fields through an M47 portable schema mirroring `app-session-snapshot-v1` while treating persisted path strings as inert data rather than re-resolving them against the verifier machine.

### Raw canonical fingerprint recomputation

The existing `CodingSessionRequest` model intentionally resolves filesystem paths when a live App Server request is validated. An offline verifier must not reinterpret downloaded path strings through a different machine's filesystem and then claim it verified the original fingerprint.

M47 therefore recomputes the snapshot fingerprint from the exact parsed JSON values supplied in the downloaded snapshot:

1. retain the untrusted supplied `fingerprint` string before model normalization;
2. remove only the top-level `fingerprint` field from the parsed JSON object;
3. canonicalize the remaining JSON with the exact App Server canonicalization contract (`sort_keys=True`, compact separators, UTF-8, no ASCII escaping);
4. compute SHA-256 over that canonical JSON;
5. require exact equality with the supplied fingerprint;
6. structurally validate the snapshot/request through an M47 portable schema whose path fields are inert strings.

This independently proves that the downloaded complete snapshot material recomputes to its claimed App Server fingerprint without consulting the verifier machine's filesystem.

## Manifest correlation and cross-read identity

A verified snapshot must exactly match the M43 manifest lifecycle section for:

- session ID;
- terminal status;
- snapshot revision;
- snapshot fingerprint;
- event count;
- lifecycle head hash;
- created timestamp;
- completed timestamp.

M47 leaves the frozen M45 verifier implementation unchanged. When `--snapshot` is supplied, the wrapper first captures one bounded manifest identity for M47 correlation, runs the existing M45 verifier independently, and then requires the M45 result's manifest byte count and SHA-256 to equal the M47-captured identity. A combined success therefore cannot silently correlate the snapshot against manifest A while M45 verified manifest B under concurrent replacement.

When `--snapshot` is omitted, this additional read is not performed: the wrapper delegates directly through M45.

## Lifecycle-export correlation

If both `--snapshot` and `--lifecycle` are supplied, each is independently checked against the same manifest identity. Both must agree on the session/revision/fingerprint/event-count/head metadata committed by the manifest.

M47 does not make `--lifecycle` mandatory merely because `--snapshot` is supplied and does not make `--snapshot` mandatory for M44/M45 portable evidence verification.

## Operator UI

M47 adds `Download session snapshot` as a terminal-only evidence action. The new action is placed below the existing lifecycle panel heading so the qualified M45 lifecycle/manifest action row is not widened with a third long button.

The dependency-free client:

- captures the existing page-memory bearer through the already-qualified auth-listener ordering;
- enables only for terminal selected sessions;
- performs authenticated same-origin `GET` with `cache: "no-store"` and `credentials: "omit"`;
- requires exact JSON content type and fixed attachment filename;
- reads one `ArrayBuffer`;
- requires exact `Content-Length` equality;
- recomputes response SHA-256 with Web Crypto and compares it with `X-Harness-X-Snapshot-SHA256`;
- validates fingerprint/revision response headers;
- parses exact bytes as fatal UTF-8 JSON;
- requires `app-session-snapshot-v1`, selected session ID, terminal status, matching fingerprint header, matching numeric revision header, and nested `app-coding-session-request-v1`;
- saves only through a temporary object URL with fixed filename `session-snapshot.json`;
- revokes the object URL;
- aborts in-flight download on selection change, lock, or unload;
- generation-guards completion/status;
- renders status/errors only through `textContent`;
- stores neither bearer nor snapshot bytes in cookies, `localStorage`, or `sessionStorage`.

The M47 client is exact-allowlisted and remains before `app.js`/M46 stream recovery/`bootstrap.js`, preserving the M40 auth-listener ordering.

## CLI result contract

Successful portable verification remains deterministic and begins with `valid:`.

M47 adds:

- `snapshot=not_supplied snapshot_revision=none` when `--snapshot` is omitted;
- `snapshot=verified snapshot_revision=<N>` when the supplied snapshot passes fingerprint/schema/manifest correlation.

Existing lifecycle/report/trace status meanings remain unchanged.

Malformed snapshot JSON, duplicate keys, schema mismatch, stale fingerprint, session/status/revision/event-count/head/timestamp disagreement, non-regular/symlink/oversized file input, or unexpected path reinterpretation fail visibly with no `valid:` result.

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

## Qualification history before freeze

Qualification remains fail-visible. Provisional CI #1188 ran on an earlier integrated M47 candidate and produced `570 passed, 1 failed in 96.22s`; the single failure compared a correctly exported JSON path string with an in-memory `Path` object. The implementation output was correct and the test was corrected to compare the persisted string. Because subsequent source-audit hardening moved the branch head, #1188 is not a freeze gate.

Later source audit also:

- replaced server-side model round-trip revalidation with direct canonical fingerprint recomputation to avoid path reinterpretation;
- pinned manifest identity across M45 and M47 reads when `--snapshot` is supplied;
- preserved direct frozen-M45 delegation with no M47-specific manifest read when `--snapshot` is omitted;
- moved the new snapshot action out of the existing M45 lifecycle action row;
- removed newline-only diff noise from existing files.

The final freeze gate must be Linux CI on the exact documented head after all of those changes.

## Deterministic acceptance

Before freeze, M47 must prove:

- exact frozen M46 base;
- M47 scope document is the first branch commit;
- authenticated terminal-only fixed-name snapshot export;
- query parameters and extra path segments rejected;
- no caller-selected path/revision/field/format surface;
- server independently recomputes the current typed snapshot fingerprint before export without re-resolving persisted request paths;
- deterministic UTF-8 JSON plus newline, 2 MiB cap, exact length/SHA/fingerprint/revision headers;
- export does not reopen report/trace/workspace paths;
- disclosure of complete task/request/path/project-memory/failure material is explicit and tested/documented rather than silently redacted;
- browser terminal gating, authenticated cookie-independent fetch, response length/SHA/header/schema/session checks, fixed filename, temporary object URL revocation, and page-memory-only bearer;
- browser cancellation/generation guards on session change, lock, and unload;
- exact UI asset allowlisting and qualified script ordering;
- installed `harness-x verify-evidence --help` exposes optional `--snapshot`;
- omitting `--snapshot` delegates through frozen M45 without an M47-specific manifest read and adds only explicit snapshot summary state;
- supplied snapshot uses the existing bounded regular-file/symlink-resistant input boundary;
- strict UTF-8/JSON object input and duplicate-key rejection;
- raw supplied fingerprint is retained and independently recomputed from exact parsed JSON values excluding only `fingerprint`;
- portable schema validation does not resolve downloaded path strings through the verifier machine;
- snapshot session/status/revision/fingerprint/event-count/head/created/completed metadata exactly match the manifest;
- supplied-snapshot verification pins M45 and M47 manifest byte identity across their independent reads;
- supplying both snapshot and lifecycle requires both verifier paths to agree with the same manifest identity;
- lifecycle/report/trace M45/M44 verification remains unchanged;
- no App Server mutation/runtime/task/verifier/model/tool/memory/controller/control authority changes;
- exact M46→M47 diff remains confined to snapshot export/transport/UI, offline verifier extension, focused tests, and this document;
- exact-head Linux CI passes including `harness-x --help` and `validate-config`.
