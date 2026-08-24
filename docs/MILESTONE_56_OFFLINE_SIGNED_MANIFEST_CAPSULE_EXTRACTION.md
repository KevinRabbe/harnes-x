# Milestone 56 — Offline Signed-Manifest Capsule Extraction

M56 is stacked directly on frozen M55 and closes one narrow portability gap left by the M55 single-file capsule: M55 can transport one byte-correlated manifest/signature capsule through one server response and one browser save, while the frozen M52 offline verifier still consumes the exact detached `session-evidence-manifest.json` and `session-evidence-manifest.sig.json` files separately.

M56 adds one local, offline, read-only-to-source extraction command that validates the M55 capsule's deterministic structure and byte correlation, then materializes the exact embedded frozen M43 manifest bytes and exact frozen M52 signature-envelope bytes under their existing fixed filenames. Cryptographic trust verification remains the frozen M52 `verify-evidence` path with an externally trusted public key.

M56 does **not** make capsule extraction a signature-verification or origin-authentication decision.

## Stack

Exact frozen M55 base:

`d64a9ac9c57bf021ec617a48a3418d2188a2a597`

M55 PR #62 must remain frozen draft/open/unmerged at that exact head.

M56 branch:

`agent/milestone-56-offline-signed-manifest-capsule-extraction`

The first M56 commit is this scope/authority document. The final M56 head and synthetic merge must be recorded only in PR metadata after exact-head qualification so the tracked document does not self-move the freeze gate.

## Installed CLI

M56 adds exactly one installed command:

`harness-x extract-evidence-capsule CAPSULE --output-dir DIR`

The command:

- reads one explicitly supplied local capsule file;
- writes only the two fixed output filenames inside the explicitly supplied output directory:
  - `session-evidence-manifest.json`
  - `session-evidence-manifest.sig.json`;
- accepts no caller-selected filenames;
- performs no network access;
- requires no public key and performs no Ed25519 verification;
- never mutates the source capsule;
- refuses to overwrite either output;
- does not discover sibling evidence files or recursively enumerate directories.

Existing `harness-x verify-evidence` remains unchanged and remains the sole installed cryptographic signature-verification path.

The extraction implementation is imported only after the new command is selected. Existing help and legacy command dispatch therefore do not eagerly acquire the M56/App Server capsule parsing stack.

## Capsule input boundary

The capsule input uses the established bounded regular-file/no-follow semantics from the portable-evidence tooling:

- lexical absolute normalization without following the supplied leaf;
- final-component symlink rejection;
- intermediate/parent symbolic-link substitution rejection;
- `O_NOFOLLOW` where available;
- pre-open `lstat` and post-open `fstat` regular-file checks;
- one bounded descriptor read;
- a dedicated 4 MiB capsule byte ceiling, which is larger than the frozen M44 manifest plus M52 signature ceilings after base64url expansion and capsule metadata overhead.

The exact retained bytes from that descriptor are the only capsule bytes parsed and validated.

## Strict capsule validation

Before creating either output M56 requires:

1. strict UTF-8 JSON object input;
2. duplicate JSON object keys rejected at every object level;
3. exact `app-signed-manifest-capsule-v1` top-level field set;
4. exact deterministic M55 sorted compact JSON plus one trailing newline for the capsule itself;
5. canonical lowercase 64-hex `manifest_sha256`;
6. canonical `sha256:<64 lowercase hex>` key fingerprint;
7. exact `ed25519` algorithm;
8. canonical base64url-without-padding `manifest_payload` and `signature_payload`;
9. decoded manifest length no greater than frozen M44 `MAX_EVIDENCE_MANIFEST_BYTES` (2 MiB);
10. decoded signature-envelope length no greater than frozen M52 `MAX_EVIDENCE_SIGNATURE_BYTES` (64 KiB);
11. decoded manifest bytes hash exactly to `manifest_sha256`;
12. decoded manifest bytes are strict UTF-8 JSON, satisfy the frozen M43 `app-terminal-evidence-manifest-v1` schema/self-fingerprint contract, and equal the frozen M43 canonical renderer output including its trailing newline;
13. decoded signature bytes are strict UTF-8 JSON with the exact frozen M52 envelope fields;
14. decoded signature envelope has exact `app-evidence-signature-v1` schema, `ed25519` algorithm, canonical key fingerprint, canonical Ed25519 signature text, and exact manifest SHA;
15. decoded signature bytes equal the frozen M52 canonical envelope serialization including its trailing newline;
16. capsule metadata, decoded manifest identity, and decoded signature-envelope metadata agree exactly.

The M44/M52 embedded byte ceilings are enforced before output so a successful extraction cannot materialize an otherwise structurally accepted pair that the frozen verifier rejects immediately only because one extracted file exceeds its established input limit.

M56 intentionally does **not** verify the Ed25519 signature because no trusted public key is supplied to extraction. Successful extraction proves deterministic capsule structure and byte correlation only.

## Output boundary

