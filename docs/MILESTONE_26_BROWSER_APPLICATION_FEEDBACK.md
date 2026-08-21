# Milestone 26 — Browser / Application Feedback

M26 makes running applications legible to the Harness X coding runtime without making a browser an uncontrolled second agent or treating screenshots as authoritative proof.

It composes on top of the qualified M22–M25 coding stack:

```text
reasoning model
    ↓
M22 long-horizon coding controller
    ↓
M23 repository intelligence + coding ACI
    ↓
M24 isolated task workspace
    ↓
M25 typed code/process/file verification
    ↓
M26 browser/application verification
```

The model can inspect and interact with a declared local application through bounded tools. Completion remains software-owned: required M25 checks must pass, required M26 browser checks must pass, and the exact M25 workspace evidence must remain fresh after browser execution.

## Design goals

M26 adds application feedback for tasks where build/tests alone cannot establish the requested behavior, especially frontend and full-stack work. It is intentionally narrower than general web browsing.

The hard boundaries are:

1. The application process is operator-declared. The model does not choose an arbitrary server command.
2. Browser navigation and browser network traffic stay on the declared local application origin by default.
3. Service workers are disabled in the live Playwright context so routed requests do not silently escape the browser policy.
4. Browser observations are bounded and typed.
5. Accessibility-oriented state is the default model-facing representation; screenshots are artifacts and failure evidence, not the primary reasoning substrate.
6. Browser verification uses a clean browser client rather than trusting state left by model-driven browsing.
7. Required verification fails closed when bounded evidence is incomplete.
8. Starting or exercising the application is not allowed to invalidate already-qualified M25 code evidence without detection.

M26 is still host-process/browser execution. It is not an OS/container sandbox. A permitted local application process has the host permissions and network access available to that process; M26's same-origin browser policy constrains the browser, not the application server process itself.

## Optional installation

Normal Harness X development and CI remain browser-free. Live browser support is an optional dependency:

```powershell
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

The deterministic `FakeBrowserProvider` is dependency-free and is used by standard CI.

## Application declaration

Browser mode requires an explicit `ApplicationServerSpec` JSON document.

Example `application.json`:

```json
{
  "schema_version": "application-server-spec-v1",
  "argv": ["npm", "run", "dev"],
  "cwd": ".",
  "base_url": "http://127.0.0.1:5173",
  "health_path": "/",
  "startup_timeout_seconds": 30,
  "shutdown_timeout_seconds": 8
}
```

`base_url` must be a pure loopback HTTP(S) origin: no credentials, path, query, or fragment. `health_path` is a path on that same origin.

Harness X owns the lifecycle:

```text
application spec
    ↓
validate cwd / executable policy
    ↓
start process in task workspace
    ↓
health check declared origin
    ↓
retain bounded process state + durable stdout/stderr
    ↓
restart after recoverable startup failure when a later browser action retries
    ↓
terminate process group during cleanup
```

A failed first startup is therefore evidence the model can repair; it is not automatically a terminal session failure.

## Browser provider boundary

`BrowserProvider` is replaceable. M26 ships two implementations:

- `FakeBrowserProvider`: deterministic, dependency-free CI/provider tests.
- `PlaywrightBrowserProvider`: live Chromium provider loaded lazily from the optional browser dependency.

The live provider uses an accessibility-oriented AI snapshot as the model-facing page representation. Observations also carry title, URL, console messages, page errors, and explicit truncation flags.

The provider keeps only bounded histories. The observation contract reports whether ARIA, console, or page-error evidence was truncated so verification can distinguish a conclusive result from an incomplete observation.

## Network policy

The live browser permits:

```text
HTTP/HTTPS  → exact declared app scheme + host + effective port
WebSocket   → corresponding same-origin ws/wss endpoint
about:
data:
blob:
```

Other hosts, other loopback ports, alternate loopback hostnames, and incompatible schemes are blocked. This prevents browser mode from becoming implicit access to arbitrary local services simply because they are on `localhost`.

Model-requested navigation must be a path on the declared application origin.

## Model-facing browser ACI

M26 declares seven browser capabilities:

```text
browser_open
browser_snapshot
browser_click
browser_fill
browser_select
browser_screenshot
browser_console
```

Selectors are typed and bounded:

```text
role + accessible name
label
text
test_id
CSS fallback
```

`browser_click`, `browser_fill`, and `browser_select` are persistent side effects because an application interaction can mutate session or backend state. `browser_screenshot` also creates a persistent artifact.

The model receives no Playwright object, browser context, arbitrary JavaScript evaluator, raw process handle, or unrestricted URL fetch primitive.

## Browser verification plan

Browser acceptance criteria are independently declared in a typed JSON plan.

Example `browser-verification.json`:

```json
{
  "schema_version": "browser-verification-plan-v1",
  "name": "dashboard acceptance",
  "checks": [
    {
      "kind": "browser_page",
      "check_id": "dashboard_visible",
      "name": "dashboard renders",
      "requirement": "required",
      "path": "/",
      "title_contains": "Dashboard",
      "snapshot_contains": ["Todo", "In Progress", "Done"],
      "snapshot_excludes": ["Fatal error"]
    },
    {
      "kind": "browser_console",
      "check_id": "console_clean",
      "name": "browser console is clean",
      "requirement": "required",
      "path": "/",
      "forbidden_console_levels": ["error"],
      "require_no_page_errors": true
    }
  ]
}
```

Available check kinds:

### `browser_page`

Opens a path and can assert:

- title contains a fragment;
- ARIA snapshot contains required fragments;
- ARIA snapshot excludes forbidden fragments.

### `browser_console`

Opens a path and can reject selected console levels and/or page errors.

### `browser_interaction`

Opens a path, performs a bounded sequence of semantic click/fill/select actions, then asserts required/forbidden fragments in the resulting ARIA snapshot.

Required and advisory semantics match M25: required failures block completion; advisory failures remain visible evidence but do not by themselves block completion.

## Fail-closed bounded evidence

M26 follows the same principle introduced for bounded file reads in M25: missing evidence is not silently treated as proof.

For ARIA snapshots:

```text
required fragment observed in bounded snapshot
    → conclusive positive evidence

