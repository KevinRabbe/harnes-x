# Milestone 62 — Verified Receipt + Detached Signature Export Orchestration

M62 is stacked directly on frozen M61 and closes one narrow operator-handoff gap in the offline receipt workflow. Frozen M57 can freshly verify an M55 capsule. Frozen M58 can persist a deterministic receipt from that successful result. Frozen M60 can sign exact receipt-file bytes with a separately managed Ed25519 private key. Before M62, producing the complete verified receipt + detached receipt-signature pair requires separate CLI steps.

M62 adds one local orchestration command that performs those already-frozen operations in order and then pins the receipt SHA-256 reported independently by frozen M58 and frozen M60. It adds no receipt parser, cryptographic primitive, public-key discovery, trusted timestamp, App Server behavior, browser behavior, or new trust authority.

## Stack

Exact frozen M61 base:

`c483a6b2bef9c08bfbd80b5b4aca7dee5bfdfbaa`

M61 PR #68 must remain frozen draft/open/unmerged at that exact head.

M62 branch:

`agent/milestone-62-verified-receipt-signature-export`

The first M62 commit is this scope/authority document. Final head and synthetic-merge evidence belong only in PR metadata after exact-head qualification.

## Installed CLI

M62 adds exactly one installed command:

`harness-x export-signed-evidence-receipt CAPSULE --output-dir DIR --evidence-public-key PATH --receipt PATH --receipt-private-key PATH --receipt-signature PATH [--snapshot PATH] [--lifecycle PATH] [--report PATH] [--trace PATH]`

The explicit roles are:

- `--evidence-public-key`: externally trusted public key used by frozen M57/M52 for fresh capsule/evidence verification;
- `--receipt-private-key`: operator-managed private key used by frozen M60 to authenticate the persisted receipt bytes;
- `--receipt`: explicit exclusive output path for the canonical frozen M58 receipt;
- `--receipt-signature`: explicit exclusive output path for the detached frozen M60 receipt signature envelope;
- `--output-dir`: existing directory for frozen M56 exact manifest/signature extraction performed during fresh verification.

M62 does not assume the evidence-signing and receipt-signing keys are the same authority.

## Orchestration order

M62 has exactly three frozen phases:

1. call frozen M57 `verify_evidence_capsule()` using the supplied capsule, evidence public key, extraction directory, and optional evidence paths;
2. only after M57 succeeds, call frozen M58 `persist_verification_receipt()` on that exact returned verification result and the supplied receipt output path;
3. only after M58 succeeds, call frozen M60 `sign_evidence_verification_receipt()` on the exact persisted receipt path, supplied receipt private key, and supplied detached-signature output path.

After all three phases return, M62 requires:

`M58.receipt_sha256 == M60.receipt_sha256`

A mismatch is a fail-visible receipt cross-read substitution condition and produces no successful M62 result.

M62 does not reproduce capsule extraction, evidence verification, receipt rendering, receipt file output, key parsing, Ed25519 signing, signature-envelope rendering, or their filesystem boundaries.

## Cross-read receipt identity

Frozen M58 renders deterministic receipt bytes and writes them to the explicit path. Frozen M60 then independently reads that path through its qualified bounded/no-follow input boundary before signing. Another actor could alter the receipt file between those operations.

M62 therefore correlates the SHA-256 identity returned by the M58 persistence phase with the SHA-256 identity independently observed and signed by M60.

This detects, but does not transactionally prevent, an inter-phase receipt substitution. M62 makes no atomicity claim across receipt persistence and later signature generation.

If the receipt changes after M58 but before or during the frozen M60 read, and M60 successfully signs the changed bytes, M62 rejects when the two returned SHA-256 values differ. The receipt file, extracted evidence files, and any detached signature successfully created by the frozen phases remain present. M62 does not delete or repair them; they must not be treated as a successful M62 pair.

## Output and failure semantics

M62 preserves each frozen phase's existing output authority and failure behavior:

