# Milestone 59 — Offline Verification Receipt Reconciliation

M59 is stacked directly on frozen M58 and closes one narrow follow-up gap in the unsigned receipt workflow. M58 can persist a deterministic receipt after successful frozen M57/M52 verification, but the receipt is intentionally unsigned and therefore has no standalone authenticity claim.

M59 adds one local reconciliation command that takes a supplied M58 receipt and independently reruns the frozen M57 verification path against a caller-supplied M55 capsule/public key. After that fresh verification succeeds, M59 re-renders the deterministic M58 receipt from the fresh result and requires the supplied receipt bytes to match exactly.

This is a **current-consistency** check only. It does not make the receipt historically authentic, prove when it was created, or prove that it is the same physical file emitted by an earlier run.

## Stack

Exact frozen M58 base:

`fc1212961a6988a7f0fb26c98acc3f415fb400d5`

M58 PR #65 must remain frozen draft/open/unmerged at that exact head.

M59 branch:

`agent/milestone-59-offline-verification-receipt-reconciliation`

The first M59 commit is this scope/authority document. Final head and synthetic-merge evidence belong in PR metadata after exact-head qualification.

## Installed CLI

M59 adds exactly one installed command:

`harness-x reconcile-evidence-receipt RECEIPT CAPSULE --output-dir DIR --public-key PATH [--snapshot PATH] [--lifecycle PATH] [--report PATH] [--trace PATH]`

The command:

1. reads the supplied receipt once through the established bounded regular-file/no-follow boundary;
2. invokes frozen M57 `verify_evidence_capsule()` on the supplied capsule/output directory/public key and optional evidence paths;
3. invokes frozen M58 `render_verification_receipt()` on that successful fresh M57 result;
4. requires the exact retained receipt input bytes to equal the freshly rendered canonical M58 bytes;
5. emits a success summary only after all four steps succeed.

M59 does not parse or reinterpret receipt JSON itself. Frozen M58 rendering remains the receipt-schema/canonical-serialization authority.

## Receipt input boundary

M59 uses a dedicated small receipt byte ceiling and the existing portable-evidence bounded regular-file helper:

- lexical absolute normalization;
- final symlink rejection;
- intermediate symbolic-link substitution rejection;
- pre-open `lstat` and post-open `fstat` regular-file checks;
- `O_NOFOLLOW` where available;
- one bounded descriptor read.

Those exact retained bytes are the only supplied receipt bytes compared later.

A receipt boundary failure occurs before fresh capsule verification and therefore before M56 extraction creates files.

## Reconciliation semantics

The reconciliation authority is exact byte equality against a fresh frozen M58 render.

M59 does not implement a second receipt parser or field-by-field comparison. If the supplied receipt differs by any field, whitespace, key order, newline, status, hash text, filename, or other byte, reconciliation fails.

A successful reconciliation therefore means:

- the current supplied capsule passed frozen M56 structural/canonical validation;
- its extracted pair passed frozen M52 cryptographic verification with the supplied public key and selected optional evidence inputs through frozen M57;
- frozen M58 rendered the expected deterministic receipt for that fresh successful result;
- the supplied receipt bytes are exactly those expected canonical bytes.

## Side-effect ordering

Fresh M57 verification requires M56 extraction into the explicit `--output-dir` before cryptographic acceptance.

Therefore, a structurally readable but mismatched supplied receipt can fail **after** the capsule has been successfully extracted and cryptographically verified. In that case:

- the command exits nonzero;
- the extracted manifest/signature files remain present;
- no successful reconciliation summary is emitted;
- M59 does not delete or rewrite the supplied receipt.

This ordering is intentional and fail-visible.

## Trust boundary

M59 adds no trust authority.

The supplied receipt remains unsigned. Exact reconciliation against a fresh successful verification result proves only that its bytes are currently consistent with the result that frozen M58 would render for the supplied capsule/public key/evidence inputs.

It does **not** prove:

- that the receipt was created by a prior Harness X execution;
- when the receipt was created;
- who created or stored it;
- that the receipt has not been replaced historically;
- signer/public-key ownership or human identity;
- server/host identity;
- trusted timestamping or non-repudiation;
- source capsule historical identity;
- filesystem/history immutability.

The cryptographic claim remains frozen M52 verification of exact manifest/signature bytes with an externally trusted public key.

## Capsule identity limitation

Frozen M58 intentionally does not record a capsule-file SHA. M59 does not add one retrospectively.

Reconciliation proves that the supplied capsule produces the same deterministic verified-result receipt now. It does not claim that the receipt identifies a unique historical outer capsule file.

## Explicit non-goals

M59 does not add:

- receipt signing or signature verification;
- trusted timestamps;
- receipt mutation/repair;
- capsule-file hash fields;
- a second receipt JSON parser/schema implementation;
- public-key discovery/publication;
- App Server/browser/network behavior;
- implicit temporary extraction;
- overwrite of extracted evidence;
- changes to frozen M52/M56/M57/M58 implementations.

## Intended changed surface

M59 should remain confined to:

1. this milestone document;
2. one narrow receipt-reconciliation module;
3. additive installed CLI parser/dispatch wiring;
4. focused receipt-boundary/exact-match/mismatch/failure-order/source-scope tests.

Frozen M58 receipt rendering/persistence, M57 orchestration, M56 extraction, M52 signing/verifier, M43 manifest generation, App Server/browser/runtime/evidence-generation surfaces remain outside the intended diff.

## Qualification contract

M59 cannot freeze until one fixed final head satisfies all of the following:

- exact merge base is frozen M58 `fc1212961a6988a7f0fb26c98acc3f415fb400d5`;
- zero commits behind frozen M58;
- source/diff audit confirms no receipt-parser duplication, cryptographic primitive, trust source, App Server/browser, or frozen-verifier widening;
- valid M58 receipt reconciles after a fresh successful frozen M57 verification;
- one-byte/noncanonical/stale receipt mismatch is nonzero;
- receipt input boundary failure occurs before extraction;
- post-verification receipt mismatch leaves fresh extracted files present but emits no success;
- full pytest passes;
- `harness-x --help` exposes the new command while retaining existing commands;
- `harness-x validate-config configs/default.yaml` passes;
- no submitted reviews or actionable review threads remain;
- PR remains draft/open/unmerged;
- final head, synthetic merge, compare totals, CI identifiers, and test count are recorded in PR metadata without moving the branch.
