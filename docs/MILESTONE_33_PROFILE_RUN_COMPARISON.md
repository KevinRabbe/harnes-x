# Milestone 33 — Strict Offline Profile Run Comparison

M33 adds a controlled way to compare two independently executed Harness X coding runs without introducing a model router, voting system, automatic escalation policy, or multi-model server controller.

The operator still chooses exactly one M32 profile for each run. Heavy local models may be served one at a time. After both runs finish, M33 compares their software-owned artifacts offline.

## Authority boundary

M33 does not decide which model is better and it does not change future model selection.

The comparison layer may:

- validate whether two run artifacts are actually comparable;
- report success/failure relations;
- expose descriptive deltas in reasoning steps, tool actions, verification attempts, and changed-file counts;
- preserve exact verification evidence references;
- report where changed-file sets agree or differ.

It may not:

- assign a winner or scalar score;
- route the next task to a model;
- vote across model outputs;
- convert a model result into verification authority;
- promote procedures or improvement candidates;
- mutate project memory;
- start or switch model servers.

The existing invariants still apply: model output is not state mutation, model completion is not task completion, and only software-owned verification can establish a passing coding/application result.

## Why comparison is offline

The M32 personal setup intentionally permits one heavyweight local server at a time. For example, `main`, `coder`, and `reasoning` may all use `http://127.0.0.1:8000/v1`, with the operator changing which model is actually served.

A controller that automatically ran `main` and then reconfigured the server for `coder` would add model-server orchestration and implicit routing policy that M32 intentionally avoided.

M33 therefore separates execution from comparison:

1. run one explicit profile through normal Harness X;
2. change the local server if needed;
3. run the second explicit profile through normal Harness X;
4. compare the two completed artifact roots offline.

## Comparison-grade run command

Use `harness-x-profile-run` rather than ordinary `harness-x-code` when the run is intended for a controlled comparison.

It reuses the normal coding parser and full isolated M30 runtime stack, but adds these constraints:

- `--model-profile` is required;
- `--in-place` and task-state resume are forbidden;
- the output path must be an absent or empty directory;
- a seeded project-memory run must use an explicit logical project key matching the seed's stored M28 identity.

Before model work begins it writes:

- `model-selection.json` from M32;
- `coding-run-manifest.json` from M33.

The M33 manifest binds the run to:

- exact Harness X installed distribution version;
- SHA-256 fingerprint of the installed Harness X Python implementation;
- task text;
- M25 verification-plan fingerprint;
- M26 browser-plan fingerprint when applicable;
- normalized application-server-spec fingerprint when applicable;
- headed/headless browser posture;
- starting project-memory fingerprint and logical project key;
- model selection;
- reasoning/tool/output budgets and deterministic coding-control thresholds;
- isolation support paths and retention posture.

The final M30 runtime still writes the normal `coding-task-report.json` and all trace, verification, isolation, long-horizon, project-memory, reliability, and revision artifacts.

## Starting project memory

Sequential runs must not silently learn from one another if they are going to be treated as a controlled model comparison.

`coding-run-manifest.json` therefore records an exact SHA-256 fingerprint of the project-memory directory *before* the run mutates it. The fingerprint covers every regular file recursively and rejects symlinks.

There are two straightforward ways to obtain matching starting-memory fingerprints.

### Empty independent roots

Give each profile a different project-memory directory that does not exist yet, but the same logical project key:

```powershell
harness-x-profile-run D:\harnes_x `
  --task "<same task>" `
  --verification-plan D:\eval\verification.json `
  --model-profile main `
  --project-memory-root D:\eval\memory-main `
  --project-memory-key harnes-x-eval `
  --output D:\eval\run-main

# Switch the local server to the coder model if needed.

harness-x-profile-run D:\harnes_x `
  --task "<same task>" `
  --verification-plan D:\eval\verification.json `
  --model-profile coder `
  --project-memory-root D:\eval\memory-coder `
  --project-memory-key harnes-x-eval `
  --output D:\eval\run-coder
```

Both fresh roots have the same exact empty-directory fingerprint before execution.

### Clone one existing memory snapshot

M28 binds persisted project memory to its `project_key`. If an existing project-memory directory is used as the seed, **do not invent a new evaluation key**. Read the exact `project_key` from the seed's `project-memory.json` and use that same value for both cloned targets.

For a project whose stored key is `D:\harnes_x`:

```powershell
$seed = "D:\harnes_x\.harness-x\project-memory"
$projectKey = "D:\harnes_x"  # exact project_key stored in $seed\project-memory.json

harness-x-profile-run D:\harnes_x `
  --task "<same task>" `
  --verification-plan D:\eval\verification.json `
  --model-profile main `
  --comparison-memory-seed $seed `
  --project-memory-root D:\eval\seeded-memory-main `
  --project-memory-key $projectKey `
  --output D:\eval\run-main

