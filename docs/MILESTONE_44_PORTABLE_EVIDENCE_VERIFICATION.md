# Milestone 44 — Portable Evidence Verification

M44 is stacked directly on frozen M43 and closes one narrow evidence portability gap left explicitly outside M43: Harness X can export a deterministic terminal evidence manifest plus the exact canonical report and verified causal trace, but there is no first-party offline command that checks those downloaded files together after they leave the App Server.

M44 adds one local read-only CLI verifier. It does not contact the App Server, create a new server endpoint, browse arbitrary remote artifacts, sign evidence, establish remote trust, or add task/runtime authority.

## Scope

M44 adds one top-level installed command:

```text
harness-x verify-evidence MANIFEST [--report PATH] [--trace PATH]
```

The command accepts local paths chosen explicitly by the operator. It performs no network requests and does not discover files recursively, enumerate directories, follow manifest-provided source paths, or fetch missing evidence.

The manifest remains the M43 portable correlation object. M44 verifies the manifest itself and, when corresponding local files are supplied, verifies those files against the identities recorded by the manifest.

## Installed CLI compatibility

M44 does not rewrite the existing `src/harness_x/cli.py` dispatcher. The installed `harness-x` entry point changes by one line to a thin `cli_entry.py` wrapper.

The wrapper builds the existing legacy parser, adds exactly one `verify-evidence` subcommand, and delegates every pre-M44 command back to the existing `harness_x.cli.main()` implementation. The evidence verifier is imported lazily only when `verify-evidence` is selected, so legacy `harness-x` commands and help do not acquire an eager App Server/evidence import dependency.

`python -m harness_x.cli` intentionally remains the pre-M44 module surface; M44's new command is the installed `harness-x verify-evidence` interface.

## Manifest verification

The verifier reads the supplied manifest through an explicit bounded regular-file input path and requires strict UTF-8 JSON. Duplicate object keys are rejected at every JSON object level rather than relying on last-key-wins parsing.

It validates the exact M43 schema `app-terminal-evidence-manifest-v1` with Pydantic `extra="forbid"` semantics and independently checks the supplied manifest self-fingerprint over the canonical JSON form excluding the `fingerprint` field.

M43's model validator derives a fingerprint when a model is instantiated. M44 therefore retains the original untrusted `fingerprint` string from parsed JSON before model validation and explicitly compares that supplied value with the independently rederived model fingerprint. A stale or forged supplied fingerprint cannot be silently replaced and accepted.

The command reports the SHA-256 and exact byte count of the manifest file itself so an operator can compare them with an independently recorded M43 HTTP response digest if one was retained. Reformatting otherwise equivalent manifest JSON may preserve the canonical self-fingerprint while changing this exact file-byte digest; M44 reports the actual bytes it received rather than pretending formatting is unchanged.

M44 does not claim that a matching self-fingerprint or file digest authenticates the manifest's origin.

## Report verification

When the manifest says the coding report is `not_available`:

- `--report` must be absent;
- supplying a report is a mismatch and fails closed.

When the manifest says the report is `available`:

- `--report` is required;
- the file is read through the same bounded regular-file input helper;
- exact byte count and SHA-256 must equal the M43 manifest;
- the payload must be strict UTF-8;
- JSON parsing must succeed;
- the JSON root must be an object;
- the fixed evidence filename remains `coding-task-report.json` in the manifest, but the local operator may store the downloaded file under a different filesystem path/name and pass that path explicitly.

M44 preserves M39 provenance semantics rather than upgrading them:

- `verified` requires the manifest's attested byte count/SHA-256 to be present and equal the current source identity;
- `legacy_unattested` requires no attested byte identity and remains weaker provenance;
- `unavailable` requires no attested byte identity and remains weaker provenance.

A self-fingerprinted manifest with internally contradictory provenance (for example `legacy_unattested` plus an attested byte identity) fails rather than being normalized.

The offline verifier cannot reconstruct the App Server's durable `ARTIFACT_AVAILABLE` event chain from the manifest alone. It validates the event-hash field's schema because that field is inside the self-fingerprinted manifest, but does not claim to independently prove the lifecycle event existed.

## Trace verification

When the manifest says the causal trace is `not_available`:

- `--trace` must be absent;
- supplying a trace is a mismatch and fails closed.

When the manifest says the trace is `available`:

