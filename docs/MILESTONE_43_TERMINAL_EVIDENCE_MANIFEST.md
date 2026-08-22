# Milestone 43 — Terminal Evidence Manifest

M43 is stacked directly on frozen M42 and closes one narrow operator-evidence gap left by M41/M42: the operator can export the canonical coding report and the authoritative causal trace separately, but there is no single terminal-session object that correlates those byte identities with the durable App Server lifecycle head.

M43 adds one authenticated generated manifest. It does not create a ZIP/session archive, generic artifact browser, caller-selected path endpoint, workspace file server, signing authority, or new runtime/task authority.

## Scope

M43 adds one authenticated terminal-only endpoint:

```text
GET /v1/sessions/{session_id}/evidence/manifest
```

The endpoint accepts no query parameters and no caller-selected artifact, filename, path, directory, glob, or trace ID.

The manifest is generated from the authoritative session snapshot plus the validated App Server lifecycle ledger. When a canonical coding report or attached causal trace exists, M43 reuses the existing M39/M41 and M42 validation boundaries to describe that exact current source identity.

## Terminal-only contract

A manifest is available only after the App Server session is terminal (`succeeded`, `failed`, or `cancelled`).

M43 does not synthesize a point-in-time evidence package for a running session. Running lifecycle and causal state continue to use the already-qualified M35/M37 live streams.

## Lifecycle evidence

Before producing a manifest, M43 independently checks the supplied lifecycle event sequence:

- at least one event must exist;
- every event belongs to the requested session;
- sequence numbers are contiguous from 1;
- every `previous_hash` exactly matches the preceding event hash;
- every event recomputes to its recorded `event_hash`;
- event count exactly equals `snapshot.event_count`;
- the final event hash exactly equals `snapshot.latest_event_hash`.

The lifecycle section records:

- terminal session status;
- snapshot fingerprint;
- event count;
- current lifecycle ledger head hash;
- created/completed timestamps.

The manifest does not replace the App Server ledger. It records the verified current ledger head needed to correlate exported evidence with that session projection.

## Coding-report evidence

If no canonical coding report exists, the report component is explicitly `not_available`.

If a report exists, M43 reuses the M39/M41 `read_validated_coding_report()` boundary, including:

- canonical report path rules;
- bounded regular-file read;
- UTF-8 and JSON-object validation;
- exact durable `ARTIFACT_AVAILABLE(coding_task_report)` identity;
- current byte count and SHA-256;
- M39 attestation status (`verified`, `legacy_unattested`, or `unavailable`);
- durable artifact-event sequence/hash;
- captured attested byte identity when available.

A present-but-invalid report is manifest corruption, not `not_available`.

## Causal-trace evidence

If no trace is attached, the trace component is explicitly `not_available`.

If a trace exists, M43 reuses the M42 `read_validated_trace_export()` boundary, including:

- terminal-only canonical trace identity;
- exact snapshot + durable `TRACE_ATTACHED` agreement;
- leaf and intermediate/parent symlink substitution rejection;
- 32 MiB regular-file source ceiling;
- complete terminal JSONL boundary;
- trace ID, contiguous step, previous-hash, event-hash, and timestamp verification;
- current byte count and SHA-256;
- verified record count;
- final source event hash;
- durable attachment-event sequence/hash.

A present-but-invalid trace is manifest corruption, not `not_available`.

## Manifest identity

The generated model uses schema version:

```text
app-terminal-evidence-manifest-v1
```

It contains a deterministic SHA-256 `fingerprint` computed over the canonical JSON form of all manifest fields except the fingerprint itself.

The HTTP response is serialized once and the exact response byte sequence is additionally described by:

```text
X-Harness-X-Evidence-Manifest-SHA256: <sha256 of exact response body>
```

The internal fingerprint and response-byte digest are integrity identifiers, not signatures or remote authenticity proofs.

## Response contract

A successful response uses:

