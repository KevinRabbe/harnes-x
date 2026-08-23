# Milestone 52 — Detached Evidence Signatures

M52 is stacked directly on frozen M51 and adds an optional cryptographic binding for the already-portable terminal evidence manifest. M43–M47 prove internal consistency of the exported manifest/snapshot/lifecycle/report/trace set, but a self-consistent rewritten set has no cryptographic claim that a holder of a particular signing key approved the manifest bytes.

M52 adds **offline detached Ed25519 signatures over the exact manifest bytes**. It deliberately does not make the App Server sign responses automatically. The authority claim is narrow: successful verification proves only that the supplied public key validates the supplied signature over the exact supplied manifest bytes. It does not prove App Server origin, signer identity, trusted time, or third-party trust.

## Frozen-scope surfaces

M52 adds three installed-CLI surfaces while preserving all pre-M52 legacy commands:

- `harness-x evidence-keygen --private-key PATH --public-key PATH`
- `harness-x sign-evidence MANIFEST --private-key PATH --output PATH`
- `harness-x verify-evidence MANIFEST ... --signature PATH --public-key PATH`

No App Server HTTP route, browser UI, session/runtime/evidence-generation authority, verifier/completion authority, model/tool, memory, budget, controller, or control policy changes in M52.

## Cryptographic dependency boundary

M52 uses `cryptography` Ed25519 primitives rather than implementing cryptography in Harness X.

- base project dependencies remain only the pre-M52 dependencies;
- a new optional `evidence-signing` extra provides `cryptography>=45,<47`;
- the development extra also includes the same dependency so Linux CI can qualify the feature;
- `cli_entry.py` still imports only the legacy CLI eagerly;
- M52 signing/key-generation/verification modules are imported lazily inside the relevant command branch;
- importing the installed CLI for a legacy command does not import `cryptography`;
- unsigned `verify-evidence` does not execute any M52-specific bounded manifest read or cryptographic primitive and preserves the exact pre-M52 success summary.

If signature functionality is invoked without the optional cryptographic dependency, M52 fails visibly with an actionable `evidence-signing`-extra error rather than silently skipping work.

## Key format and key identity

M52 uses standard unencrypted PEM:

- Ed25519 PKCS8 private key PEM;
- Ed25519 SubjectPublicKeyInfo public key PEM.

`evidence-keygen` generates exactly one Ed25519 keypair using `cryptography`, refuses overwrite, writes the private key with mode `0600` on POSIX, writes the public key only after private creation succeeds, and removes the newly created private file if public creation fails. Private key material is never printed.

The M52 key identifier is:

`sha256:<64 lowercase hex>`

computed over the raw 32-byte Ed25519 public key. This fingerprint is an identifier only, not a trust decision. The verifier must obtain an expected public key through an external trusted channel if identity matters.

## Detached signature format

The recommended filename is `session-evidence-manifest.sig.json`; the operator selects the actual path through `--output`.

The strict JSON object is:

```json
{
  "schema_version": "app-evidence-signature-v1",
  "algorithm": "ed25519",
  "key_fingerprint": "sha256:<64 lowercase hex>",
  "manifest_sha256": "<64 lowercase hex>",
  "signature": "<base64url without padding>"
}
```

The root must be an object; duplicate and unknown keys are rejected; `algorithm` is exactly `ed25519`; the signature is the canonical base64url-without-padding encoding of one 64-byte Ed25519 signature; and serialization is deterministic sorted-key compact JSON followed by exactly one newline.

The Ed25519 signature covers **only the exact manifest file bytes**. The envelope is not independently self-signed and contains no timestamp, hostname, username, path, nonce, or ambient machine metadata.

## Signing boundary

`sign-evidence` is offline and read-only with respect to evidence inputs.

It:

1. reads the manifest through the frozen M44 bounded regular-file/no-follow path boundary and the existing manifest limit;
2. reads one unencrypted Ed25519 private PEM through the same boundary with an independent 16 KiB key limit;
3. computes SHA-256 over the exact manifest bytes;
4. signs those exact bytes without reparsing or reconstructing JSON;
5. emits the deterministic strict signature envelope;
6. creates the output using exclusive create plus `O_NOFOLLOW` where available;
7. refuses overwrite and symbolic-link parent substitution;
8. validates the new descriptor is a regular file;
9. flushes and fsyncs output;
10. removes a newly created partial output if a write/fsync failure occurs.

Key generation uses the same exclusive-output helper. A focused regression proves a public-key creation failure removes the private key that M52 created earlier in the same operation.

## Verification boundary

`verify-evidence` remains backward-compatible when `--signature` and `--public-key` are omitted. The two options are pairwise required when signature verification is requested.

When both are supplied, M52:

1. runs frozen M47 portable-evidence verification first, including any supplied snapshot/lifecycle/report/trace evidence;
2. rereads the manifest through the bounded one-descriptor boundary and requires byte count plus SHA-256 to equal the manifest identity already returned by M47, preventing a combined success across two different manifest versions;
3. reads the signature envelope with an independent 64 KiB limit;
4. reads the public key with the independent 16 KiB key limit;
5. validates strict UTF-8/JSON/envelope schema and duplicate-key rules;
6. requires the envelope manifest SHA-256 to equal the exact manifest SHA-256;
7. requires the envelope key fingerprint to equal the supplied public-key fingerprint;
8. verifies Ed25519 over the exact manifest bytes;
9. appends `signature=verified key=<fingerprint>` only after every check succeeds.

