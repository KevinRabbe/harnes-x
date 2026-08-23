# Milestone 44 — Portable Evidence Verification

M44 is stacked directly on frozen M43 and closes one narrow evidence portability gap left explicitly outside M43: Harness X can export a deterministic terminal evidence manifest plus the exact canonical report and verified causal trace, but there is no first-party offline command that checks those downloaded files together after they leave the App Server.

M44 adds one local read-only CLI verifier. It does not contact the App Server, create a new server endpoint, browse arbitrary remote artifacts, sign evidence, establish remote trust, or add task/runtime authority.

## Scope

M44 adds one top-level command:

```text
harness-x verify-evidence MANIFEST [--report PATH] [--trace PATH]
```

The command accepts local paths chosen explicitly by the operator. It performs no network requests and does not discover files recursively, enumerate directories, follow manifest-provided source paths, or fetch missing evidence.

The manifest remains the M43 portable correlation object. M44 verifies the manifest itself and, when corresponding local files are supplied, verifies those files against the identities recorded by the manifest.

## Manifest verification

The verifier must read the supplied manifest through an explicit bounded regular-file input path and require strict UTF-8 JSON.

It must validate the exact M43 schema `app-terminal-evidence-manifest-v1` with Pydantic `extra="forbid"` semantics and independently recompute the manifest self-fingerprint over the canonical JSON form excluding the `fingerprint` field.

The verifier must not rely on the M43 model validator silently replacing an untrusted supplied fingerprint. The originally supplied fingerprint is compared with a separately recomputed value before the input is accepted.

The command reports the SHA-256 and exact byte count of the manifest file itself so an operator can compare them with an independently recorded M43 HTTP response digest if one was retained. M44 does not claim that a matching self-fingerprint or file digest authenticates the manifest's origin.

## Report verification

When the manifest says the coding report is `not_available`:

- `--report` must be absent;
- supplying a report is a mismatch and fails closed.

When the manifest says the report is `available`:

- `--report` is required;
- the file is read through a bounded regular-file input path;
- exact byte count and SHA-256 must equal the M43 manifest;
- the payload must be strict UTF-8;
- JSON parsing must succeed;
- the JSON root must be an object;
- the fixed evidence filename remains `coding-task-report.json` in the manifest, but the local operator may store the downloaded file under a different filesystem path/name and pass that path explicitly.

M44 preserves M39 provenance semantics rather than upgrading them:

- `verified` requires the manifest's attested byte count/SHA-256 to be present and equal the current source identity;
- `legacy_unattested` requires no attested byte identity and remains weaker provenance;
- `unavailable` requires no attested byte identity and remains weaker provenance.

The offline verifier cannot reconstruct the App Server's durable `ARTIFACT_AVAILABLE` event chain from the manifest alone. It validates the event-hash field's schema because that field is inside the self-fingerprinted manifest, but does not claim to independently prove the lifecycle event existed.

## Trace verification

When the manifest says the causal trace is `not_available`:

- `--trace` must be absent;
- supplying a trace is a mismatch and fails closed.

When the manifest says the trace is `available`:

- `--trace` is required;
- the file is read through a bounded regular-file input path with the M42 32 MiB ceiling;
- exact byte count and SHA-256 must equal the M43 manifest;
- the exact captured bytes are passed through the M35/M42 `verify_trace_payload()` integrity algorithm with a complete-final-line requirement;
- every record must therefore satisfy trace ID, contiguous step, previous-hash, recomputed event-hash, schema, and timestamp ordering checks;
- verified record count must equal the manifest;
- verified final event hash (or empty-trace sentinel state) must equal the manifest.

As with report evidence, the durable `TRACE_ATTACHED` event identity recorded by M43 is correlated by the manifest fingerprint but cannot be independently reconstructed from only the portable three-file set.

## Input boundary

M44 is a local explicit-input verifier, not a file browser.

Each supplied path is read only after these checks:

- path exists;
- final source is not a symbolic link;
- resolved path equals the supplied absolute/normalized path, rejecting symbolic-link substitution in resolved components;
- opened descriptor is a regular file;
- final-component `O_NOFOLLOW` is used when available;
- input is bounded before and during read.

Default hard limits:

- manifest: 2 MiB;
- coding report: existing `MAX_REPORT_BYTES`;
- causal trace: existing `MAX_TRACE_EXPORT_BYTES` (32 MiB).

The command never writes to any supplied evidence path.

## CLI result contract

Success prints one compact deterministic summary beginning with `valid:` and returns exit code 0.

The summary includes:

- session ID;
- manifest file byte count and SHA-256;
- report state (`verified`, `legacy_unattested`, `unavailable`, or `not_available`);
- trace state (`verified` or `not_available`);
- verified trace record count when a trace exists.

Invalid manifest syntax/schema/fingerprint, missing required component files, unexpected component files, byte/hash disagreement, invalid report JSON, invalid trace integrity, or provenance inconsistency must fail visibly with a nonzero exit through the existing CLI error boundary rather than producing a `valid:` line.

## Authority and provenance boundary

M44 verifies portable evidence consistency only. It cannot:

- authenticate who generated the manifest;
- prove the manifest was downloaded from a particular App Server;
- reconstruct or independently verify the full App Server lifecycle ledger from the three portable files;
- upgrade M42 trace identity into a historical immutable trace attestation;
- upgrade `legacy_unattested` or M39 `unavailable` report provenance to `verified`;
- establish task success, verification success, or semantic correctness;
- write, repair, normalize, redact, or mutate evidence files;
- execute tools/models;
- contact remote services;
- bypass permissions, budgets, lifecycle transitions, or runtime controls.

M44 therefore gives operators an offline consistency/integrity check for the portable evidence set, not a signature or remote trust root.

## Non-goals / limitations

M44 does not add signatures/public-key verification, certificate chains, timestamp authority, remote transparency logs, ZIP/session bundles, generic artifact browsing, lifecycle-ledger export, desktop packaging, network verification, evidence repair, or semantic report evaluation.

## Deterministic acceptance

Before freeze, M44 must prove:

- exact frozen M43 base;
- `harness-x verify-evidence` appears in installed CLI help;
- strict bounded manifest read and schema validation;
- supplied manifest fingerprint is independently recomputed and mismatches fail;
- manifest file byte count/SHA-256 reported from exact input bytes;
- available report requires `--report`; unavailable report rejects `--report`;
- report exact byte/SHA match, UTF-8/JSON-object validation, and all M39 provenance states remain distinct;
- available trace requires `--trace`; unavailable trace rejects `--trace`;
- trace exact byte/SHA match plus complete M35/M42 hash-chain verification, record count, trace ID, and final event hash match;
- partial final trace line fails;
- source symlink and intermediate-component symlink substitution fail;
- non-regular and over-limit inputs fail;
- verifier performs no network access and writes no evidence files;
- success output is deterministic and starts with `valid:`;
- existing `verify-trace`, App Server routes/UI, M39/M41 report validation/export, M42 trace export, and M43 manifest generation remain behaviorally unchanged;
- exact M43→M44 diff remains confined to offline verification code, CLI wiring, tests, and this document;
- exact-head Linux CI passes.