- fresh M57 verification failure: no M58 receipt or M60 receipt signature is created; frozen M57/M56 failure-side effects remain authoritative;
- receipt persistence failure: no M60 signature attempt occurs; already extracted/verified files remain;
- receipt signing/input/key/output failure: persisted receipt and extracted verified files remain; detached signature follows frozen M60 output behavior;
- final M58/M60 receipt-SHA mismatch: all successfully created phase outputs remain, but M62 exits nonzero and emits no successful combined summary.

Receipt and signature paths are independently exclusive outputs. M62 does not claim a filesystem transaction, crash atomicity, or pair-level rollback.

## Success result

A successful M62 result means:

1. frozen M57/M52 freshly verified the supplied capsule/evidence under the supplied evidence public key;
2. frozen M58 persisted its canonical deterministic receipt for that successful result;
3. frozen M60 signed exact bytes read back from that persisted receipt path under the supplied receipt private key;
4. the receipt SHA-256 returned by frozen M58 equals the receipt SHA-256 signed by frozen M60.

The combined success summary extends the frozen M58 result with explicit detached receipt-signature state, receipt-signing key fingerprint, signature output path, and the common receipt SHA-256.

## Trust boundary

M62 is orchestration only. It composes existing current verification and receipt-byte authentication semantics; it does not add a trust root.

A successful result does **not** independently establish:

- human or organizational ownership of the evidence public key or receipt signing key;
- signer identity without an external trust channel;
- App Server/server/host identity;
- when evidence verification, receipt creation, or receipt signing occurred;
- historical immutability before or after the current operation;
- trusted timestamping, certificates, PKI, transparency, revocation, or KMS custody;
- transactional atomicity of the output pair;
- semantic truth beyond frozen verifier semantics.

The M60 limitation remains: anyone holding the receipt private key can sign arbitrary bounded receipt bytes. M62 narrows this operationally by signing a receipt only after fresh frozen verification and M58 persistence, but the key's external ownership/trust remains outside Harness X.

## Explicit non-goals

M62 does not add:

- a new receipt schema;
- a new detached-signature schema;
- receipt JSON parsing or mutation;
- a new Ed25519 implementation;
- public-key/private-key discovery or publication;
- automatic M59/M61 verification of the newly produced pair;
- implicit output paths or filenames;
- output overwrite/repair;
- pair rollback or crash-atomic filesystem semantics;
- App Server or browser receipt/signature creation;
- network access;
- trusted timestamps, certificates, PKI, transparency, revocation, or KMS semantics;
- changes to frozen M57, M58, M60, or M61 implementation behavior.

## Intended changed surface

M62 should remain confined to:

1. this milestone document;
2. one narrow orchestration module calling frozen M57, then M58, then M60, plus receipt-SHA correlation;
3. additive installed CLI parser/dispatch wiring for the one command;
4. focused phase-order, distinct-key-role, SHA-correlation, failure-side-effect, CLI, and source-scope tests.

Frozen M61 authenticated reconciliation, M60 receipt signing/verification, M59 reconciliation, M58 receipt rendering/persistence, M57 orchestration, M56 extraction, M52 evidence-manifest signing/verifier, M43 manifest generation, App Server/browser/runtime/evidence-generation surfaces remain outside the intended diff.

## Qualification contract

M62 cannot freeze until one fixed final head satisfies all of the following:

- exact merge base is frozen M61 `c483a6b2bef9c08bfbd80b5b4aca7dee5bfdfbaa`;
- zero commits behind frozen M61;
- source/diff audit confirms no parser, cryptographic primitive, key trust source, App Server/browser, or frozen implementation widening;
- successful end-to-end export works with distinct evidence-signing and receipt-signing keypairs;
- evidence verification failure creates no receipt or receipt signature;
- receipt persistence failure prevents any signature attempt;
- receipt-signing failure preserves the successfully persisted receipt and verified extracted files;
- different successful M58/M60 receipt SHA-256 identities are rejected;
- frozen M57/M58/M60/M61 commands remain independently available and unchanged;
- full pytest passes;
- `harness-x --help` exposes `export-signed-evidence-receipt` while retaining all existing commands;
- `harness-x validate-config configs/default.yaml` passes;
- no submitted reviews or actionable review threads remain;
- PR remains draft/open/unmerged;
- final head, synthetic merge, compare totals, CI identifiers, and test count are recorded in PR metadata without moving the branch.