```text
Content-Type: application/json; charset=utf-8
Content-Disposition: attachment; filename="session-evidence-manifest.json"
Content-Length: <exact generated response bytes>
Cache-Control: no-store
X-Content-Type-Options: nosniff
X-Harness-X-Evidence-Manifest-SHA256: <exact response SHA-256>
```

No source file is reopened to generate the response. Report and trace component identities come from the exact validated source results returned by their existing validators.

## Operator UI

M43 adds a `Download evidence manifest` action for terminal selected sessions.

The dependency-free browser client:

- captures the existing page-memory bearer through the already-qualified listener ordering;
- enables the action only for terminal session states;
- performs authenticated same-origin `fetch()` with `cache: "no-store"` and `credentials: "omit"`;
- requires the expected content type and fixed attachment filename;
- reads one `ArrayBuffer`;
- recomputes SHA-256 with Web Crypto and requires equality with the response digest header;
- parses the exact downloaded bytes as UTF-8 JSON and requires `app-terminal-evidence-manifest-v1` plus the selected session ID;
- uses only a temporary same-page object URL and fixed filename `session-evidence-manifest.json`;
- revokes the object URL immediately;
- aborts in-flight download on session selection change, lock, or unload;
- generation-guards completion/status so session A cannot present under session B;
- renders status/errors only with `textContent`;
- does not store bearer or manifest bytes in cookies, `localStorage`, or `sessionStorage`.

## Authority boundary

M43 is correlation metadata only. It cannot:

- write, append, repair, replace, attest, or mutate report/trace/lifecycle source records;
- select or enumerate arbitrary filesystem paths or artifacts;
- create a ZIP/session archive;
- establish task success, verification success, or semantic correctness;
- execute tools or models;
- bypass authentication, permissions, budgets, or lifecycle transitions;
- mutate memory, revisions, controller state, model state, or causal state;
- hard-cancel running work.

The App Server lifecycle ledger remains authoritative for session lifecycle. `TraceStore` remains the sole causal execution ledger. M39 remains the historical report-byte attestation authority for captured reports. Existing coding runtime/verifier remain task/completion authorities.

## Provenance limitation

M43 correlates currently validated evidence. It does not upgrade M42 trace identity into immutable historical attestation and does not upgrade M38-era or M39-unavailable report evidence into verified historical content.

A manifest fingerprint is not a signature, public-key attestation, timestamp authority, remote trust root, or proof against an actor capable of rewriting all relevant local evidence and regenerating the manifest.

## Non-goals / limitations

M43 does not add ZIP/session bundle export, generic artifact browsing, workspace file access, directory listing, filesystem picker integration, desktop-shell packaging, signatures/public-key authenticity, remote trust, immutable trace attestation, or running evidence snapshots.

## Deterministic acceptance

Before freeze, M43 must prove:

- exact frozen M42 base;
- terminal-only manifest availability;
- lifecycle event sequence/hash-chain/head agreement with the snapshot;
- explicit report `not_available` versus validated available report;
- explicit trace `not_available` versus validated available trace;
- present-but-corrupt report or trace fails closed rather than being reclassified unavailable;
- available report fields exactly match the existing validated report result;
- available trace fields exactly match the existing validated trace result;
- deterministic manifest self-fingerprint;
- exact response-body SHA-256 header and fixed filename;
- mandatory bearer authentication;
- query parameters and extra path segments rejected;
- no caller-selected artifact/path surface;
- browser terminal gating, authenticated cookie-independent fetch, local response SHA-256 verification, fixed filename, temporary object URL revocation, and page-memory-only bearer;
- session/lock/unload cancellation plus stale-generation suppression;
- existing M35/M37/M40/M41/M42 routes and clients remain compatible;
- exact static-asset allowlisting and Node syntax qualification;
- exact M42→M43 diff remains confined to manifest projection/transport/UI, tests, and this document;
- exact-head Linux CI passes.
