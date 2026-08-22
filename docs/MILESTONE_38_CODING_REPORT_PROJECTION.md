# Milestone 38 — Coding Report Projection

M38 is stacked directly on frozen M37 and is intentionally read-only. It closes the operator gap where the local UI can see the durable `coding_report_path` but cannot inspect the report through the authenticated App Server.

## Scope

M38 adds one canonical report surface:

```text
GET /v1/sessions/{session_id}/report
```

The endpoint may expose only the App Server session's canonical `coding-task-report.json` and only when that file is backed by the durable App Server lifecycle artifact event:

```text
ARTIFACT_AVAILABLE
artifact_kind = coding_task_report
```

The endpoint does not accept a path, filename, artifact key, glob, or arbitrary filesystem selector from the caller.

## Required safety / integrity contract

Before a report is projected, M38 must prove:

- the session exists and is terminal;
- `coding_report_path` is present in the durable session snapshot;
- the resolved report path is exactly the canonical `coding-task-report.json` directly inside that session's resolved `output_root`;
- the App Server lifecycle ledger contains a matching `ARTIFACT_AVAILABLE` event whose `artifact_kind` is exactly `coding_task_report` and whose resolved path matches the snapshot path;
- the file is a regular file and remains inside the session output root after resolution;
- the report is bounded in size before JSON parsing;
- bytes decode as UTF-8;
- the root JSON value is an object;
- the returned projection includes a SHA-256 digest of the exact source bytes.

A missing report is availability, not corruption. A snapshot/artifact/path/size/encoding/JSON mismatch is treated as report corruption and must not be silently repaired or normalized.

## UI scope

The local operator UI may render the authenticated report projection using the existing safe DOM/text rendering strategy. Report data must not be injected with `innerHTML` or interpreted as executable content.

## Authority

M38 is observability only. It cannot:

- create or modify the coding report;
- establish verification or task success from report contents;
- write App Server lifecycle events;
- read arbitrary session files;
- follow a caller-selected path;
- mutate trace, runtime, verification, memory, or revision state.

The coding runtime remains the report producer and task-outcome authority. The App Server only projects the already-recorded durable artifact read-only.

## Non-goals

M38 does not add generic artifact browsing/download, workspace file serving, screenshots, patch download, desktop-shell filesystem access, hard cancellation, or new model/tool/verification authority.

## Freeze gates

Before M38 is frozen:

- authenticated report projection endpoint exists;
- unauthenticated access remains rejected;
- report path is canonical and output-root confined;
- durable artifact-event evidence is mandatory;
- symlink/path substitution and missing/tampered report cases are fail-visible;
- size/UTF-8/JSON-object bounds are deterministic;
- UI renders report data without executable HTML;
- M37 reconnect/auth behavior remains intact;
- documentation is complete;
- exact M37→M38 diff is confined to intended App Server projection/UI/tests/docs;
- exact-head Linux CI passes.
