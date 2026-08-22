# Milestone 35 — Verified Causal Trace Projection and Stream

M35 connects the local App Server introduced in M34 to the existing Harness X causal trace without creating a second reasoning/tool/verification event truth. A future GUI can now page or stream the task's real execution history while the authoritative records remain the hash-chained `TraceStore` JSONL written by the runtime.

## Architecture

```text
existing Harness X runtime
          |
          | authoritative TraceRecorder events
          v
TraceStore JSONL
          |
          | read-only verified projection
          v
Harness X App Server (M35)
          |
          +-- GET /v1/sessions/{id}/trace
          +-- GET /v1/sessions/{id}/trace/stream
          |
          v
future GUI / desktop client
```

The M34 App Server lifecycle ledger remains separate:

```text
sessions/app_<id>/events.jsonl
```

That ledger records App Server lifecycle facts such as session creation, start, cancellation, artifact availability, terminal status, and one M35 pointer event:

```text
TRACE_ATTACHED
{
  "trace_id": "trace_<uuid>",
  "trace_path": "<session-output-root>/trace_<uuid>.jsonl"
}
```

It does **not** copy causal trace events into the App Server ledger.

## Source-of-truth invariant

The M35 invariant is:

> The existing Harness X `TraceStore` is the sole causal execution ledger. The App Server may persist its identity/path and may project it read-only, but it must not manufacture a parallel reasoning, tool, verification, or coding-control history.

This keeps causal identity stable across CLI/runtime use, App Server use, replay, diagnostics, and a future GUI.

## Verification observability hardening

M35 integration exposed one pre-existing observability gap in the coding runtime: software-owned command verification executed through the normal tool boundary but did not append the already-defined `verification_completed` event to the authoritative trace.

The coding verifier now emits one bounded summary after each completed verification attempt. The event records only:

- the number of configured commands;
- the number actually executed;
- whether the attempt passed;
- command return codes.

Detailed process execution remains represented by the existing tool events, and verification authority is unchanged. The App Server does not synthesize this event.

## Durable trace attachment

The coding runtime creates a trace directly inside its session output root. M35 discovers that file by the existing `trace_<uuid>.jsonl` naming contract and persists only its pointer in the App Server snapshot/event ledger.

Attachment is constrained and idempotent:

- the trace must be directly inside the session output root;
- the filename must match the supplied trace ID;
- a session may attach only one trace;
- rediscovering the exact same trace is a no-op;
- attaching a different trace to an already attached session is rejected.

A persisted attachment survives App Server restart. If a process restart interrupts a running session, M34's truthful restart behavior still marks that session failed rather than pretending to resume instruction state, while M35 preserves the attached causal evidence for later inspection.

## Verified projection

`trace_projection.py` reads the source JSONL directly and validates every complete record before exposing it. Validation requires:

1. a valid `TraceRecord` schema;
2. the supported trace-record schema version;
3. the exact expected trace ID;
4. contiguous causal steps beginning at 1;
5. the exact previous-record hash chain;
6. the recomputed current event hash;
7. monotonic timestamps.

A projection event retains the source record identity needed for independent inspection:

- authoritative step;
- source event ID;
- source previous hash;
- source event hash;
- trace ID and task ID;
- timestamp, event type, component, and system version.

Projection fingerprints describe the projected UI object; they do not replace source trace hashes.

## Concurrent writer semantics

The runtime is the single writer, while an App Server client may read the trace concurrently.

During a non-terminal session, M35 may ignore **only** an incomplete final JSONL line that is currently being written. Every complete line before it must validate normally. A complete but invalid line is corruption even while the task is still running.

Once the App Server session is terminal, the exception disappears. The source trace must end on a complete validated JSONL record boundary. A terminal partial record is reported as corruption.

This rule avoids mistaking an ordinary append race for corruption without weakening validation of durable records.

## Bounded and credential-aware projection

Trace metadata is bounded before it crosses the HTTP boundary. M35 limits nesting depth, collection size, individual string size, reference count/length, and final projected event size.

Credential-shaped metadata keys are redacted, including common variants of:

- API keys;
- authorization values;
- passwords;
- secrets;
- access/refresh tokens;
- cookies.

The projector does not claim arbitrary secret detection. The runtime should still avoid recording credentials in causal metadata in the first place; projection redaction is defense in depth.

## Trace page API

