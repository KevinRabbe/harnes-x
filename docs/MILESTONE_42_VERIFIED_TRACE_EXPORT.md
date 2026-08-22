# Milestone 42 — Verified Causal Trace Export

M42 is stacked directly on frozen M41 and closes one narrow operator gap left by M35: the App Server can verify, page, and stream the authoritative causal trace, but the operator cannot export that exact source JSONL for offline inspection.

M42 adds export for exactly one source: the canonical trace already attached to one terminal App Server session. It does not create a generic artifact browser, caller-selected path endpoint, workspace file server, archive API, or new runtime authority.

## Scope

M42 adds one authenticated read-only endpoint:

```text
GET /v1/sessions/{session_id}/trace/export
```

The endpoint accepts no query parameters and no caller-supplied path. It derives trace identity/path only from the authoritative session snapshot plus exactly one durable `TRACE_ATTACHED` lifecycle event.

Export is terminal-only. A running/cancel-requested session must use the already-qualified M35 page/SSE interfaces rather than receiving a raw file snapshot that could race the single trace writer.

## Same-read verification/export invariant

The successful response body must be the exact byte sequence verified during that request.

M42 must not:

1. verify the trace through one filesystem read;
2. reopen the path;
3. export a potentially different second read.

The trace verifier therefore exposes payload-level verification, while the M42 export boundary retains one immutable source result containing both the exact source payload and parsed `TraceRecord` sequence. The HTTP handler writes that retained payload directly.

## Canonical trace boundary

Before reading bytes, M42 requires:

- terminal App Server session;
- `trace_id` and `trace_path` both present;
- valid canonical `trace_<uuid>.jsonl` filename;
- trace directly under the session output root;
- snapshot path exactly equal the canonical attached trace path;
- exactly one durable `TRACE_ATTACHED` event for the session;
- durable event trace ID/path exactly equal the snapshot identity/path;
- the persisted absolute trace path resolves strictly to itself, so neither the leaf file nor any resolved parent component may be replaced by a symbolic link;
- final-component `O_NOFOLLOW` protection when supported by the platform;
- source is a regular file according to `fstat` after open;
- hard raw-export ceiling of 32 MiB (`32 * 1024 * 1024` bytes), checked both against file metadata and the bounded read.

The endpoint never accepts an artifact name, directory, filename, path, glob, query selector, or arbitrary trace ID from the caller.

The resolved-path equality check is intentionally stronger than checking only `trace_path.is_symlink()`: replacing the persisted session output directory with a symlink to another directory must also fail rather than allowing a self-consistent substitute trace to be exported under the original lexical path.

## Trace integrity

M42 reuses M35 trace-record verification against the same captured source bytes:

- complete terminal JSONL record boundary;
- valid `TraceRecord` schema;
- supported record schema version;
- exact expected trace ID for every record;
- contiguous causal steps beginning at 1;
- exact `previous_hash` chain;
- recomputed current event hash equality;
- monotonic record timestamps.

A malformed, partial, substituted, over-limit, or hash-chain-invalid source fails as `trace_corruption` before a successful export body.

No complete corrupt record may be skipped or repaired.

M42 verifies current source self-consistency and its attachment identity. It does not introduce a separately persisted historical final-trace digest or public-key signature. Consequently, its response headers are not proof that an attacker with sufficient local write authority could never replace the entire trace plus its current self-consistent contents; signatures, remote trust, and immutable final-trace attestation remain outside this milestone.

## Response contract

A successful export uses:

```text
Content-Type: application/x-ndjson; charset=utf-8
Content-Disposition: attachment; filename="causal-trace.jsonl"
Content-Length: <exact verified source bytes>
Cache-Control: no-store
X-Content-Type-Options: nosniff
X-Harness-X-Trace-ID: <attached trace id>
X-Harness-X-Trace-SHA256: <exact current source SHA-256>
X-Harness-X-Trace-Records: <verified record count>
X-Harness-X-Trace-Final-Event-Hash: <last source event hash or empty-trace sentinel>
X-Harness-X-Trace-Attachment-Event-Hash: <durable lifecycle event hash>
```