- `--trace` is required;
- the file is read through the same bounded regular-file input helper with the M42 32 MiB ceiling;
- exact byte count and SHA-256 must equal the M43 manifest;
- the exact captured bytes are passed through the M35/M42 `verify_trace_payload()` integrity algorithm with a complete-final-line requirement;
- every record must therefore satisfy trace ID, contiguous step, previous-hash, recomputed event-hash, schema, and timestamp ordering checks;
- verified record count must equal the manifest;
- verified final event hash (or empty-trace state) must equal the manifest.

A maliciously modified trace does not become acceptable merely by updating the manifest's current byte SHA-256: unless the causal records themselves form a valid M35/M42 chain, offline verification still fails. Conversely, because M43/M44 do not provide signatures or a persisted historical trace-content attestation, an actor who can rewrite an entire self-consistent trace and generate a new self-fingerprinted manifest can create a new internally consistent set. M44 does not claim otherwise.

As with report evidence, the durable `TRACE_ATTACHED` event identity recorded by M43 is correlated by the manifest fingerprint but cannot be independently reconstructed from only the portable three-file set.

## Input boundary

M44 is a local explicit-input verifier, not a file browser.

Each supplied path is read only after these checks:

- path exists;
- final source is not a symbolic link;
- normalized absolute lexical path resolves strictly to itself, rejecting symbolic-link substitution in resolved components;
- a pre-open `lstat` requires a regular file, avoiding FIFO/device reads;
- final-component `O_NOFOLLOW` is used when available;
- opened descriptor is independently required to be a regular file by `fstat`;
- input size is checked against descriptor metadata and against a bounded `maximum + 1` read.

Default hard limits:

- manifest: 2 MiB;
- coding report: existing `MAX_REPORT_BYTES`;
- causal trace: existing `MAX_TRACE_EXPORT_BYTES` (32 MiB).

The digest, parsing, and trace verification all operate on the exact retained bytes from that one bounded descriptor read. The verifier never writes to any supplied evidence path.

These checks reject ordinary leaf/intermediate symlink substitution but are not claimed as a hostile concurrent-filesystem transaction or OS-level immutable snapshot primitive.

## CLI result contract

Success prints one compact deterministic summary beginning with `valid:` and returns exit code 0.

The summary includes:

- session ID;
- manifest file byte count and SHA-256;
- report state (`verified`, `legacy_unattested`, `unavailable`, or `not_available`);
- trace state (`verified` or `not_available`);
- verified trace record count when a trace exists.

Invalid manifest syntax/schema/fingerprint, duplicate keys, missing required component files, unexpected component files, byte/hash disagreement, invalid report JSON, invalid trace integrity, or provenance inconsistency fail visibly with argparse's existing nonzero CLI error behavior and do not emit a `valid:` line.

## Authority and provenance boundary

M44 verifies portable evidence consistency only. It cannot:

- authenticate who generated the manifest;
- prove the manifest was downloaded from a particular App Server;
- reconstruct or independently verify the full App Server lifecycle ledger from the three portable files;
- independently prove the manifest-recorded `ARTIFACT_AVAILABLE` or `TRACE_ATTACHED` events existed without the lifecycle ledger;
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
- `harness-x verify-evidence` appears in installed CLI help without removing existing commands;
- legacy commands delegate to the frozen pre-M44 dispatcher;
- the verifier module is lazy-loaded only for the M44 command;
- strict bounded manifest read, duplicate-key rejection, and schema validation;
- supplied manifest fingerprint is independently retained/recomputed and mismatches fail;
- manifest file byte count/SHA-256 reported from exact input bytes;
- available report requires `--report`; unavailable report rejects `--report`;
- report exact byte/SHA match, UTF-8/JSON-object validation, and all M39 provenance states remain distinct;
- internally contradictory report provenance fails;
- available trace requires `--trace`; unavailable trace rejects `--trace`;
- trace exact byte/SHA match plus complete M35/M42 hash-chain verification, record count, trace ID, and final event hash match;
- a trace integrity mutation still fails when a newly self-fingerprinted manifest is updated to the modified file's current SHA-256;
- partial final trace line fails;
- leaf and intermediate-component symlink substitution fail through the shared file reader;
- non-regular and over-limit inputs fail;
- verifier performs no network access and writes no evidence files;
- success output is deterministic and starts with `valid:`;
- existing `verify-trace`, App Server routes/UI, M39/M41 report validation/export, M42 trace export, and M43 manifest generation remain behaviorally unchanged;
- `src/harness_x/cli.py` remains byte-for-byte unchanged;
- exact M43→M44 diff remains confined to offline verification code, thin installed CLI wiring, tests, and this document;
- exact-head Linux CI passes.
