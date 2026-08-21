# Milestone 21 — Coding Runtime

Milestone 21 turns the existing Harness X reasoning/tool/orchestrator boundaries into a first practical repository-editing runtime.

The goal is not a new coding-agent architecture. It is to reuse the architecture already built:

```text
coding request
    |
    v
TaskOrchestrator + GoalMemory + WorkingState
    |
    v
ReasoningService
    |
    | structured proposals only
    v
ToolExecutor
    |
    +--> workspace_list
    +--> workspace_read
    +--> workspace_search
    +--> workspace_write
    +--> workspace_patch
    `--> process_run
              |
              v
      software-owned verification
              |
         pass / feedback
```

## Hard boundaries

- The reasoning model never receives direct filesystem or subprocess handles.
- Model output remains a proposal. Candidate identity and provenance are software-owned.
- Every filesystem/process action crosses `ToolExecutor` permission, schema, budget, timeout, and trace checks.
- Workspace file tools resolve every path against one configured workspace root and reject path traversal.
- `process_run` uses an argv array with `shell=False`; shell text is not executed.
- The initial command policy permits a small executable set, read-only Git subcommands, and package-manager `run`/`test` only.
- The process environment is filtered rather than blindly inheriting arbitrary environment variables.
- A model saying `complete` does not complete the task. Configured verification commands must return exit code 0.
- Failed verification is written back into bounded working state so a later reasoning step can repair the change.

## Operator command

A local OpenAI-compatible reasoning server can be used through:

```bash
harness-x-code path/to/workspace \
  --base-url http://127.0.0.1:8080/v1 \
  --model local-model \
  --task "Build the requested website" \
  --verify "npm run build" \
  --output .harness-x/coding-run
```

Multiple verification commands may be supplied by repeating `--verify`.

## Initial coding tools

### `workspace_list`

Lists bounded workspace entries and excludes common dependency/build directories from recursive traversal.

### `workspace_read`

Reads bounded UTF-8 line ranges from files inside the workspace.

### `workspace_search`

Searches UTF-8 source files for exact text with bounded file and match counts.

### `workspace_write`

Creates a UTF-8 file or explicitly overwrites one when `overwrite=true` is supplied.

### `workspace_patch`

Performs an exact-text replacement only when the observed occurrence count matches the request. This gives edits a simple optimistic-concurrency check instead of silently patching an unexpected file state.

### `process_run`

Executes a bounded argv vector in a workspace-relative current directory with `shell=False`, filtered environment variables, captured output, and an explicit timeout.

The first command policy includes Python, pytest, Ruff, Node, npm/pnpm/yarn script execution, and read-only Git inspection. Mutating Git actions such as commit/push/reset/clean are intentionally not exposed.

## Verification loop

The first runtime intentionally keeps completion simple and strong:

```text
model status=complete
        |
        v
configured verifier commands
     /       \
   pass      fail
    |          |
 COMPLETE   WorkingState evidence
               |
               v
          next model step
```

This means a generated site may not finish merely because the model thinks the implementation is done; for example, `npm run build` must actually succeed when that verifier is configured.

## Containment limitation

The workspace file tools are path-contained, but `process_run` is **not an OS/container sandbox**. A permitted local program or repository script can itself perform filesystem or network actions available to the host process. Milestone 21 therefore uses command allowlisting, argv execution, filtered environment variables, bounded runtime, explicit operator-selected workspaces, and no Git mutation authority, but stronger process/container isolation remains future work.

## First real acceptance task

The intended first physical task is a website build from an existing starter repository. The same local model can later be run through a simple coding loop and through Harness X using the same request for an informal practical comparison, but that comparison is not required for runtime operation.