These headers describe current verified source identity. They do not create a signature, remote trust root, task-success verdict, verification verdict, historical immutable trace attestation, or new authority.

## Operator UI

M42 adds a `Download verified trace` action to the existing causal-trace panel.

The packaged dependency-free client:

- captures the existing page-memory bearer through qualified auth-form listener ordering;
- enables raw export only for terminal selected-session states (`succeeded`, `failed`, or `cancelled`);
- uses authenticated same-origin `fetch()` with `cache: "no-store"` and `credentials: "omit"`;
- requires the expected content type and fixed attachment filename;
- validates trace ID, content length, record-count header, final event hash, lifecycle attachment event hash, and source SHA-256 shape;
- reads one `ArrayBuffer` and recomputes SHA-256 with Web Crypto before download;
- refuses download when byte count or digest differs from the response provenance headers;
- uses a temporary same-page object URL and fixed filename `causal-trace.jsonl`;
- revokes the object URL immediately;
- aborts in-flight export on selection change, lock, or unload;
- generation-guards status/download completion so session A cannot present under session B;
- renders status/errors only with `textContent`;
- stores no bearer or trace bytes in cookies, `localStorage`, or `sessionStorage`.

The raw trace may contain structured metadata that the M35 UI projection would redact or truncate. Export is therefore an explicit authenticated evidence action, not a replacement for the bounded/redacted live UI projection.

## M35 compatibility

M42 extracts the existing record-validation algorithm into `verify_trace_payload()` but leaves `load_verified_trace_records()` as the M35 filesystem-reading wrapper. M35 page and SSE callers therefore retain their existing behavior, including the running-reader rule that only an incomplete final JSONL line may be ignored while the session is nonterminal.

The M42 32 MiB source ceiling, no-symlink path-resolution checks, and terminal-only rule apply only to raw export. They do not alter M35 live projection/stream semantics.

## Authority boundary

M42 exports already-existing verified trace bytes only. It cannot:

- write, append, repair, redact, truncate, synthesize, or reorder trace records;
- choose or enumerate arbitrary filesystem paths;
- export arbitrary lifecycle artifacts;
- expose a running raw-trace snapshot;
- establish task or verification success;
- execute tools or models;
- bypass bearer authentication, budgets, permissions, or session transitions;
- mutate lifecycle, memory, revision, model, controller, or causal state;
- hard-cancel running work.

The existing `TraceStore` remains the sole causal execution ledger. Existing coding runtime/verifier and App Server lifecycle logic remain task/completion authorities.

## Non-goals / limitations

M42 does not add generic artifact download/browsing, ZIP/session export, workspace file access, directory listing, filesystem picker integration, desktop-shell packaging, signatures/public-key authenticity, remote trust, trace immutability, a persisted final-trace content attestation, or running raw-trace snapshots.

M35 live page/SSE projection continues to be the appropriate interface while a trace is being written.

## Deterministic acceptance

Before freeze, M42 must prove:

- terminal-only export;
- exact same-read verified bytes are returned without a second filesystem reopen;
- exact content length and source SHA-256 headers;
- complete trace hash-chain verification against the returned bytes;
- durable `TRACE_ATTACHED` event identity/path agreement;
- leaf symbolic-link substitution rejection;
- intermediate/parent-directory symbolic-link path substitution rejection;
- terminal partial-line rejection;
- invalid record/hash/step/timestamp rejection through the existing verifier;
- explicit 32 MiB export size bound;
- mandatory bearer authentication;
- query parameters and extra path segments rejected;
- fixed filename with no caller-controlled file selection;
- existing M35 page/SSE semantics unchanged;
- browser authenticated/cookie-independent fetch and local SHA-256 recomputation;
- temporary revoked object URL and page-memory-only bearer;
- selection/lock/unload cancellation plus stale-generation suppression;
- exact static asset allowlisting and Node JavaScript syntax qualification;
- exact M41→M42 diff remains confined to trace validation/export, operator UI export, tests, and this document;
- exact-head Linux CI passes.
