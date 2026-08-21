# Milestone 25 — Typed Verification Platform

M25 turns coding verification from an unstructured list of process commands into a software-owned evidence system. It is stacked on M24 isolated task workspaces and keeps M22 coding-control authority unchanged.

## Authority model

The model may recommend work and may say it believes a task is complete. It does not own verification state.

```text
model completion claim
        ↓
M22 VERIFY phase
        ↓
M25 VerificationPlan
        ↓
Harness X tools / permissions
        ↓
VerificationRun
        ↓
required checks pass on exact workspace state?
        ├─ no  → M22 IMPLEMENT / repair
        └─ yes → M22 REVIEW / completion authority
```

A passing run is authoritative only for the exact verification-plan fingerprint and exact source-relevant workspace fingerprint it records.

## Verification plan

`VerificationPlan` is immutable and SHA-256 fingerprinted. Check IDs must be unique.

Supported M25 check kinds:

- `command` — runs a bounded permitted process through `ToolExecutor` / `process_run`;
- `file_exists` — requires a workspace file to exist and be readable;
- `file_contains` — checks bounded UTF-8 file content.

Every check has a `requirement`:

- `required` — failure or verification error blocks completion;
- `advisory` — failure remains visible evidence but does not block completion.

Checks may also specify `when_changed` glob patterns. Such checks run only when the task workspace differs from its task-start baseline in a matching path.

## Example plan

```json
{
  "schema_version": "coding-verification-plan-v1",
  "name": "web application verification",
  "fail_fast_required": true,
  "checks": [
    {
      "kind": "command",
      "check_id": "unit_tests",
      "name": "unit tests",
      "requirement": "required",
      "argv": ["python", "-m", "pytest", "-q"],
      "cwd": ".",
      "when_changed": ["src/**", "tests/**"]
    },
    {
      "kind": "command",
      "check_id": "frontend_build",
      "name": "frontend build",
      "requirement": "required",
      "argv": ["npm", "run", "build"],
      "cwd": "web",
      "when_changed": ["web/**"]
    },
    {
      "kind": "file_exists",
      "check_id": "manifest_exists",
      "name": "package manifest remains present",
      "requirement": "required",
      "path": "web/package.json"
    },
    {
      "kind": "file_contains",
      "check_id": "migration_marker",
      "name": "migration marker exists",
      "requirement": "advisory",
      "path": "CHANGELOG.md",
      "needle": "Migration",
      "should_contain": true,
      "case_sensitive": true,
      "max_bytes": 262144
    }
  ]
}
```

The fingerprint field is derived by Harness X and does not need to be supplied manually.

## CLI

Existing command-based verification remains the zero-migration path:

```powershell
harness-x-code D:\projects\app `
  --task "Fix the failing implementation." `
  --verify "python -m pytest -q"
```

Those commands are compiled into required typed command checks.

A richer plan may be loaded with:

```powershell
harness-x-code D:\projects\app `
  --task "Implement the requested feature." `
  --verification-plan .\verification.json
```

`--verification-plan` and repeatable `--verify` are composable. CLI commands are appended as additional required checks with unique IDs. At least one verification source is required.

As in M21, ordinary Python aliases in CLI verification commands are rebound to the interpreter running Harness X so Windows does not accidentally invoke a different system Python.

## Evidence and freshness

Each `VerificationRun` records:

- plan fingerprint;
- workspace fingerprint before verification;
- workspace fingerprint after verification;
- changed files relative to task start;
- each check's status and evidence;
- required and advisory failures;
- deterministic required-failure signature;
- aggregate verdict;
- stable run fingerprint.

`latest_is_fresh()` is true only while the current workspace still matches the post-verification workspace fingerprint and the same plan remains configured.

M22 receives M25's canonical typed `failure_signature` for repeated-failure control. Successful completion evidence is bound to the typed verification-run fingerprint, plan fingerprint, and verified workspace fingerprint rather than to a lossy compatibility row.

## Fail-closed file evidence

`workspace_read` is intentionally bounded. A truncated prefix can prove that a needle was found, but cannot prove that a needle is absent from the remainder of the file.

Therefore M25 uses `StrictVerificationPlatform`:

```text
needle found in observed prefix
        → conclusive (pass or fail according to should_contain)

needle not found + complete read
        → conclusive

needle not found + truncated read
        → ERROR: file_content_indeterminate_truncated
```

This prevents both false positive assertions and false negative assertions caused by bounded reads.

## Verifier source stability

M25 fingerprints source-relevant workspace files immediately before and after a verification run. If a verifier changes source content, Harness X adds the required failure `__workspace_stability__` even when the underlying command exited zero.

This is intentional: formatting, generation, migration, or repair commands that mutate the codebase are not accepted as pure verification evidence.

## Artifacts

A verified coding run writes:

```text
coding-task-report.json
verification-plan.json
verification-runs.json
trace_*.jsonl
```

When composed with M24 isolation, M24's isolation manifests and exported task delta are written alongside them.

## Scope boundary

M25 verifies process, file, and source-state evidence. It does **not** claim that a web page looks correct, that a UI interaction works, or that an application is semantically usable in a browser.

Those capabilities belong to M26, where browser/application observations become another independent evidence provider feeding the same software-owned verification authority.

M25 also does not turn M24 into an OS/container sandbox. Permitted processes still execute with the host permissions/network made available by the existing coding execution boundary.
