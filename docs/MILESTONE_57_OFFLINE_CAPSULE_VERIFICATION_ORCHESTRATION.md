# Milestone 57 — Offline Capsule Verification Orchestration

M57 is stacked directly on frozen M56 and closes one narrow usability gap in the signed-manifest capsule workflow. M55 produces one deterministic single-file signed-manifest capsule. M56 validates that capsule and materializes the exact embedded M43 manifest and M52 detached-signature files. Frozen M52 then verifies those exact files with an externally trusted public key. Before M57, performing the complete cryptographic check requires two explicit CLI invocations.

M57 adds one local orchestration command that performs those already-frozen operations in order: first M56 exact-byte extraction into an explicitly supplied directory, then frozen M52 evidence/signature verification over the exact extracted paths. It adds no signature primitive, public-key source, trust registry, App Server behavior, browser behavior, or evidence-reconstruction path.

## Stack

Exact frozen M56 base:

`7ce29e7a2d98c96c54cf5356dd337342bc1db6c1`

M56 PR #63 must remain frozen draft/open/unmerged at that exact head.

M57 branch:

`agent/milestone-57-offline-capsule-verification-orchestration`

The first M57 commit is this scope/authority document. The final M57 head and synthetic merge are recorded only in PR metadata after exact-head qualification so the tracked contract does not self-move the freeze gate.

## Installed CLI

M57 adds exactly one installed command:

`harness-x verify-evidence-capsule CAPSULE --output-dir DIR --public-key PATH [--snapshot PATH] [--lifecycle PATH] [--report PATH] [--trace PATH]`

The command has two explicit phases:

1. invoke frozen M56 `extract_evidence_capsule()` on the caller-supplied capsule and output directory;
2. invoke frozen M52 `verify_portable_evidence_with_signature()` on the exact two returned extraction paths, with the caller-supplied public key and any caller-supplied optional snapshot/lifecycle/report/trace paths.

The command does not accept caller-selected manifest or signature filenames. Those remain the frozen M43/M52 names produced by M56.

## Orchestration boundary

M57 does not reproduce capsule parsing, base64url decoding, manifest validation, signature-envelope validation, output path safety, Ed25519 verification, public-key parsing, or evidence-consistency verification.

Those responsibilities remain delegated exactly as follows:

- M56 is capsule structural/canonical validation and exact-byte extraction authority;
- M52/M47/M45/M44 remain cryptographic and portable-evidence consistency verification authority.

The new orchestration module may import and call those public functions, retain their returned metadata, and render one combined success summary. It must not import `cryptography`, instantiate Ed25519 keys, call signature primitives, parse the public key itself, or implement a second signature verifier.

## Filesystem semantics

M57 deliberately requires `--output-dir` rather than using an implicit temporary directory.

This preserves the already-qualified M56 filesystem boundary and makes side effects explicit to the operator. Successful orchestration leaves the exact extracted manifest and signature files in that directory for independent inspection or later re-verification.

M56 output rules remain unchanged:

- output directory already exists;
- fixed manifest/signature filenames only;
- no overwrite;
- no-follow/resolved-parent exclusive-create boundary;
- exact retained decoded bytes are written;
- ordinary second-file failure attempts manifest rollback and surfaces rollback failure;
- no crash-atomic/filesystem-transaction claim.

## Verification-failure semantics

Extraction necessarily occurs before frozen M52 can verify the signature because M57 intentionally refuses to create a new in-memory cryptographic verifier.

Therefore, if M56 extraction succeeds but subsequent frozen M52 verification fails—for example because the supplied public key is wrong, an optional snapshot/lifecycle/report/trace input is inconsistent, or the detached signature is cryptographically invalid—the command:

- exits nonzero through the existing argparse error surface;
- emits no successful `valid:` summary;
- does **not** delete the successfully extracted files;
- does not claim the extracted files are trusted or cryptographically verified.

