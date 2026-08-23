# Milestone 52 — Detached Evidence Signatures

M52 is stacked directly on frozen M51 and adds an optional cryptographic binding for the already-portable terminal evidence manifest. M43–M47 prove internal consistency of the exported manifest/snapshot/lifecycle/report/trace set, but a self-consistent rewritten set has no cryptographic claim that a holder of a particular signing key approved the manifest bytes.

M52 adds **offline detached Ed25519 signatures over the exact manifest bytes**. It deliberately does not yet make the App Server sign responses automatically; server-integrated signing may be a later milestone. M52 therefore establishes the portable key/signature format, safe key-generation/signing CLI, and verification boundary first.

The authority claim is intentionally narrow: successful verification proves only that the supplied public key validates the supplied signature over the exact manifest bytes. M52 does not establish who owns that public key, when signing occurred, whether the manifest came directly from an App Server, or whether the key is trusted by any third party.

## Scope

M52 adds three installed-CLI surfaces while preserving all pre-M52 legacy commands:

- `harness-x evidence-keygen --private-key PATH --public-key PATH`
- `harness-x sign-evidence MANIFEST --private-key PATH --output PATH`
- `harness-x verify-evidence MANIFEST ... --signature PATH --public-key PATH`

The signature file is fixed-schema deterministic JSON and contains no timestamp, hostname, username, path, or other ambient machine metadata.

No App Server HTTP route, browser UI, session/runtime/evidence generation authority, verifier/completion authority, model/tool, memory, budget, controller, or control policy changes in M52.

## Cryptographic dependency boundary

M52 uses `cryptography` Ed25519 primitives rather than implementing cryptography in Harness X.

- standard/base Harness X installs remain unchanged;
- a new optional `evidence-signing` extra provides the cryptographic dependency;
- the development extra also includes that dependency so Linux CI can qualify the feature;
- CLI signing/key-generation imports are lazy and do not affect pre-M52 commands;
- unsigned `verify-evidence` remains available without invoking signature code.

If signature functionality is requested without the optional dependency, the CLI must fail visibly with an actionable error rather than silently skipping verification.

## Key format and generation

M52 uses standard unencrypted PEM:

- Ed25519 PKCS8 private key PEM;
- Ed25519 SubjectPublicKeyInfo public key PEM.

`evidence-keygen`:

- generates exactly one Ed25519 keypair using `cryptography`;
- refuses to overwrite either target;
- creates the private-key file with owner-only mode `0600` on POSIX systems;
- creates the public-key file only after private-key creation succeeds;
- cleans up a newly created private-key file if public-key creation fails;
- never prints private key material;
- prints only a compact success summary containing the public-key fingerprint and paths supplied by the operator.

Private keys are operator-managed files. Harness X does not add a keychain, passphrase manager, cloud KMS integration, or key escrow.

## Public-key identity

The M52 key identifier is:

`sha256:<64 lowercase hex>`

computed over the raw 32-byte Ed25519 public key.

This fingerprint is an identifier, not a trust decision. Verifiers must obtain the expected public key through some external trusted channel if identity matters.

## Detached signature format

Default output filename is operator-selected through `--output`; recommended filename is `session-evidence-manifest.sig.json`.

The exact JSON object is:

```json
{
  "schema_version": "app-evidence-signature-v1",
  "algorithm": "ed25519",
  "key_fingerprint": "sha256:<64 lowercase hex>",
  "manifest_sha256": "<64 lowercase hex>",
  "signature": "<base64url without padding>"
}
```

Rules:

- root must be a JSON object;
- no duplicate keys at any object level;
- no unknown fields;
- `algorithm` is exactly `ed25519`;
- `manifest_sha256` is SHA-256 of the exact manifest file bytes;
- `signature` is exactly the Ed25519 signature over those same exact bytes, encoded base64url without padding;
- serialization is deterministic sorted-key compact JSON followed by exactly one newline;
- the envelope is not self-signed; the Ed25519 signature covers only the manifest bytes.

## Signing boundary

`sign-evidence` is offline and read-only with respect to evidence inputs.

