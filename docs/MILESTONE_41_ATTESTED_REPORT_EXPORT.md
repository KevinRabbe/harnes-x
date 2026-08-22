# Milestone 41 — Attested Coding Report Export

M41 is stacked directly on frozen M40 and closes one narrow operator gap left by M38/M39: the authenticated operator can inspect a canonical coding report through a JSON projection, but cannot export the exact validated source bytes that the App Server is projecting.

M41 adds export for exactly one artifact: the canonical per-session `coding-task-report.json`. It does not create a generic artifact browser, caller-selected path endpoint, workspace file server, archive API, or new task/runtime authority.

## Scope

M41 adds one authenticated read-only endpoint:

```text
GET /v1/sessions/{session_id}/report/export
```

The endpoint accepts no query parameters and no caller-supplied path. It derives the canonical report only from the authoritative session snapshot and durable `ARTIFACT_AVAILABLE(coding_task_report)` event.

The response body is the exact bounded source byte sequence validated during that request. M41 must not parse a report, reopen the path, and then serve a second filesystem read: provenance checks and response bytes must refer to the same captured byte sequence.

## Validation and provenance

M41 reuses the M38/M39 report boundary:

- terminal session required;
- canonical absolute `coding-task-report.json` directly under the session output root;
- no symbolic link substitution;
- exactly one durable `ARTIFACT_AVAILABLE(coding_task_report)` event with the exact canonical path;
- bounded regular-file source read using the existing 2 MiB ceiling;
- valid UTF-8;
- valid JSON object root;
- complete M39 attestation metadata when any attestation key is present;
- byte-count and SHA-256 equality against a captured durable attestation before a `verified` report can be exported.

A verified report whose current bytes differ from the ledger attestation fails as `report_corruption` before any export body is sent, including same-length modifications that remain valid JSON.

M38-era `legacy_unattested` reports and explicit M39 `unavailable` reports remain exportable after current-source validation. They are never promoted to `verified`; their provenance state is exposed on the response so the operator can distinguish current-source export from ledger-attested export.

## Response contract

A successful response uses:

```text
Content-Type: application/json; charset=utf-8
Content-Disposition: attachment; filename="coding-task-report.json"
Cache-Control: no-store
X-Content-Type-Options: nosniff
X-Harness-X-Report-SHA256: <current exact lowercase SHA-256>
X-Harness-X-Report-Attestation: verified | legacy_unattested | unavailable
X-Harness-X-Artifact-Event-Hash: <durable lifecycle event hash>
```

`Content-Length` is the exact validated source byte count.

The custom headers are provenance metadata only. They do not establish task success, semantic correctness, verification success, or remote authenticity.

## Operator UI

M41 adds a `Download exact report` action to the existing coding-report panel.

Because stateful App Server routes remain bearer-authenticated, the browser must not use a plain unauthenticated anchor to the export route. A packaged dependency-free export client:

- captures the bearer through the same already-qualified manual-auth form listener ordering used by the report viewer;
- performs an authenticated same-origin `fetch()` with `cache: "no-store"`;
- requires the expected content type, exact content length, SHA-256 provenance header, and recognized attestation status;
- downloads the returned response bytes through a temporary same-page object URL;
- uses the fixed filename `coding-task-report.json` rather than trusting server/user-provided arbitrary filenames;
- revokes the temporary object URL immediately after triggering the download;
- clears in-memory bearer state on the existing lock action;
- renders errors only through `textContent`.

The export client must not use cookies, `localStorage`, `sessionStorage`, `innerHTML`, caller-selected paths, or a persistent blob/object URL.

M40 bootstrap ordering remains valid: report/export listeners are registered before `app.js` synchronously clears the password field, and `bootstrap.js` continues to run only after those listeners exist.

## Authority boundary

M41 exports already-existing validated report bytes only. It cannot:

- write, repair, replace, rename, or attest report bytes;
- choose an arbitrary filesystem path;
- enumerate workspace/session files;
- export arbitrary lifecycle artifacts;
- establish task or verification success;
- execute tools or models;
- bypass bearer authentication, budgets, permissions, or session transitions;
- mutate causal trace, memory, revision, model, controller, or lifecycle state;
- hard-cancel running work.

M39 remains the historical byte-provenance authority for captured report identity. Existing coding runtime/verifier and App Server lifecycle logic remain task/completion authorities.

## Non-goals / limitations

M41 does not add generic artifact download/browsing, ZIP export, workspace file access, directory listing, filesystem picker integration, desktop-shell packaging, signatures/public-key authenticity, remote trust, or report-byte immutability.

The browser download target is controlled by normal browser download behavior; M41 does not gain filesystem write authority beyond handing a response to the user's browser.

## Deterministic acceptance

Before freeze, M41 must prove:

- export returns the exact source bytes observed by the validation read;
- `Content-Length` and SHA-256 header exactly describe those returned bytes;
- verified M39 reports export only when current bytes match the durable attestation;
- same-length valid-JSON tampering fails before response body;
- legacy and unavailable reports remain explicitly labeled and are not promoted to verified;
- nonterminal/no-report sessions fail without file access;
- bearer authentication remains mandatory;
- query parameters, extra path segments, and arbitrary path selection are rejected;
- response filename is fixed and cannot be caller-controlled;
- existing `/report` JSON projection semantics remain unchanged;
- browser export uses authenticated fetch, a temporary revoked object URL, safe DOM rendering, and no browser credential persistence;
- M40 bootstrap/manual-auth ordering still supplies the export client with page-memory bearer state;
- packaged JavaScript syntax passes under Node when available;
- exact M40→M41 diff remains confined to canonical report validation/export, operator UI export, tests, and documentation;
- exact-head Linux CI passes.
