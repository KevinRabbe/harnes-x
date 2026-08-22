# Milestone 34 — Local Single-User App Server Foundation

M34 introduces the local Harness X App Server boundary needed by a future GUI or desktop client. It is intentionally designed for one operator on one machine. It does not add accounts, tenants, cloud synchronization, a model router, or a second source of runtime authority.

## Architecture

```text
future GUI / desktop client
          |
          | authenticated loopback HTTP + SSE
          v
Harness X App Server (M34)
          |
          | typed CodingSessionRequest
          v
existing M30/M32 coding runtime
          |
          +-- repository tools / permissions
          +-- M25 verification authority
          +-- M26 browser verification
          +-- M27 long-horizon task state
          +-- M28 project memory
          +-- M29 procedure reliability
          +-- M30 procedure revision
```

The App Server schedules and projects work. It does not decide whether model output is correct and it does not bypass any existing tool, verification, memory, or promotion boundary.

## Personal-use scope

M34 deliberately assumes:

- one user;
- one local Harness X installation;
- one local heavyweight model server/GPU workload at a time by default;
- local filesystem workspaces and artifacts;
- loopback HTTP only.

The service therefore has one coding worker. Additional sessions may be created while a run is active, but they remain `created` until the worker is available.

## Session protocol

`CodingSessionRequest` is versioned as `app-coding-session-request-v1` and requires:

- an explicit workspace path;
- task text;
- an explicit M32 model profile;
- at least one verification command or an M25 verification-plan path.

It may also specify project-memory location/identity, normal coding budgets, baseline-verification policy, and the paired M26 application/browser plan inputs.

Paths are normalized to absolute paths in the durable request. Live-path existence is checked only when a new session is scheduled. Historical snapshots therefore remain readable after the original repository or verification file is later moved or deleted.

## Durable session state

Each session has a directory under the configured app-server data root:

```text
sessions/app_<uuid>/
  snapshot.json
  events.jsonl
```

`events.jsonl` is append-only and hash chained. Each record contains:

- session ID;
- monotonically contiguous sequence number;
- typed event kind;
- UTC timestamp;
- bounded event payload;
- previous event hash;
- current event hash.

The event record is fsynced before the snapshot projection is atomically replaced. On restart, a snapshot that trails a valid event ledger is replayed forward. A snapshot ahead of the ledger, a broken sequence/hash chain, cross-session event, or altered snapshot fingerprint is rejected.

The snapshot is therefore a convenient durable projection, not a substitute causal authority.

## Lifecycle

The current states are:

```text
CREATED
  -> RUNNING
  -> SUCCEEDED | FAILED

CREATED | RUNNING
  -> CANCEL_REQUESTED

CANCEL_REQUESTED
  -> CANCELLED | SUCCEEDED | FAILED
```

Queued work can be cancelled before execution and becomes `CANCELLED` immediately.

The existing coding runtime does not yet expose a safe cancellation hook inside a running reasoning/tool loop. M34 therefore does not falsely claim preemption. A running cancel request is durably recorded. If the underlying run subsequently succeeds, the session may still finish `SUCCEEDED`; if it returns unsuccessfully after the request, M34 records `CANCELLED` with the runtime evidence retained.

A process restart cannot resume an in-memory M34 worker. Sessions found `RUNNING` or `CANCEL_REQUESTED` on App Server startup are explicitly failed with `app_server_restart_interrupted_running_session`. A session still in `CREATED` may be requeued if its launch inputs still exist.

## Existing coding runtime integration

`HarnessCodingRunner` converts the typed request to the same argument/configuration surface used by `harness-x-code`, then directly constructs the existing isolated M30 runtime in-process.

It does not shell out to the CLI and it does not implement its own verifier. The normal final artifact remains:

```text
coding-task-report.json
```

When that artifact appears, M34 emits an `artifact_available` event and records its path in the terminal session snapshot.

