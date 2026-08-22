# Milestone 39 — Durable Report Attestation

M39 is stacked directly on frozen M38 and closes one deliberately documented integrity gap: M38 can fingerprint the current coding-report bytes, but the pre-M39 `ARTIFACT_AVAILABLE` event anchors only the report path and therefore cannot prove that current bytes equal the bytes observed when the artifact was durably recorded.

M39 remains a provenance milestone. It does not move task, verification, model, tool, trace, memory, or completion authority into the App Server or operator UI.

## Scope

M39 adds content attestation only for the canonical App Server coding report:

```text
coding-task-report.json
```

When a coding runner returns and that canonical report exists, the App Server reads it through a bounded, regular-file, non-symlink source reader and attempts to capture:

- exact source byte count;
- SHA-256 of the exact source bytes.

A successful capture is committed into the existing `ARTIFACT_AVAILABLE` event alongside the existing artifact kind/path metadata:

```text
attestation_schema_version = app-artifact-content-attestation-v1
attestation_status         = captured
source_digest_algorithm    = sha256
source_bytes               = <integer>
source_sha256               = <64 lowercase hex>
```

The artifact event is appended and fsynced through the existing hash-chained App Server ledger **before** the terminal `SESSION_COMPLETED`, `SESSION_FAILED`, or `SESSION_CANCELLED` transition. The following terminal event therefore chains from the attestation-bearing artifact event hash.

M39 does not introduce a second ledger, signature system, certificate authority, generic artifact attestation API, arbitrary filesystem hash endpoint, or caller-selected file surface.

## Capture boundary

`report_attestation.py` owns bounded source capture. It:

- rejects symbolic links;
- opens the source read-only and uses `O_NOFOLLOW` where the platform provides it;
- requires a regular file by descriptor metadata;
- applies the same 2 MiB ceiling used by M38 report projection;
- reads at most `maximum_bytes + 1` so growth across the metadata/read boundary remains bounded;
- computes SHA-256 over the exact bytes actually read.

The attestation is therefore the identity of the bytes observed immediately before ledger persistence. If the file changes after capture but before/after the artifact event append, later projection compares current bytes against the durable captured identity and exposes the mismatch.

## Artifact-store contract

`AppSessionStore.add_artifact()` remains backward-compatible and path-only by default. Optional attestation fields are accepted only as a complete pair:

```text
source_bytes + source_sha256
```

Before ledger append, the store rejects:

- one field without the other;
- captured content identity together with `attestation_error`;
- negative, boolean, or otherwise non-integer source byte counts;
- non-lowercase/non-64-character SHA-256 text;
- blank attestation errors.

Thus malformed producer input cannot first enter the ledger and rely on a later projection to notice it.

## Capture failure without outcome authority

A safe source read can fail independently of the coding result. M39 treats that as provenance/observability failure, not task failure.

When `ReportAttestationCaptureError` is raised after the runner returned, the App Server still records the canonical report artifact but commits:

```text
attestation_schema_version = app-artifact-content-attestation-v1
attestation_status         = unavailable
attestation_error          = <bounded diagnostic>
```

The coding runner's independently established `succeeded` / `failure_reason` values continue to drive the later terminal transition. A capture failure therefore cannot convert an otherwise successful task into a failed task.

This separation covers **attestation capture** failures. A failure of the underlying durable App Server ledger itself remains a storage-integrity failure and is not intentionally hidden.

## Projection contract

M39 upgrades the authenticated report projection to:

```text
app-coding-report-projection-v2
```

The projection continues all M38 canonical-path, artifact-evidence, symlink, regular-file, size, UTF-8, and JSON-object validation. It additionally exposes:

- `artifact_event_hash`;
- `attestation_status`;
- `attested_source_bytes` when available;
- `attested_source_sha256` when available;
- bounded `attestation_error` when capture was unavailable;
- the current `source_bytes` and `source_sha256` as before.

### Verified

A captured attestation becomes projection status:

```text
verified
```

only when the current exact byte count and SHA-256 equal the values committed in the hash-chained artifact event. Either mismatch is `report_corruption` and is never silently downgraded.

Because comparison happens before interpreting JSON, even a later modification that remains perfectly valid JSON is fail-visible if its bytes no longer match the durable attestation.

### Legacy compatibility

M38 and older path-only coding-report artifact events contain none of the M39 attestation keys. They remain readable as:

```text
legacy_unattested
```

M39 does not retrofit a digest or pretend historical bytes were observed. Current-source structural validation remains available, but no historical content-integrity claim is made for those sessions.

If any attestation key is present, the event is no longer treated as legacy. The schema/status/fields must form one complete recognized M39 state or projection fails as corruption. This prevents partial/malformed metadata from masquerading as verified or legacy evidence.

### Unavailable

A well-formed explicit capture-failure event projects as:

```text
unavailable
```

The operator can still inspect a currently valid report, but the projection explicitly states that no historical content identity was durably captured for that run.

## Operator UI

The existing authenticated M38 report viewer remains presentation-only and now surfaces the three provenance states:

- `ledger attestation verified`;
- `legacy path-only artifact`;
- `attestation unavailable`.

It also displays the artifact event hash and current source SHA-256. Verified projections display the attested SHA-256; unavailable projections may display the bounded capture diagnostic.

All values continue to render through `textContent`; no report or attestation data is interpreted as HTML. Bearer handling remains header-only and page-memory-only. M37/M38 stale-selection guards and report refresh behavior remain unchanged.

## Authority

M39 attests bytes only. It cannot:

- decide whether the coding task succeeded;
- establish verification from report contents or digest equality;
- modify report bytes;
- repair, rewrite, or skip lifecycle events;
- read caller-selected files;
- attest arbitrary workspace artifacts through HTTP/UI;
- execute tools or bypass permissions/budgets;
- mutate causal trace, project/procedure memory, revision, model, or controller state;
- hard-cancel running work.

The coding runtime/verifier remain completion and verification authorities. The App Server lifecycle ledger remains transition evidence. Digest equality establishes report-byte provenance only.

## Non-goals / limitations

M39 does not add:

- signatures or public-key authenticity;
- remote trust or multi-user identity;
- a timestamp authority;
- generic artifact download/browsing;
- arbitrary workspace hashing;
- historical attestation for M38-era path-only sessions;
- report-byte immutability enforcement at the filesystem layer.

M39 detects a later byte mismatch when an attested report is projected; it does not prevent the filesystem modification itself.

## Deterministic acceptance

M39 acceptance must prove:

- a new canonical report receives a complete SHA-256/byte-count attestation;
- the artifact event is hash-valid and precedes the terminal transition;
- the terminal event's `previous_hash` is the attestation-bearing artifact event hash;
- projection returns `verified` only after exact byte-count + SHA-256 agreement;
- same-length, valid-JSON tampering becomes fail-visible corruption;
- incomplete/malformed attestation metadata cannot masquerade as verified;
- historical path-only events project as `legacy_unattested`;
- explicit capture failure projects as `unavailable` and does not rewrite an independently successful task outcome;
- store-side malformed attestation inputs are rejected before event append;
- UI surfaces verified/legacy/unavailable provenance with safe text rendering;
- existing authenticated report-route/path-selection restrictions remain intact;
- M37 resilient stream and M38 report-authentication behavior remain intact;
- no generic file hashing/serving surface is introduced;
- exact M38→M39 diff is confined to intended App Server provenance/UI/tests/docs;
- exact-head Linux CI passes.
