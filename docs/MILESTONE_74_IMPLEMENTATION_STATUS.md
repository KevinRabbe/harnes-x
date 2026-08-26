# Milestone 74 — Implementation Candidate Status

M74 remains stacked exactly on frozen M73
`bae15b625428822210fc0660994f28a6c8cc34d8` and is still a draft, unmerged milestone.

The implementation candidate now covers the scope defined in
`MILESTONE_74_FILES_ATTACHMENTS_DIFFS_ARTIFACTS.md` through these bounded product surfaces:

- project-owned immutable attachment bytes with server-generated identity, SHA-256 metadata, size
  bounds, and normalized display filenames;
- canonical-workspace relative file references that become immutable per-execution snapshots before
  they can enter bounded untrusted context;
- authenticated execution-resource projection preserving project/chat/execution ownership;
- bounded read-only diff projection using the existing side-effect-free repository read tools, with
  no stage, commit, checkout, reset, restore, patch-apply, or arbitrary Git-command endpoint;
- execution artifacts discovered only from software-recorded artifact events and revalidated for
  ownership, root containment, byte size, and SHA-256 before download;
- everyday browser surfaces for selecting attachments, adding relative workspace references, viewing
  bounded diffs, and downloading registered artifacts through exact authenticated product routes.

The browser does not receive authority to choose workspace roots, artifact roots, absolute host paths,
commands, environment variables, credentials, verification state, evidence state, or permissions.
Diff visibility remains explicitly non-verifying and non-evidentiary. Resource references remain
context inputs only and do not grant tool permission or M72 sensitive-action approval.

M71 context bounds, M72 sensitive-action approvals, M73 immutable settings snapshots, coding/tool
side-effect checks, verifier authority, and evidence authority remain inherited and unchanged.

Formal milestone freeze still requires one exact final-head pull-request synthetic-merge qualification,
final source/diff/review audit, and recording that exact qualified head in PR #81. Any later head movement
must invalidate that freeze and require fresh qualification.