```text
GET /v1/sessions/{session_id}/trace?after=<step>&limit=<1..1000>
Authorization: Bearer <local token>
```

The default limit is 200. `after` is an authoritative causal trace step, not an App Server event sequence.

The response includes:

- `trace_attached`;
- trace identity/path when attached;
- `after` and `limit`;
- `next_after`;
- `has_more`;
- whether a running-reader final partial line was ignored;
- bounded projected trace events.

Before the runtime creates a trace, the endpoint returns an explicit empty unattached page rather than inventing state.

If a complete source record fails integrity validation, the JSON endpoint returns HTTP 409 with:

```json
{
  "schema_version": "app-server-error-v1",
  "error": "trace_corruption",
  "detail": "..."
}
```

## Live trace SSE

```text
GET /v1/sessions/{session_id}/trace/stream?after=<step>
Authorization: Bearer <local token>
Accept: text/event-stream
```

The stream can be opened while a task is still `RUNNING`. It repeatedly discovers/reads the authoritative trace and emits newly validated records as they become complete:

```text
id: <authoritative trace step>
event: trace_event
data: <TraceProjectionEvent JSON>
```

The cursor advances only by source trace step. If more than one bounded page is already available, the stream continues paging without sleeping. If no new complete record is available and the session is still active, it polls the same source trace without creating lifecycle noise.

After the session becomes terminal and all available validated events have been emitted, the stream closes.

If corruption is discovered after SSE response headers have already been sent, HTTP status can no longer change. The stream therefore emits one terminal diagnostic event and closes:

```text
event: trace_error
data: {"schema_version":"app-trace-stream-error-v1","error":"trace_corruption",...}
```

## Lifecycle stream versus causal stream

The two SSE endpoints intentionally answer different questions.

`/events/stream` is the durable App Server lifecycle:

```text
session_created
session_started
trace_attached
artifact_available
session_completed
...
```

`/trace/stream` is the underlying Harness X execution:

```text
reasoning_requested
reasoning_completed
action_proposed
tool_permission_checked
tool_execution_finished
verification_completed
coding_phase_changed
budget_changed
error_recorded
...
```

A GUI can subscribe to both without conflating orchestration state with causal runtime state.

## Full-stack acceptance

The M35 App Server → isolated coding-runtime integration test requires a real completed session to:

- leave the operator/source checkout unchanged;
- produce the normal coding report and isolated output artifacts;
- attach exactly one authoritative trace;
- successfully validate/project that trace;
- expose real reasoning request/completion events;
- expose real tool execution;
- expose software-owned verification completion;
- expose coding-control phase changes.

Separate HTTP acceptance covers live-while-running trace SSE and explicit HTTP corruption behavior. Restart coverage verifies that a durable trace attachment remains attached exactly once after interrupted-run reconciliation.

## Public package surface

M35 exports the projection API from `harness_x.app_server`:

```python
TraceProjectionEvent
TraceProjectionPage
build_trace_projection_page
load_verified_trace_records
```

These are read/projection contracts. They do not grant trace mutation authority.

## Security and authority boundary

M35 inherits M34's literal `127.0.0.1` bind, bearer-token authentication for session data, Host-header validation, no permissive CORS, bounded requests/responses, and no arbitrary file-serving endpoint.

M35 additionally cannot:

- write model reasoning on behalf of the runtime;
- duplicate causal records into App Server lifecycle storage;
- repair or silently skip a complete corrupt trace record;
- change verification outcomes;
- alter tool permission decisions;
- mutate repository state through projection code;
- infer successful completion from trace appearance alone;
- claim that a running task was resumed after a process restart.

The coding report and existing runtime authorities still determine task outcome.

## Current limitations

- Trace discovery currently assumes exactly one `trace_<uuid>.jsonl` directly under the session output root.
- SSE uses bounded polling over standard-library HTTP rather than filesystem notifications or WebSockets.
- Only a currently incomplete final JSONL line receives concurrent-write tolerance.
- Credential-key redaction is defense in depth, not a general secret scanner.
- Trace projection exposes structured causal metadata, not private free-form chain-of-thought.
- Running-task hard cancellation remains outside M35; M34 cancellation semantics are unchanged.
- M35 provides the data boundary for a GUI but does not yet ship that GUI.

These constraints keep the UI bridge verifiable and preserve the architecture's single causal source of truth.