Wrong keys, manifest mutation, signature/envelope mutation, invalid base64url, malformed/non-Ed25519 keys, symlinks, nonregular files, and oversized key/signature inputs fail visibly. Signature validity is an additional condition and never bypasses evidence-consistency verification.

## Path and output safety

M52 intentionally reuses the M44/M47 path model rather than inventing a second one:

- `expanduser` plus lexical absolute normalization;
- resolved-path equality to reject intermediate symlink traversal;
- leaf `lstat` symlink/nonregular rejection for inputs;
- `O_NOFOLLOW` where available;
- bounded one-descriptor reads;
- exclusive output creation and no overwrite.

The independent limits are:

- private/public key input: 16 KiB;
- detached signature envelope: 64 KiB;
- manifest: existing M43/M44 manifest limit.

## Authority boundary

M52 proves **key possession over exact manifest bytes only**.

It does not prove:

- App Server origin;
- that signing happened at session completion;
- trusted timestamp or chronology;
- public-key ownership or identity;
- certificate-chain or PKI trust;
- transparency-log inclusion;
- remote attestation;
- hardware-backed key custody;
- semantic truth of task/verifier/report/trace/lifecycle claims.

A party holding the private key can sign a rewritten but self-consistent evidence set. Key custody and public-key trust remain external operator responsibilities.

M52 also deliberately does not add automatic server signing, browser signature download, public-key publication, key rotation/revocation registry, certificates, a ZIP/session bundle, network verification, or a remote trust service.

## Source-audit hardening

The source audit produced two implementation/compatibility hardenings before freeze qualification:

1. exclusive output creation originally could leave a newly created partial file if a post-open write/fsync failed. `_exclusive_write()` now removes a newly created file on any failed creation path; a focused injected-fsync regression pins the behavior.
2. the first M52 wrapper summary appended `signature=not_supplied key=none` on unsigned verification. That unnecessarily changed the qualified pre-M52 CLI output. Final M52 returns the exact underlying M47 summary when no signature is supplied, while still exposing internal `signature_status=not_supplied` to callers.

The source audit also confirmed that M52 imports no network client, signs bytes rather than reconstructed JSON, runs evidence consistency before signature verification, and changes no App Server/browser/session/runtime/evidence-generation authority.

## Fail-visible qualification history

### CI #1341 — provisional failure

Head: `8282b97b088abcbb4a889aab2d86baca220f8239`

- run id: `32672902722`
- job id: `97276348218`
- synthetic merge: `7db78a8d7c1031e66971480d970f5e61b99af7ba`
- Ubuntu 24.04.4 LTS / Python 3.12.14 / Actions Node 24
- `1 failed, 649 passed in 124.23s`
- help/config skipped after pytest failure
- sole failure: the symlink regression expected the M52 subclass `EvidenceSigningError`; the reused frozen M44 reader correctly raised its established parent `PortableEvidenceVerificationError`
- implementation symlink rejection was correct; the test contract was fixed without changing implementation.

### CI #1343 — provisional failure

Head: `b40fcfd2e686a5481f25757631282c3952f2f666`

- run id: `32672988112`
- job id: `97276557769`
- synthetic merge: `3da0136123c1b155c310b31c880989f76006e412`
- `1 failed, 654 passed in 120.40s`
- help/config skipped
- same pre-fix symlink exception-type assertion; newly added dependency/output-safety regressions otherwise reached the same correct implementation boundary.

### CI #1345 — provisional green

Head: `585e802ee95f01041a1fe31028b8ee6dd4fc5417`

- run id: `32673071619`
- job id: `97276762752`
- synthetic merge: `cdd41215174e6a56756c49ee9243fda696e30b8f`
- `655 passed in 114.00s`
- `harness-x --help`: PASS
- `harness-x validate-config configs/default.yaml`: PASS
- superseded because explicit independent key/signature size-limit regressions were added afterward.

### CI #1347 — provisional green

Head: `6aa6ae532e80f18fbef854f729c92401476945b6`

- run id: `32673204178`
- job id: `97277083732`
- synthetic merge: `9f9fc25d4f8e784c707e4f0ea81a41281eaf5d91`
- Ubuntu 24.04.4 LTS / Python 3.12.14 / Actions Node 24
- `657 passed in 66.54s`
- `harness-x --help`: PASS and exposes `verify-evidence`, `evidence-keygen`, and `sign-evidence`
- `harness-x validate-config configs/default.yaml`: PASS
- config output: `valid: system_version=0.1.0-alpha.0`
- superseded only by this final milestone-contract commit.

## Deterministic freeze acceptance

M52 freezes only if the final documented head proves:

- exact frozen M51 base `6e92b096fe2eae3c23e7602874e584b4854ee2f6` and zero commits behind;
- this scope document remains the first M52 commit `5a6e616e7707920be990aba742fd817a6813094a`;
- M51 PR #58 remains unchanged, draft/open/unmerged;
- base install remains free of `cryptography`; signing/dev extras contain it;
- Ed25519 key generation, key fingerprinting, exact-byte deterministic signing, safe output creation, rollback, strict envelope parsing, wrong-key/tamper rejection, pairwise CLI options, no-signature compatibility, symlink/nonregular/oversize boundaries are qualified;
- no App Server/browser/session/runtime/evidence-generation/model/tool/memory/controller/control authority changes;
- exact M51→M52 diff remains narrow and source-audited;
- exact-head Linux CI passes, including installed `harness-x --help` and `harness-x validate-config configs/default.yaml`.