Retaining those files is intentional. M56 extraction already established their structural/canonical correlation to the capsule, and deleting them on a later trust-verification failure would make the command's explicit filesystem side effect dependent on trust outcome while providing no security benefit.

Operators must treat files left after a verification failure as unverified evidence.

## Success result

A successful M57 result means both phases succeeded:

1. frozen M56 accepted the capsule and extracted exact embedded M43/M52 bytes;
2. frozen M52 accepted the extracted manifest/signature pair with the caller-supplied public key after its existing portable-evidence consistency checks.

The deterministic success summary extends the frozen M52 success summary only with an explicit `capsule=validated` marker and the fixed extracted manifest/signature paths. It does not say that the capsule itself is a new signature object or that the App Server/public key is trusted by Harness X.

## Trust / authority boundary

M57 adds no trust source.

The supplied public key remains external operator input. A successful result proves the same cryptographic claim as frozen M52: possession of the corresponding private key over the exact manifest bytes, plus the already-frozen portable-evidence consistency checks selected by the caller.

M57 does not establish:

- public-key ownership or signer identity;
- App Server/server/host identity;
- human/operator identity;
- signature/completion time;
- uncompromised key custody;
- PKI/certificate/transparency trust;
- semantic truth of evidence claims;
- historical immutability;
- filesystem crash atomicity.

Anyone holding the private key can still create indistinguishable valid M52 signatures.

## Failure ordering

M57 preserves fail-visible phase ordering:

1. capsule/file/output-boundary failures occur in M56 before cryptographic verification;
2. once extraction succeeds, frozen M52 performs its existing evidence-consistency-first verification and exact manifest-byte identity pinning before Ed25519 acceptance;
3. no M57 success summary is emitted unless the frozen M52 call succeeds.

M57 does not catch and reinterpret failure categories beyond converting existing `PortableEvidenceVerificationError` descendants through the installed CLI parser's established nonzero error surface.

## Explicit non-goals

M57 does not add:

- a second Ed25519 verifier;
- public-key discovery/publication;
- browser signature verification;
- App Server routes or UI;
- network access;
- implicit temporary extraction;
- automatic output deletion after verification failure;
- output overwrite/repair;
- ZIP/tar/session bundles;
- generic evidence discovery;
- key rotation/revocation registries;
- certificates, trusted timestamps, transparency, KMS, or hardware custody.

## Intended changed surface

M57 should remain confined to:

1. this milestone document;
2. one narrow offline orchestration module;
3. additive `verify-evidence-capsule` parser/dispatch wiring in the installed CLI wrapper;
4. focused orchestration/CLI/failure-order/source-boundary tests.

Frozen M56 extraction implementation, frozen M52 signing/verifier implementation, M43 manifest generation, M53/M55 App Server signing/capsule implementations, browser clients, App Server HTTP/UI/store/service/protocol/runtime, report/trace/snapshot/lifecycle generation, coding runtime/verifier, model/tool, memory, budget, controller, and control implementation remain outside the intended diff.

## Qualification contract

M57 cannot freeze until all of the following are true on one fixed final head:

- exact merge base is frozen M56 `7ce29e7a2d98c96c54cf5356dd337342bc1db6c1`;
- zero commits behind frozen M56;
- source/diff audit confirms no cryptographic primitive, App Server, browser, evidence-generation, or trust-source widening;
- successful end-to-end orchestration proves the extracted exact M55 pair is accepted by frozen M52 with the matching public key;
- wrong-key and inconsistent-evidence failures are nonzero and leave extracted files visibly present but unverified;
- existing M56 extraction and M52 verification commands remain independently available and unchanged;
- full pytest passes;
- `harness-x --help` passes and exposes `verify-evidence-capsule` while retaining all existing commands;
- `harness-x validate-config configs/default.yaml` passes;
- no submitted reviews or actionable review threads remain;
- PR remains draft/open/unmerged;
- exact final head, synthetic merge, compare totals, CI identifiers, and test count are recorded in PR metadata without moving the branch.
