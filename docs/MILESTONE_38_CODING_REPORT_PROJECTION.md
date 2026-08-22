# Milestone 38 — Coding Report Projection

M38 is stacked directly on frozen M37 and is intentionally read-only. It closes the operator gap where the local UI can see the durable `coding_report_path` but cannot inspect the report through the authenticated App Server.

## Scope

M38 adds one canonical report surface on the product-facing `LocalOperatorHTTPServer`:

```text
GET /v1/sessions/{session_id}/report
```

The endpoint may expose only the App Server session's canonical `coding-task-report.json` and only when that file is backed by the durable App Server lifecycle artifact event:

```text
ARTIFACT_AVAILABLE
artifact_kind = coding_task_report
```

The endpoint does not accept a path, filename, artifact key, glob, query selector, or arbitrary filesystem selector from the caller. `LocalAppHTTPServer` remains unchanged for API-only compatibility; the installed operator transport layers this one read-only product surface above it.

## Required safety / integrity contract

Before a report is projected, M38 proves:

- the session exists and is terminal;
- `coding_report_path` is present in the durable session snapshot;
- the resolved report path is exactly the canonical `coding-task-report.json` directly inside that session's resolved `output_root`;
- the App Server lifecycle ledger contains exactly one matching `ARTIFACT_AVAILABLE` event whose `artifact_kind` is exactly `coding_task_report` and whose path matches the snapshot path;
- the canonical source is not a symbolic link;
- the opened source is a regular file;
- the report is bounded to 2 MiB before JSON parsing;
- bytes decode as UTF-8;
- the root JSON value is an object;
- the returned projection includes a SHA-256 digest of the exact source bytes served.

A session with no durable report yet is availability and returns `report_not_available`. A snapshot/artifact/path/symlink/size/encoding/JSON mismatch is `report_corruption`; M38 never repairs or normalizes the source.

### Digest boundary

The pre-M38 `ARTIFACT_AVAILABLE` ledger record anchors the canonical report **path**, not a content digest. Therefore `source_sha256` fingerprints the exact current bytes returned by M38; it is not a historical cryptographic attestation of the bytes that existed when the artifact event was appended. A later modification that remains valid UTF-8 JSON cannot be proven from the existing M34 lifecycle schema alone.

M38 does not hide that limitation. The durable session status remains the App Server's task-outcome projection, verification authority remains in the coding verifier/runtime, and the UI must not reinterpret report contents as stronger authority than those existing sources.

## UI scope

The local operator UI renders the authenticated report projection in a dedicated report panel. The viewer:

- keeps its bearer copy only in page memory;
- sends it only through the `Authorization` header;
- observes selected-session and session-status changes so a report appears when a running selected session later becomes terminal;
- generation-guards asynchronous responses so stale report loads cannot render into another selected session;
- renders metadata and report JSON with `textContent` only.

Report data is never injected with `innerHTML` or interpreted as executable content.

## Authority

M38 is observability only. It cannot:

- create or modify the coding report;
- establish verification or task success from report contents;
- write App Server lifecycle events;
- read arbitrary session files;
- follow a caller-selected path;
- mutate trace, runtime, verification, memory, or revision state.

The coding runtime remains the report producer and task-outcome authority. The operator transport only projects the already-recorded durable artifact read-only.

## Non-goals

M38 does not add generic artifact browsing/download, workspace file serving, screenshots, patch download, desktop-shell filesystem access, hard cancellation, cryptographic historical report attestation, or new model/tool/verification authority.

## Deterministic acceptance

M38 acceptance covers:

- successful exact projection with source byte count and SHA-256;
- unavailability before a terminal durable report exists;
- mandatory durable artifact-event evidence;
- snapshot path substitution rejection;
- symbolic-link substitution rejection;
- invalid UTF-8, malformed JSON, and non-object JSON rejection;
- explicit size-bound rejection;
- unauthenticated HTTP rejection;
- authenticated report retrieval;
- HTTP 409 after structural source corruption;
- rejection of extra report path segments and all report query parameters;
- safe report-viewer DOM strategy and no browser credential persistence;
- exact static-asset allowlisting;
- packaged report-viewer JavaScript syntax when Node is available.

The normal repository suite retains all M34–M37 session, trace, UI-authentication, and resilient-stream contracts.

## Freeze gates

Before M38 is frozen:

- authenticated report projection endpoint exists;
- unauthenticated access remains rejected;
- report path is canonical and output-root confined;
- durable artifact-event path evidence is mandatory;
- symlink/path substitution and malformed/missing report cases are fail-visible;
- size/UTF-8/JSON-object bounds are deterministic;
- current-source SHA-256 is exposed without overstating it as historical attestation;
- UI renders report data without executable HTML and refreshes on terminal status changes;
- M37 reconnect/auth behavior remains intact;
- documentation is complete;
- exact M37→M38 diff is confined to intended App Server projection/UI/tests/docs;
- exact-head Linux CI passes.