It must:

1. read the manifest through the same bounded regular-file/no-follow path boundary used by portable evidence verification;
2. refuse oversized/nonregular/symlink-substituted input under that boundary;
3. load exactly one Ed25519 private key from a regular local file under an equivalent bounded/no-follow boundary;
4. compute the exact manifest SHA-256;
5. sign the exact manifest bytes deterministically;
6. emit the strict signature envelope;
7. refuse to overwrite the output path;
8. create the output as a regular file with no caller-controlled content other than the requested path.

Signing does not reparse or reinterpret the manifest. It signs bytes, not a reconstructed JSON object.

## Verification boundary

`verify-evidence` remains backward-compatible when `--signature` and `--public-key` are omitted.

Signature verification is opt-in and requires both options together. Supplying only one is a CLI error.

When both are supplied, M52 must:

1. run the existing M47 portable-evidence verification unchanged first;
2. use the same exact manifest bytes identity returned by that verification;
3. read the signature envelope and public key through bounded regular-file/no-follow boundaries;
4. validate the signature envelope schema strictly, including duplicate-key rejection;
5. require the envelope manifest SHA-256 to equal the already-verified manifest SHA-256;
6. require the envelope key fingerprint to equal the supplied public key fingerprint;
7. verify Ed25519 over the exact manifest bytes;
8. report `signature=verified key=<fingerprint>` only after all checks succeed.

A cryptographically valid signature over different bytes, a mismatched public key, malformed PEM, non-Ed25519 key, bad envelope, bad base64url, or invalid signature must fail visibly.

M52 must not weaken or bypass snapshot/lifecycle/report/trace checks. Signature validity is an additional condition, not a replacement for evidence consistency verification.

## Path and file-safety boundary

M52 reuses the M44/M47 principles:

- lexical path normalization may accept legitimate `nested/../file` spelling;
- resolved-path equality rejects intermediate symlink traversal;
- leaf `lstat` rejects symlinks and nonregular files;
- `O_NOFOLLOW` is used where available;
- bounded one-descriptor reads prevent size/race substitution after validation;
- key/signature size limits are deliberately small and independent of evidence-size limits.

Output creation must refuse overwrite and symlink targets by using exclusive creation.

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
- immutability after signing beyond ordinary signature verification;
- semantic truth of the task, verifier, report, trace, or lifecycle claims.

A malicious party holding the private key can sign a rewritten but self-consistent evidence set. Key custody and public-key trust remain external operator responsibilities.

## Non-goals

M52 does not add:

- automatic server signing;
- browser signature download;
- public-key publication endpoint;
- key rotation/revocation registry;
- certificate issuance;
- ZIP/session bundle;
- network verification;
- remote trust service.

## Deterministic acceptance

Before freeze, M52 must prove:

- exact frozen M51 base `6e92b096fe2eae3c23e7602874e584b4854ee2f6`;
- this scope document is the first M52 commit;
- M51 PR #58 remains unchanged, draft/open/unmerged;
- base installs remain free of the cryptographic dependency and the new dependency lives in optional signing/dev extras;
- Ed25519 key generation emits standard PEM and refuses overwrite;
- private key mode is owner-only on POSIX;
- public-key fingerprint is SHA-256 over raw 32-byte public key;
- signing covers exact manifest bytes and deterministic envelope bytes;
- signing refuses unsafe/oversized manifest/private-key inputs and output overwrite;
- verification is unchanged when signature options are omitted;
- signature/public-key options are pairwise required;
- strict envelope parsing rejects duplicates/unknown fields/invalid base64url/algorithm/fingerprint/hash;
- verification rejects wrong key, manifest mutation, envelope mutation, invalid signature, non-Ed25519 keys, symlinks, and oversize inputs;
- successful verification summary includes signature status and key fingerprint;
- no App Server/browser/session/runtime/evidence-generation/model/tool/memory/controller/control authority changes;
- exact M51→M52 diff remains narrow and source-audited;
- exact-head Linux CI passes including installed `harness-x --help` and `harness-x validate-config configs/default.yaml`.