Model selection is still explicit. M34 does not automatically switch between `main`, `coder`, `reasoning`, or `api` profiles.

## HTTP transport

Run:

```powershell
harness-x-app-server --root D:\harness-x-app --port 8765
```

M34 binds only to literal:

```text
127.0.0.1
```

It does not expose a LAN/public bind option.

At startup the command prints a small JSON record containing the base URL and local token-file path. The token value itself is not printed in normal API responses.

The transport persists:

```text
<root>/access-token
<root>/server-info.json
<root>/data/sessions/...
<root>/data/runs/...
```

The token and server-info files are written with owner-only mode where the platform supports POSIX permissions. On Windows the server still relies on loopback binding and the user's normal filesystem ACLs.

## HTTP endpoints

### Health

```text
GET /v1/health
```

Returns a minimal health object. It is the only endpoint that does not require the bearer token.

### Session list

```text
GET /v1/sessions
Authorization: Bearer <local token>
```

### Session snapshot

```text
GET /v1/sessions/{session_id}
Authorization: Bearer <local token>
```

### Event page

```text
GET /v1/sessions/{session_id}/events?after=<sequence>
Authorization: Bearer <local token>
```

### Event stream

```text
GET /v1/sessions/{session_id}/events/stream?after=<sequence>
Authorization: Bearer <local token>
Accept: text/event-stream
```

The stream emits standard SSE records with `id`, `event`, and JSON `data`. It closes after the session is terminal and all known events have been emitted.

M34 SSE currently represents the durable App Server lifecycle. It does **not** duplicate every reasoning/tool/verification event from the existing Harness X trace. A later milestone should bridge/tail that causal trace into a fine-grained UI stream rather than inventing a second event truth inside HTTP code.

### Create coding session

```text
POST /v1/sessions
Authorization: Bearer <local token>
Content-Type: application/json
```

The body is `CodingSessionRequest`. Accepted sessions return HTTP 202.

### Request cancellation

```text
POST /v1/sessions/{session_id}/cancel
Authorization: Bearer <local token>
```

Terminal sessions reject cancellation with HTTP 409.

## HTTP hardening

M34 uses the Python standard library HTTP server to avoid adding a web-framework dependency for this small personal surface.

The transport additionally:

- permits only literal `127.0.0.1` binding;
- requires a bearer token for all session data/mutations;
- rejects Host headers other than `127.0.0.1` or `localhost`, reducing DNS-rebinding exposure;
- bounds JSON request bodies to 2 MiB;
- requires JSON content type for session creation;
- does not emit permissive CORS headers;
- sends `no-store`, `nosniff`, and no-referrer headers on JSON responses;
- never serves arbitrary filesystem paths through a generic HTTP file endpoint.

A future browser UI should preferably be served from the same loopback origin. Cross-origin access should not be enabled by wildcard CORS.

## Authority boundary

The M34 invariant is:

> UI state and App Server session state are projections around the coding runtime, not substitutes for runtime authority.

Specifically, M34 cannot:

- directly mutate repository files on behalf of a model;
- bypass the existing tool executor or permissions;
- mark M25/M26 verification checks passed;
- rewrite M27 task authority;
- admit M28 memory without the existing verified closeout;
- change M29 reliability;
- validate/promote an M30 revision;
- declare a coding task successful when the coding report says it failed.

## Current limitations

- One worker is intentional; there is no GPU/model-server scheduler.
- Running-task cancellation is request/observation only, not hard preemption.
- The HTTP server is not a general remote-access server.
- M34 does not serve a GUI/static frontend yet.
- SSE currently exposes coarse durable session events, not the complete causal trace.
- There is no WebSocket layer; HTTP commands plus SSE are sufficient for the foundation.
- Server restart marks an interrupted active run failed rather than attempting unsafe instruction-pointer restoration.

Those limits keep M34 small enough to qualify without changing the existing cognitive/control architecture.
