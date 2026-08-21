# Milestone 24 — Isolated task workspaces

Milestone 24 changes where coding work happens. M22 still owns long-horizon control and M23 still owns repository intelligence and the coding ACI, but the workspace handed to those layers is now isolated from the operator's source checkout by default.

## Goal

A model-selected write, patch, verifier, test, formatter, or other permitted process must operate on a task workspace rather than directly on the user's source tree unless the operator explicitly chooses `--in-place`.

The milestone is about **workspace isolation and lifecycle**, not OS/container security. `process_run` still executes host processes with the permissions available to Harness X. M24 prevents ordinary task-file mutation from targeting the source checkout; it does not turn subprocesses into a security sandbox.

## Architecture

```text
operator source workspace
        |
        v
TaskWorkspaceIsolationManager
        |
        +-- Git source ------------------------------+
        |   exact HEAD + source fingerprint          |
        |   independent local clone                  |
        |   replay dirty tracked/staged patch        |
        |   copy untracked files                     |
        |   optionally copy ignored support paths    |
        |                                             |
        +-- non-Git source --------------------------+
            filesystem snapshot copy                 |
                                                      v
                                            isolated task workspace
                                                      |
                                                      v
                                  M23 repository intelligence + ACI
                                                      |
                                                      v
                                       M22 control + verification
                                                      |
                                                      v
                                           task-local delta export
                                                      |
                                  +-------------------+------------------+
                                  |                                      |
                             retain workspace                       cleanup workspace
```

## Source identity

Every isolated run records a `SourceWorkspaceIdentity` with:

- source root;
- Git/non-Git classification;
- exact Git HEAD when available;
- branch when available;
- whether the Git source was dirty;
- explicit copied support paths;
- SHA-256 source-state fingerprint.

For Git sources the fingerprint binds the exact HEAD, porcelain status, tracked/staged diff, untracked-file contents, and requested support-path contents. The source identity is recomputed after the isolated workspace is prepared. If it changed during preparation, Harness X rejects the snapshot instead of silently starting from a mixed source state.

For non-Git sources the fingerprint is computed from the bounded-change-tracked filesystem contents before and after copying. Runtime/dependency/cache directories excluded from task-delta accounting are not treated as source-code identity.

## Git preparation

Git workspaces use an independent local clone:

```text
git clone --local --no-hardlinks --no-checkout SOURCE TASK_WORKSPACE
git checkout --detach EXACT_HEAD
```

`--no-hardlinks` deliberately trades disk space for a stronger lifecycle boundary: a retained task repository owns its copied Git objects and does not depend on the source repository's object database or worktree metadata.

If the source is dirty:

1. `git diff --binary HEAD --` captures tracked staged/unstaged content relative to the exact HEAD.
2. Harness X applies that patch inside the task clone.
3. `git ls-files --others --exclude-standard -z` enumerates untracked files, which are copied into the task clone.
4. Explicit support paths are copied.
5. The source fingerprint is recomputed and must match the pre-copy fingerprint.

The isolated baseline therefore matches the operator-visible source state for tracked and untracked Git content without attributing those pre-existing changes to the coding model.

Ignored files are not automatically copied for Git sources. This matters for repositories whose build depends on local ignored directories such as `node_modules`. Use, for example:

```powershell
harness-x-code `
  D:\projects\my-site `
  --task "Implement the requested feature" `
  --verify "npm run build" `
  --isolation-copy-path node_modules
```

Support paths are copied, not symlinked or hardlinked. That can be expensive, but it prevents task processes from mutating the source through a shared mutable dependency tree.

## Non-Git preparation

A non-Git source is copied into the task workspace with Git/Harness-X metadata excluded. Symlinks are dereferenced during the snapshot copy instead of intentionally creating a path back to the source tree.

This is a correctness-first implementation. Large non-Git trees can therefore be expensive to copy.

## Task delta

M24 records a content manifest immediately after isolation is prepared. After the coding runtime finishes, it records a second manifest and derives task-local:

- added files;
- modified files;
- deleted files;
- baseline SHA-256 for files that existed at task start;
- final SHA-256 and size for files that exist at task end.

The comparison is against the **isolated starting state**, not Git `HEAD`. Therefore pre-existing operator dirtiness does not appear as model-created work.

Before optional cleanup, final added/modified files are copied to:

```text
<output>/isolation/isolated-changes/files/...
```

and the structured delta is written to:

```text
<output>/isolation/isolation-result.json
```

Git runs also retain:

```text
<output>/isolation/source-initial.patch
<output>/isolation/isolated-final.patch
```

The first is the source's tracked dirty patch at task start. The second is the final isolated working-tree diff relative to the exact source HEAD. The authoritative task-local delta remains the baseline/final manifest comparison because the final Git diff intentionally also contains any pre-existing source dirtiness.

M24 does **not** automatically apply these changes back to the source checkout. Automatic write-back would need its own source-freshness and conflict policy; until that exists, the source remains an independent authority boundary.

## Retention

`harness-x-code` accepts:

```text
--retain-workspace always
--retain-workspace on_failure
--retain-workspace never
```

The default is `always` during development so the exact environment is available for inspection. Regardless of retention, the task delta and changed-file bundle are exported before cleanup.

The default isolation parent is outside the source checkout under the operating system's temporary directory. A custom `--isolation-root` is allowed, but Harness X rejects an isolation root inside the source workspace to prevent recursive/self-mutating task layouts.

## CLI behavior

Isolation is the default:

```powershell
harness-x-code `
  D:\projects\my-site `
  --task "Build the requested dashboard" `
  --verify "npm run build"
```

Direct source mutation now requires an explicit escape hatch:

```text
--in-place
```

This preserves debugging/backward compatibility but is not the M24 default path.

## Preserved M22/M23 semantics

M24 does not change:

- model proposal authority;
- the 13-tool M23 ACI;
- tool schemas or permissions;
- M22 phase transitions;
- commitments;
- progress/stuck accounting;
- reasoning/tool budgets;
- verification freshness;
- software-owned completion authority;
- the M23 repository-context protocol.

The same runtime runs; only its workspace root changes.

## Deliberate limitations

- `process_run` is still not an OS/container sandbox. A permitted executable can use host permissions and network available to the process.
- Git submodules are not materialized automatically in M24.
- Git ignored dependencies must be copied explicitly when needed.
- Copying large dependency trees or large non-Git workspaces can be expensive.
- Task-delta accounting excludes common generated/runtime trees such as `node_modules`, `.venv`, caches, build output, and coverage output.
- Symlink-heavy repositories can have portability edge cases in the exported changed-file bundle.
- M24 exports changes but does not automatically reconcile/apply them back to a possibly changed source checkout.

These are intentional boundaries. M25 can build stronger verification/promotion/apply semantics on top of a task workspace whose starting state and resulting delta are explicit.
