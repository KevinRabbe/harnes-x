# Milestone 60 — Detached Verification Receipt Signatures

M60 is stacked directly on frozen M59 and closes one narrow authenticity gap left by the unsigned M58 receipt. M59 can establish that supplied receipt bytes are currently consistent with a fresh frozen verification result, but neither M58 nor M59 authenticates who supplied or retained those receipt bytes.

M60 adds a distinct detached Ed25519 signature envelope for exact verification-receipt bytes, using the existing operator-managed Ed25519 key model. It deliberately does **not** claim that signing a receipt proves the recorded verification happened, that the receipt was originally produced by Harness X, or when any verification/signing occurred.

## Stack

Exact frozen M59 base:

`b5130dcbf63c54a007b12210f46ea02b661c62c5`

M59 PR #66 must remain frozen draft/open/unmerged at that exact head.

M60 branch:

`agent/milestone-60-detached-verification-receipt-signatures`

The first M60 commit is this scope/authority document. Final head and synthetic-merge evidence belong in PR metadata after exact-head qualification.

## Installed CLI

M60 adds exactly two installed commands:

`harness-x sign-evidence-receipt RECEIPT --private-key PATH --output PATH`

`harness-x verify-evidence-receipt-signature RECEIPT --signature PATH --public-key PATH`

The existing `evidence-keygen` command remains the operator-managed key-generation path. M60 adds no key discovery, key registry, certificate, or network source.

## Signature envelope

M60 defines a distinct strict envelope schema:

`app-evidence-verification-receipt-signature-v1`

Exact fields:

- `schema_version`: exact `app-evidence-verification-receipt-signature-v1`;
- `algorithm`: exact `ed25519`;
- `key_fingerprint`: canonical `sha256:<64 lowercase hex>` fingerprint of the raw Ed25519 public key;
- `receipt_sha256`: lowercase SHA-256 of the exact signed receipt bytes;
- `signature`: canonical unpadded base64url text for the 64-byte Ed25519 signature.

Envelope serialization is deterministic sorted compact UTF-8 JSON with exactly one trailing newline.

The M60 envelope is deliberately separate from frozen M52 `app-evidence-signature-v1`; M60 does not overload M52's semantically specific `manifest_sha256` field to mean a receipt hash.

## Signing boundary

Signing:

1. reads one explicitly supplied local receipt through the established bounded regular-file/no-follow boundary, using the frozen M59 receipt byte ceiling;
2. loads an explicitly supplied unencrypted Ed25519 private key through the frozen M52 key boundary;
3. signs the exact retained receipt bytes;
4. derives the public-key fingerprint from that private key;
5. creates the strict M60 detached envelope naming the exact receipt SHA-256;
6. writes the envelope once through the frozen M52 resolved-parent/exclusive-create/no-follow output boundary.

Signing does **not** parse the receipt JSON, run M59 reconciliation, or assert that the receipt is a valid M58 receipt. It authenticates exact bytes only.

## Verification boundary

Verification:

1. reads the supplied receipt once through the same bounded/no-follow receipt boundary;
2. reads one supplied detached M60 envelope through a dedicated bounded/no-follow signature-envelope boundary;
3. requires strict UTF-8 JSON, duplicate-key rejection, exact M60 schema fields, canonical signature text, and canonical deterministic envelope serialization;
4. requires envelope `receipt_sha256` to equal the exact retained receipt bytes;
5. loads the explicitly supplied Ed25519 public key through the frozen M52 key boundary;
6. requires envelope key fingerprint to equal that public key's fingerprint;
7. verifies the Ed25519 signature over the exact retained receipt bytes.

A successful result is an exact-byte Ed25519 authenticity check relative to the supplied public key.

## Relationship to M58/M59

M60 is intentionally orthogonal to M59 reconciliation.

- M59 answers: "Do these supplied receipt bytes equal what frozen M58 would render now after fresh frozen verification of the supplied evidence?"
- M60 answers: "Does this detached signature verify over these exact supplied receipt bytes under this supplied public key?"

Neither answer establishes the external trustworthiness of the public key. Operators must obtain the public key through an external trusted channel.

M60 does not automatically invoke M59, and M59 does not automatically require M60. They may be composed by an operator when both current consistency and receipt-byte authenticity are desired.

## Trust boundary

A valid M60 signature proves possession of the corresponding private key for the exact receipt bytes, assuming the supplied public key is externally trusted.

It does **not** independently prove:

- that the receipt is canonical M58 output;
- that the verification recorded by the receipt actually ran;
- that M59 reconciliation succeeded;
- when the receipt or signature was created;
- who controls the signing key in human/organizational terms;
- public-key ownership or signer identity without an external trust channel;
- App Server/server/host identity;
- historical immutability;
- non-repudiation or trusted timestamping.

Anyone holding the private key can sign arbitrary bounded receipt bytes, including fabricated content. This limitation must remain explicit in CLI/docs/tests.

## File/output semantics

Receipt, key, and detached-signature inputs reuse established bounded regular-file/no-follow semantics. Signature output reuses the frozen M52 exclusive-create boundary:

- parent must already exist and resolve without symbolic-link substitution;
- no overwrite;
- `O_NOFOLLOW` where available;
- regular-file descriptor validation;
- exact write, flush, and fsync.

No crash-atomic or filesystem-transaction claim is made.

## Explicit non-goals

M60 does not add:

- automatic M58 receipt parsing or validation;
- automatic M59 reconciliation;
- receipt mutation/repair;
- trusted timestamps;
- certificates, PKI, transparency, revocation, or KMS semantics;
- public-key publication/discovery;
- App Server or browser signing/verification;
- network access;
- changes to frozen M52 signing/verifier behavior;
- changes to frozen M58/M59 receipt behavior.

## Intended changed surface

M60 should remain confined to:

1. this milestone document;
2. one narrow detached receipt-signing/verification module reusing frozen M52 key/signature primitives and file boundaries;
3. additive installed CLI parser/dispatch wiring for the two commands;
4. focused exact-byte/canonical-envelope/wrong-key/tamper/output-boundary/source-scope tests.

Frozen M59 reconciliation, M58 receipt rendering/persistence, M57 orchestration, M56 extraction, M52 evidence-manifest signing/verifier, M43 manifest generation, App Server/browser/runtime/evidence-generation surfaces remain outside the intended diff.

## Qualification contract

M60 cannot freeze until one fixed final head satisfies all of the following:

- exact merge base is frozen M59 `b5130dcbf63c54a007b12210f46ea02b661c62c5`;
- zero commits behind frozen M59;
- source/diff audit confirms no receipt parser/reconciliation coupling, new key trust source, App Server/browser, or frozen M52 implementation movement;
- exact receipt bytes sign and verify successfully under a matching keypair;
- one-byte receipt tamper, wrong public key, envelope hash tamper, noncanonical envelope serialization, duplicate keys, and invalid signature text fail nonzero;
- signing refuses overwrite and respects receipt/key/output no-follow boundaries;
- existing M58/M59 commands remain available and unchanged;
- full pytest passes;
- `harness-x --help` exposes both new commands and retains all existing commands;
- `harness-x validate-config configs/default.yaml` passes;
- no submitted reviews or actionable review threads remain;
- PR remains draft/open/unmerged;
- final head, synthetic merge, compare totals, CI identifiers, and test count are recorded in PR metadata without moving the branch.
