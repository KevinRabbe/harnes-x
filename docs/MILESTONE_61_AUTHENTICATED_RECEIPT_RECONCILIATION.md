# Milestone 61 — Authenticated Receipt Reconciliation

M61 is stacked directly on frozen M60 and composes two already-frozen, intentionally orthogonal receipt checks. Frozen M59 establishes current consistency between supplied M58 receipt bytes and a fresh frozen capsule/evidence verification. Frozen M60 establishes detached Ed25519 authenticity of exact supplied receipt bytes relative to an externally trusted receipt-signing public key.

M61 adds one local orchestration command that requires both checks to succeed for the same receipt byte identity. It adds no receipt parser, cryptographic primitive, key-discovery source, timestamp, certificate, App Server behavior, browser behavior, or evidence-generation path.

## Stack

Exact frozen M60 base:

`54bcfea387e5012128a786a8aa13900138b65c3e`

M60 PR #67 must remain frozen draft/open/unmerged at that exact head.

M61 branch:

`agent/milestone-61-authenticated-receipt-reconciliation`

The first M61 commit is this scope/authority document. Final head and synthetic-merge evidence belong only in PR metadata after exact-head qualification.

## Installed CLI

M61 adds exactly one installed command:

`harness-x verify-authenticated-evidence-receipt RECEIPT CAPSULE --receipt-signature PATH --receipt-public-key PATH --evidence-public-key PATH --output-dir DIR [--snapshot PATH] [--lifecycle PATH] [--report PATH] [--trace PATH]`

The command deliberately exposes two public-key roles:

- `--receipt-public-key` authenticates exact receipt bytes through frozen M60;
- `--evidence-public-key` performs frozen M59/M57/M52 current evidence verification and reconciliation.

The two paths may name the same key, but M61 does not assume that they do. Receipt signing and evidence-manifest signing are distinct authority roles.

## Orchestration order

M61 has exactly two frozen phases:

1. call frozen M60 `verify_evidence_verification_receipt_signature()` on the supplied receipt, detached receipt signature, and receipt-signing public key;
2. only after M60 succeeds, call frozen M59 `reconcile_evidence_verification_receipt()` on the same supplied receipt and capsule using the evidence-signing public key plus any explicitly supplied snapshot/lifecycle/report/trace paths.

This ordering is intentional. A malformed, tampered, or unauthenticated receipt/signature fails before M59 can create fresh extracted manifest/signature files.

M61 does not reproduce either phase's parsing, canonicalization, key handling, Ed25519 verification, capsule extraction, evidence consistency verification, or receipt rendering.

## Cross-read receipt identity

Frozen M60 and frozen M59 each independently perform their qualified bounded/no-follow read of the receipt path. M61 must not claim a combined result unless those two successful phases refer to the same receipt byte identity.

Therefore, after both phases return successfully, M61 requires:

`M60.receipt_sha256 == M59.receipt_sha256`

A mismatch is a fail-visible cross-read substitution error and produces no successful combined summary.

This SHA-256 identity pin does not add a third receipt read and does not parse receipt JSON. It only correlates the exact byte identities already returned by the two frozen phases.

If receipt bytes change between phases and M59 subsequently succeeds on different bytes, M61 rejects after M59. Any fresh extracted files produced by M59 remain subject to frozen M59/M57 filesystem semantics; M61 does not roll them back.

## Success result

A successful M61 result means all of the following are true:

1. frozen M60 verified the detached Ed25519 signature over exact receipt bytes under the explicitly supplied receipt public key;
2. frozen M59 established that the same receipt byte identity equals frozen M58 output for a fresh frozen M57/M52 verification of the supplied capsule/evidence inputs under the explicitly supplied evidence public key;
3. the receipt SHA-256 returned by both frozen phases is identical.

The deterministic success summary extends frozen M59's success summary with explicit receipt-authentication state and the receipt-signing key fingerprint. It must distinguish the receipt-signing key from the evidence-signing key already reported by frozen M59/M52.

## Failure semantics

M61 preserves phase authority and side effects:

- M60 receipt/signature/public-key input or cryptographic failure occurs before M59 and therefore before fresh capsule extraction;
- after M60 succeeds, M59 failures retain exactly frozen M59/M57 behavior, including possible retained extracted files after later evidence-key or reconciliation failure;
- post-success receipt-SHA disagreement fails after both frozen calls and does not delete M59 outputs;
- M61 emits no successful `valid:` result unless both frozen phases and the SHA identity correlation succeed.

M61 does not catch and reinterpret failure classes except through the installed CLI's existing `PortableEvidenceVerificationError` error surface.

## Trust boundary

M61 composes two claims; it does not create a third trust root.

A successful M61 result establishes, relative to externally trusted supplied public keys:

- current receipt consistency with fresh frozen evidence verification under the evidence public key; and
- detached receipt-byte authenticity under the receipt public key.

It does **not** independently establish:

- human or organizational ownership of either public key;
- signer identity without an external trust channel;
- App Server/server/host identity;
- when the evidence verification ran;
- when the receipt or detached signature was created;
- historical immutability before the current check;
- trusted timestamping, certificates, PKI, transparency, revocation, or KMS custody;
- semantic truth of the underlying evidence claims beyond frozen verifier semantics.

Anyone holding the relevant private key can produce signatures under that key. External key trust remains an operator responsibility.

## Explicit non-goals

M61 does not add:

- a new Ed25519 implementation;
- receipt JSON parsing or schema validation;
- receipt/signature mutation or repair;
- automatic key discovery/publication;
- a requirement that evidence and receipt signing use the same key;
- implicit temporary extraction;
- automatic cleanup of frozen M59/M57 outputs on later failure;
- App Server or browser routes/UI;
- network access;
- trusted timestamps, certificates, PKI, transparency, revocation, or KMS semantics;
- changes to frozen M59 reconciliation or frozen M60 signing/verification behavior.

## Intended changed surface

M61 should remain confined to:

1. this milestone document;
2. one narrow orchestration module that calls frozen M60 then frozen M59 and correlates their receipt SHA-256 values;
3. additive installed CLI parser/dispatch wiring for the one command;
4. focused phase-order, two-key-role, SHA-correlation, failure-side-effect, CLI, and source-scope tests.

Frozen M60 receipt-signature implementation, M59 reconciliation, M58 receipt rendering/persistence, M57 orchestration, M56 extraction, M52 evidence-manifest signing/verifier, M43 manifest generation, App Server/browser/runtime/evidence-generation surfaces remain outside the intended diff.

## Qualification contract

M61 cannot freeze until one fixed final head satisfies all of the following:

- exact merge base is frozen M60 `54bcfea387e5012128a786a8aa13900138b65c3e`;
- zero commits behind frozen M60;
- source/diff audit confirms no cryptographic primitive, receipt parser, key trust source, App Server/browser, or frozen implementation widening;
- successful composition works with distinct evidence-signing and receipt-signing keypairs;
- invalid receipt signature and wrong receipt public key fail before fresh extraction;
- M60 success followed by stale receipt, wrong evidence public key, or other M59 failure remains nonzero under frozen M59 side-effect semantics;
- different successful M60/M59 receipt SHA-256 values are rejected by M61;
- frozen M59 and M60 commands remain independently available and unchanged;
- full pytest passes;
- `harness-x --help` exposes `verify-authenticated-evidence-receipt` while retaining all existing commands;
- `harness-x validate-config configs/default.yaml` passes;
- no submitted reviews or actionable review threads remain;
- PR remains draft/open/unmerged;
- final head, synthetic merge, compare totals, CI identifiers, and test count are recorded in PR metadata without moving the branch.