# Switch the local server if needed.

harness-x-profile-run D:\harnes_x `
  --task "<same task>" `
  --verification-plan D:\eval\verification.json `
  --model-profile coder `
  --comparison-memory-seed $seed `
  --project-memory-root D:\eval\seeded-memory-coder `
  --project-memory-key $projectKey `
  --output D:\eval\run-coder
```

The profile-run preflight reads the seed identity without mutating it and rejects a mismatched `--project-memory-key`. The seed copy also rejects file/directory symlinks, requires an absent or empty target, and verifies that the copied target has the same exact directory fingerprint as the seed before the model is allowed to run.

Do not use a seed directory that another process is mutating while copies are being made.

## Strict comparability

`harness-x-compare-runs` validates each run internally and then compares the pair.

A run is internally invalid if:

- `model-selection.json` disagrees with the selection embedded in `coding-run-manifest.json`;
- the report task disagrees with the pre-run manifest task;
- the report verification-plan fingerprint disagrees with the pre-run manifest;
- the browser-plan fingerprint disagrees with the pre-run manifest;
- the expected isolation/source evidence is absent;
- the manifest fingerprint is invalid or has been tampered with.

Two valid runs are `strictly_comparable=true` only when all of these controlled conditions match:

- Harness X distribution version;
- exact Harness X Python implementation fingerprint;
- task text;
- exact isolated source-workspace fingerprint;
- M25 verification-plan fingerprint;
- M26 browser-verification-plan fingerprint or absence of browser mode;
- application-server-spec fingerprint or absence of browser mode;
- headed/headless browser posture;
- starting project-memory fingerprint;
- logical project-memory key;
- max reasoning steps;
- max tool actions;
- max output tokens;
- baseline-verification policy;
- idle/inspection/no-progress/same-failure control thresholds;
- isolation support paths.

The project-memory *directory path* may differ. Separate paths are expected. Their starting content fingerprint and logical key are what must match.

The source fingerprint comes from M24 isolation. This means the two profile runs may happen hours apart, but a source change between them makes the pair non-comparable rather than silently contaminating the result. The Harness X package fingerprint similarly prevents two different harness implementations from being labeled a controlled model comparison merely because both distributions report the same package version.

## Offline comparison

```powershell
harness-x-compare-runs `
  D:\eval\run-main `
  D:\eval\run-coder `
  --output D:\eval\main-vs-coder.json
```

When strict conditions match, the command returns success and the report records:

- both model selections;
- both manifest fingerprints;
- Harness X version/package fingerprints;
- exact source/code-verification/browser/application fingerprints;
- browser headed/headless posture;
- starting-memory fingerprint;
- success/failure state;
- latest code/browser verification evidence fingerprints and verdicts;
- reasoning steps;
- tool actions;
- verification attempts;
- final coding phase;
- pending commitments;
- failure reason when present;
- changed-file sets;
- right-minus-left descriptive count deltas.

There is intentionally no `winner`, `score`, or routing recommendation.

## Incomparable runs

The comparator still creates a report when controlled conditions differ. It lists every detected incompatibility and sets:

```text
strictly_comparable = false
```

By default the CLI returns exit code `2` in this case. `--allow-incomparable` changes only the CLI exit status; it does not hide the mismatches or convert the pair into a controlled comparison.

This supports forensic inspection without weakening the benchmark contract.

## Browser-backed runs

Comparison-grade profile runs may use the normal M26 application/browser flags. The pre-run manifest records the browser-verification-plan fingerprint, the normalized `ApplicationServerSpec` fingerprint, and headed/headless posture. The offline comparator additionally records the latest browser verification run fingerprint and verdict.

A browser-backed run and a non-browser run are not strictly comparable. Two browser-backed runs with different M26 plans, app launch specs, or headed/headless modes are not strictly comparable.

## What M33 does not establish

A single pair of runs does not establish a universal model ranking. Even a strictly controlled comparison is evidence about one task, source snapshot, Harness X implementation, project-memory snapshot, model profile, and runtime environment.

M33 does **not** fingerprint the external inference server binary, GPU driver/runtime, quantized weight files, or provider-side implementation behind a remote API. Operators should keep those fixed and record them externally for serious experiments. Repeated trials are still required to estimate stochastic variance.

M33 deliberately stops at faithful experimental provenance and descriptive outcome comparison. Aggregation, repeated trials, statistical evaluation, MINIMAL-vs-FULL Harness X experiments, or automatic model policy are separate milestones and must preserve the same authority boundaries.
