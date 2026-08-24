# Milestone 58 — Offline Verification Receipt Export

M58 is stacked directly on frozen M57 and closes one narrow auditability gap in the offline capsule workflow. Frozen M57 can validate/extract an M55 capsule and delegate cryptographic verification to frozen M52, but its successful result is only a transient CLI summary.

M58 adds one optional deterministic local JSON receipt written only after frozen M57 verification succeeds. The receipt records the verified manifest identity, signer-key fingerprint, selected evidence-verification statuses, and the fact that the M55 capsule passed frozen M56 structural validation. It is an execution record only: it is unsigned, has no trusted timestamp, and is not a new attestation or trust source.

## Stack

Exact frozen M57 base:

`c98dc9f879b420437c7ad3eea33588b5db53437a`

M57 PR #64 must remain frozen draft/open/unmerged at that exact head.

M58 branch:

`agent/milestone-58-offline-verification-receipt-export`

The first M58 commit is this scope/authority document. Final head and synthetic-merge evidence belong in PR metadata after exact-head qualification so the tracked contract does not self-move the freeze gate.

## Installed CLI

M58 extends only the frozen M57 command with one optional argument:

`harness-x verify-evidence-capsule CAPSULE --output-dir DIR --public-key PATH [...] [--receipt PATH]`

When `--receipt` is omitted, behavior must remain the frozen M57 behavior exactly.

When `--receipt PATH` is supplied:

1. run frozen M57 `verify_evidence_capsule()` unchanged;
2. only after that call returns successfully, render one canonical receipt from the returned frozen M57/M52 result;
3. write the receipt once through the established resolved-parent/exclusive-create/no-follow output boundary;
4. extend the success summary with the receipt path and receipt SHA-256.

M58 does not create a receipt on any extraction, evidence-consistency, public-key, or Ed25519 verification failure.

## Receipt schema

The canonical schema is `app-evidence-verification-receipt-v1`.

The exact top-level fields are:

- `schema_version`: exact `app-evidence-verification-receipt-v1`;
- `algorithm`: exact `ed25519`;
- `capsule_status`: exact `validated`;
- `signature_status`: exact `verified`;
- `session_id`: frozen verification session ID;
- `manifest_bytes`: frozen verification manifest byte count;
- `manifest_sha256`: frozen verification manifest SHA-256;
- `key_fingerprint`: frozen M52 public-key fingerprint;
- `snapshot_status`: frozen M47 status;
- `snapshot_revision`: frozen M47 revision or null;
- `lifecycle_status`: frozen lifecycle status;
- `lifecycle_events`: frozen lifecycle event count or null;
- `report_status`: frozen report status;
- `trace_status`: frozen trace status;
- `trace_records`: frozen trace record count or null;
- `manifest_filename`: exact fixed M43 filename `session-evidence-manifest.json`;
- `signature_filename`: exact fixed M52 filename `session-evidence-manifest.sig.json`.

The receipt deliberately excludes local absolute paths, wall-clock time, hostname, process ID, user identity, and source capsule path so identical verification results serialize identically across output directories and machines.

## Canonical serialization

Receipt bytes are deterministic UTF-8 JSON:

- exact field set only;
- keys sorted lexicographically;
- compact separators;
- ASCII-safe JSON encoding;
- exactly one trailing newline.

The returned receipt SHA-256 is the SHA-256 of those exact bytes. The receipt does not embed its own hash.

## Trust boundary

M58 adds no trust authority.

A receipt is generated only after frozen M57 returns a successful result, so its content summarizes a successful execution of frozen M56 structural validation plus frozen M52 cryptographic verification. However, the receipt itself is unsigned and can be copied, edited, deleted, or fabricated after the fact.

Therefore the receipt does **not** independently prove:

- that verification actually ran;
- when verification ran;
- who ran verification;
- public-key ownership or signer identity;
- App Server/server/host identity;
- historical immutability;
- source capsule byte identity;
- receipt authenticity or non-repudiation.

The authoritative cryptographic claim remains frozen M52 verification of the exact manifest/signature bytes using an externally trusted public key.

## Capsule identity limitation

M58 intentionally does not add a second read/hash of the source capsule merely to place a capsule SHA in the receipt. Doing so would introduce a cross-read substitution boundary after frozen M56/M57 and could falsely imply that the receipt identifies the exact capsule bytes validated earlier.

The receipt therefore records only `capsule_status=validated`, the verified manifest identity, the verified public-key fingerprint, and `signature_status=verified`. It does not record or claim a portable SHA-256 identity for the outer M55 capsule file or the detached-signature envelope file.

## Output semantics

Receipt output uses the established M52 exclusive-write boundary:

- caller supplies an explicit receipt path;
- parent must already exist and resolve without symbolic-link substitution;
- no overwrite;
- `O_NOFOLLOW` where available;
- regular-file descriptor validation;
- exact write, flush, and fsync.

Receipt writing occurs after successful M57 verification. If receipt creation fails, the command exits nonzero. The already-extracted manifest/signature files remain present and were successfully verified during that execution, but no M58 receipt exists. M58 does not roll back frozen M56 outputs.

This is not crash-atomic and is not a filesystem transaction.

## Explicit non-goals

M58 does not add:

- receipt signing;
- a trusted timestamp;
- certificate/PKI/transparency semantics;
- public-key discovery or publication;
- a capsule-file SHA claim;
- App Server or browser receipt generation;
- network access;
- overwrite/repair behavior;
- automatic evidence discovery;
- changes to frozen M56 extraction or frozen M52/M57 verification logic.

## Intended changed surface

M58 should remain confined to:

1. this milestone document;
2. one deterministic receipt-render/write module;
3. additive optional `--receipt` wiring on `verify-evidence-capsule`;
4. focused success/failure/determinism/output-boundary/source-scope tests.

Frozen M57 orchestration implementation, frozen M56 extraction, frozen M52 signing/verifier implementation, M43 manifest generation, App Server/browser/runtime/evidence-generation surfaces remain outside the intended diff.

## Qualification contract

M58 cannot freeze until one fixed final head satisfies all of the following:

- exact merge base is frozen M57 `c98dc9f879b420437c7ad3eea33588b5db53437a`;
- zero commits behind frozen M57;
- source/diff audit confirms no cryptographic primitive, trust source, App Server/browser, capsule-parser, or frozen-verifier widening;
- omitting `--receipt` preserves frozen M57 behavior;
- successful verification produces exact canonical deterministic receipt bytes;
- receipt contains only data already returned by frozen M57/M52 plus fixed schema/algorithm/filename constants;
- verification failure produces no receipt;
- receipt overwrite/path failures are nonzero and do not delete verified extracted files;
- full pytest passes;
- `harness-x --help` and default-config validation pass;
- no submitted reviews or actionable review threads remain;
- PR remains draft/open/unmerged;
- final head, synthetic merge, compare totals, CI identifiers, and test count are recorded in PR metadata without moving the branch.