forbidden fragment observed in bounded snapshot
    → conclusive failure

required fragment absent + complete snapshot
    → conclusive failure

required fragment absent + truncated snapshot
    → ERROR: browser_snapshot_indeterminate_truncated

forbidden fragment absent + complete snapshot
    → conclusive pass for that condition

forbidden fragment absent + truncated snapshot
    → ERROR: browser_snapshot_indeterminate_truncated
```

For console/page-error evidence:

```text
forbidden error observed
    → conclusive failure

no forbidden error observed + complete history
    → pass

no forbidden error observed + truncated relevant history
    → ERROR: browser_console_evidence_indeterminate_truncated
```

This avoids false confidence when a large page or long-running console exceeds the observation bound.

## Verification authority and freshness

The completion sequence is:

```text
model requests completion
        ↓
M25 typed verification
   ├── required failure → evidence → model repair
   └── pass
        ↓
reset browser client
        ↓
M26 browser verification
   ├── required failure → evidence → model repair
   └── pass
        ↓
re-evaluate M25 exact workspace freshness
   ├── stale → M26 required ERROR
   └── fresh → controller may complete
```

Browser verification does not simply accept model-created browser state. The browser client is reset before a verification run so cookies, local storage, transient navigation and model-driven UI interactions are not automatically promoted to independent acceptance evidence.

After browser verification, M26 calls the M25 freshness check again. If starting the application or exercising it changed source-relevant workspace state, M26 appends the required synthetic failure:

```text
check_id: __code_verification_freshness__
failure_code: code_verification_stale_after_browser
```

A browser pass therefore cannot complete the task on stale code evidence.

## Controller failure identity

M26 browser runs have their own deterministic required-failure signature and run fingerprint. M22 uses the current verification attempt's authoritative failure identity. A browser failure from an earlier attempt is not reused when a later attempt fails earlier in M25 code verification.

This matters for long-horizon control because repeated-failure detection and replanning must respond to the failure that actually occurred in the current attempt.

## Isolation composition

The normal browser runtime is composed inside M24 task-workspace isolation.

The declared application process starts in the isolated task workspace, browser verification observes that application, and task deltas are exported according to M24 rules. The operator source checkout is not modified unless the user explicitly selects the in-place escape hatch.

For repositories whose ignored dependencies are required to run the application, support paths can be copied into the isolated workspace, for example:

```powershell
--isolation-copy-path node_modules
```

This is a copy into the task workspace, not a mutable symlink back to the operator checkout.

## CLI

Browser mode is opt-in. It requires both an application declaration and browser verification plan.

```powershell
harness-x-code D:\projects\my-site `
  --task "Implement the requested dashboard and leave it working." `
  --verification-plan .\verification.json `
  --application-spec .\application.json `
  --browser-verification-plan .\browser-verification.json `
  --isolation-copy-path node_modules `
  --output .harness-x\coding-run
```

Existing non-browser runs keep M25 behavior and do not launch Chromium.

## Artifacts

M26 adds browser/application evidence alongside the existing coding report and M25 verification artifacts. Depending on the executed path, the output directory contains:

```text
coding-task-report.json
verification-plan.json
verification-runs.json
browser-verification-plan.json
browser-verification-runs.json
application/
    stdout.log
    stderr.log
browser/
    verification/
        ... failure screenshots ...
isolation/
    ... M24 isolation metadata and exported changes ...
```

Failure screenshots are supplemental evidence. The authoritative pass/fail state is the typed browser verification result, not visual interpretation by the language model.

## What M26 deliberately does not add

M26 does not yet attempt to provide:

- unrestricted internet browsing;
- arbitrary JavaScript execution by the model;
- visual-diff or computer-vision grading as completion authority;
- OS/container isolation for application processes;
- long-horizon history condensation or persistent strategy memory.

The last item is the next architectural problem. M27 should focus on durable long-horizon task state: obligations, decisions, evidence indexing, structured condensation, checkpoints/resume, and preventing high-value information from disappearing as the raw interaction history grows.

## Qualification target

Freeze M26 only after an exact-head CI run proves:

- standard/dev install remains Playwright-free;
- all deterministic tests pass;
- CLI/help and config validation pass;
- same-origin browser policy is covered;
- bounded ARIA/console/page-error evidence fails closed when indeterminate;
- browser failure can drive a repair and later pass;
- application mutation invalidates stale M25 evidence;
- M24 isolated execution preserves the operator source checkout.