The output directory is explicit. M56 derives only the two frozen filenames within it and applies the existing safe exclusive-create boundary used by M52 signing outputs:

- lexical-normalized resolved-parent checks;
- parent must already exist and be a directory;
- symbolic-link parent substitution rejected;
- exclusive create;
- `O_NOFOLLOW` where available;
- regular-file descriptor validation;
- exact byte write, flush, and fsync;
- no overwrite.

Both targets are preflighted before writes. Extraction then creates the manifest followed by the signature. If signature creation fails after the manifest was created, M56 attempts to remove that newly created manifest before returning the extraction error. If rollback itself fails, that failure is surfaced explicitly together with the original extraction failure; M56 does not silently claim cleanup succeeded.

This is **not** claimed to be crash-atomic or a filesystem transaction. A process/OS crash between the two exclusive writes can leave the first file present, and an OS/filesystem failure can prevent rollback. M56 does not weaken that boundary with temporary-directory renames, archive formats, or replacement of existing files.

## Exact-byte preservation

Extraction parses reconstructed models only for validation. It never writes reconstructed manifest or signature serialization.

The validated decoded byte strings retained from the capsule are written exactly as embedded. Therefore, for a valid capsule produced by M55:

- extracted `session-evidence-manifest.json` is byte-for-byte the frozen M43 manifest response body that M55 signed;
- extracted `session-evidence-manifest.sig.json` is byte-for-byte the frozen M52 detached signature envelope embedded by M55;
- the extracted pair can be supplied directly to frozen `harness-x verify-evidence ... --signature ... --public-key ...`.

Focused integration qualification proves the extracted files verify successfully through the existing M52 verifier with the matching external public key.

## Authority / trust boundary

M56 establishes no new trust authority.

Successful extraction proves only that:

- the supplied local capsule passed M55 structural/canonical correlation checks;
- the exact decoded manifest bytes match the capsule's manifest SHA;
- the exact decoded signature envelope names those same manifest bytes and key-fingerprint identifier;
- the two exact decoded byte strings were written under the frozen filenames.

It does **not** prove:

- Ed25519 signature validity;
- public-key ownership or signer identity;
- App Server/server/host identity;
- human/operator identity;
- signature/completion time;
- uncompromised private-key custody;
- semantic truth of lifecycle/report/trace claims;
- historical immutability of source evidence;
- filesystem crash atomicity.

The operator must still obtain the public key through an external trusted channel and use frozen M52 verification for the cryptographic claim.

## Source-audit hardening

M56 source audit deliberately tightened two boundaries after the first source-complete implementation:

1. the extractor originally bounded only the outer capsule file; it now also enforces the exact frozen M44 2 MiB manifest ceiling and M52 64 KiB signature-envelope ceiling before output, so successful extraction remains directly compatible with the frozen verifier's file boundary;
2. ordinary second-output failure originally attempted rollback but swallowed an `unlink` failure; final M56 surfaces rollback failure explicitly and has a regression proving that a remaining partial manifest is never misreported as successfully cleaned up.

Neither hardening changes trust semantics or App Server behavior.

## Explicit non-goals

M56 does not add:

- a new signature verifier;
- public-key publication or discovery;
- browser cryptographic verification;
- network fetching;
- ZIP/tar/session archives;
- generic evidence-bundle extraction;
- snapshot/lifecycle/report/trace embedding or discovery;
- arbitrary capsule-selected output paths;
- overwrite/repair behavior;
- trusted timestamps, PKI, certificates, transparency, rotation/revocation registries, or KMS custody;
- App Server HTTP/UI/session/runtime changes.

## Intended changed surface

M56 remains confined to:

1. this milestone document;
2. one offline capsule extraction/validation module;
3. one narrow installed CLI wrapper extension for `extract-evidence-capsule`;
4. focused extraction, rollback, boundary, compatibility, and M52 round-trip verification tests.

Frozen M43 manifest generation, M52 signing/verifier implementation, M53/M55 App Server signing/capsule implementations, M54 browser pair client, M55 browser capsule client, App Server HTTP/UI/store/service/protocol/runtime, report/trace/snapshot/lifecycle generation, coding runtime/verifier, model/tool, memory, budget, controller, and control implementation are outside the intended diff.

## Qualification contract

M56 cannot freeze until all of the following are true on one fixed final head:

- exact merge base is frozen M55 `d64a9ac9c57bf021ec617a48a3418d2188a2a597`;
- zero commits behind frozen M55;
- source/diff audit confirms no App Server or trust-authority widening;
- focused strict parsing/canonical correlation/output-boundary tests pass;
- extracted valid M55 output verifies through frozen M52 `verify-evidence` with a matching public key;
- full pytest passes;
- `harness-x --help` passes and exposes `extract-evidence-capsule` while retaining existing commands;
- `harness-x validate-config configs/default.yaml` passes;
- no submitted reviews or actionable review threads remain;
- PR remains draft/open/unmerged;
- exact final head, synthetic merge, compare totals, CI identifiers, and test count are recorded in PR metadata without moving the branch.
