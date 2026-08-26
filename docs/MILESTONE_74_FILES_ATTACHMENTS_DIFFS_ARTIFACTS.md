# Milestone 74 — Files, Attachments, Diffs + Artifacts

## Status

Implementation milestone stacked exactly on frozen M73
`bae15b625428822210fc0660994f28a6c8cc34d8`.

This document is the first M74 commit and defines the milestone boundary before implementation.

## Objective

Make ordinary project work practical by giving the product layer explicit, bounded ways to reference
workspace files, attach immutable user-provided bytes, inspect execution diffs, and retrieve generated
artifacts without turning the browser into a general filesystem, shell, evidence, or permission
authority.

M74 should make file-shaped context and outputs visible to the user while preserving the existing
M66–M73 project/chat/execution identities, M71 context bounds, M72 sensitive-action approvals, M73
settings snapshots, coding/tool permissions, verifier authority, and evidence boundaries.

## Required behavior

M74 should:

1. define versioned project-scoped file/attachment/artifact metadata contracts with immutable identity,
   size, digest, provenance, and ownership;
2. support bounded upload of user-selected attachment bytes into software-owned project storage,
   never persisting browser-supplied absolute paths as authority;
3. support explicit read-only references to regular files under the canonical project workspace by
   normalized relative path, rejecting traversal, symlink escape, devices, directories, and files
   outside configured bounds;
4. snapshot any file content used by an accepted conversation execution so later workspace edits do
   not retroactively change that execution's effective context;
5. include only eligible bounded textual file/attachment snapshots in model context through an
   explicit provenance-visible M71-style context boundary; opaque/binary attachments remain metadata
   unless a separately declared parser exists;
6. expose bounded read-only diff projection for an execution from software-owned workspace state,
   with path and byte/line limits and no implicit staging, committing, reverting, or patch applying;
7. expose generated-artifact metadata and authenticated byte retrieval only for outputs explicitly
   registered by the execution/product layer and contained in software-owned artifact roots;
8. preserve project ownership, archived-project behavior, restart durability, canonical workspace
   identity, and append-only/fingerprint conflict checks where immutable snapshots are involved;
9. add everyday UI surfaces for selecting attachments, referencing workspace files, viewing bounded
   diffs, and downloading registered artifacts using inherited authenticated product APIs and safe DOM
   operations; and
10. fail closed on malformed names, unsupported paths, oversized payloads, digest mismatches,
    ownership mismatches, missing snapshots, corrupt metadata, and storage escape attempts.

## Initial storage and projection boundary

The first implementation should remain deliberately small and local-first.

### User attachments

- project-scoped immutable blobs plus versioned metadata;
- server-generated attachment identity;
- original filename retained as display metadata only after normalization/bounding;
- SHA-256 digest and exact byte size recorded before the attachment becomes referenceable;
- strict per-blob and per-request limits;
- no executable launch, archive extraction, macro execution, MIME sniffing as authority, or native
  open/reveal behavior.

### Workspace file references

- relative paths only, rooted at the canonical project workspace;
- resolution must prove the final regular file remains inside the canonical workspace;
- snapshots are immutable content records tied to project/execution identity;
- file references are read/context inputs only and never grants to modify that path;
- text decoding is explicit and bounded; binary files are not silently coerced into prompt text.

### Diffs

- read-only projection of the current execution/workspace change surface;
- bounded filenames, status metadata, summary counts, and textual patch fragments where safe;
- no stage/unstage, commit, checkout, reset, restore, apply-patch, or arbitrary Git command surface;
- diff visibility is not verification success and is not evidence attestation.

### Generated artifacts

- only software-registered outputs under a project/execution-owned artifact root are visible;
- metadata carries project ID, optional chat/execution IDs, logical name, relative storage name,
  media type, byte size, digest, and creation provenance;
- authenticated download validates ownership, containment, size, and digest before serving bytes;
- artifact registration does not make an output verified, trusted, signed, or promoted evidence.

## Authority and safety invariants

The following remain authoritative:

- canonical project workspace ownership and lifecycle from the product store;
- execution acceptance/frozen identity from the M69–M73 conversation stack;
- M71 context construction and hard character/byte limits;
- M72 sensitive-action approval for actions that cross its approval boundary;
- M73 immutable project-settings snapshot for each accepted execution;
- coding/tool permission and side-effect checks for filesystem mutation;
- verifier outputs for verification success;
- evidence manifests, signatures, receipts, and capsules for evidence authority.

M74 must not:

- expose arbitrary host filesystem browsing or absolute-path selection to the web product layer;
- accept browser-provided workspace roots, artifact roots, executable paths, shell commands, Git
  commands, environment variables, bearer credentials, or signing keys;
- let an attachment or referenced file grant tool permission or sensitive-action approval;
- read through symlinks or path traversal outside the canonical project workspace or artifact roots;
- silently inject opaque binary bytes, archives, images, PDFs, executables, or unknown encodings into
  model text context;
- allow diff viewing to mutate the repository or establish verification/evidence truth;
- allow artifact download paths to be chosen directly by the browser;
- retroactively mutate accepted plans, frozen M71 contexts, M72 approvals, M73 settings snapshots,
  traces, reports, evidence, memory, or improvement records;
- add native open/reveal, drag-and-drop shell integration, cloud storage, remote sync, repository
  history browsing, full Git client behavior, or installer/runtime distribution.

## Qualification plan

Before freeze, M74 must demonstrate on one exact head:

- attachment upload is authenticated, project-scoped, bounded, durable, digest-verified, and immune to
  traversal/filename tricks;
- workspace references reject absolute paths, traversal, symlink escape, directories, devices,
  oversized files, and ownership mismatch;
- accepted executions freeze referenced textual content and remain unchanged after later workspace or
  attachment-store edits;
- M71 context limits still hold when project instructions plus referenced text are present;
- binary/unsupported attachments remain non-text metadata and cannot enter prompt text accidentally;
- diff projection is bounded, read-only, project/execution-scoped, and cannot invoke mutation paths;
- registered artifact download rejects guessed paths, ownership mismatch, traversal, corruption, and
  digest/size mismatch;
- archived/wrong-project operations fail closed;
- everyday UI exposes only the bounded product APIs and does not retain bearer tokens, absolute
  filesystem paths, arbitrary commands, or unrestricted download paths;
- full inherited pytest, `harness-x --help`, and default config validation pass on Ubuntu and Windows;
- inherited Windows desktop restore/build/smoke/publish/artifact qualification passes; and
- final source/diff/review audit records the exact qualified M74 head in the PR body only.

## Non-goals

M74 does not implement repository commit/history management, patch application, conflict resolution,
native shell/open/reveal actions, rich document/PDF/image extraction, OCR, archive extraction, cloud
file providers, multi-user sharing, installer/runtime distribution, reliability/reconnect recovery,
or any new verification, evidence, memory, approval, model-routing, or self-improvement authority.

M75 remains the planned everyday reliability checkpoint.
