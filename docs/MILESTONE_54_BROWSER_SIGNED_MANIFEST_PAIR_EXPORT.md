# Milestone 54 — Browser Signed-Manifest Pair Export

M54 is stacked directly on frozen M53 and closes one narrow operator-UI usability gap left by M53: the local App Server can produce a detached Ed25519 signature over its current terminal evidence manifest, but the browser previously offered no fail-closed way to obtain a manifest/signature pair while checking that both responses refer to the same exact manifest bytes.

M54 adds **one authenticated operator-UI action that fetches the existing M43 manifest and M53 detached signature, validates their exact byte/header/schema correlation, and only then initiates downloads for the two frozen portable files**.

M54 is not browser-side signature trust. The UI has no trusted public key and does not claim that the displayed key fingerprint identifies an operator, installation, host, or server. Cryptographic signature verification remains the frozen M52 offline verifier with a public key obtained through an external trusted channel.

## Stack

Exact frozen M53 base:

`cc0ba28199005144906dda39837d1ca7828f1da3`

First M54 commit / scope document:

`635f4a232f252568641df0ae305a54afdcde7e33`

M54 remains stacked directly on that M53 SHA. Frozen M53 PR #60 must remain unchanged, draft/open/unmerged.

The exact final M54 head and synthetic merge are intentionally **not self-referenced in this tracked document**: writing them here necessarily creates another commit and invalidates the value. The PR freeze record is authoritative for the exact frozen head after the final exact-head CI completes.

## Implementation contract

M54 adds one operator action:

`Download signed manifest pair`

for the currently selected terminal session.

It uses only existing authenticated endpoints:

- `GET /v1/sessions/{session_id}/evidence/manifest`
- `GET /v1/sessions/{session_id}/evidence/signature`

M54 adds no App Server HTTP route, signing semantics, key management, evidence generation, session mutation, or offline verifier change.

The existing standalone `Download evidence manifest` action is preserved and remains available even when M53 signing is not configured.

## Browser state boundary

The M54 client follows the existing operator-download state pattern:

- bearer token retained only in M54 page memory;
- no `localStorage` credential/evidence storage;
- no `sessionStorage` credential/evidence storage;
- no cookie use;
- no credential/evidence URL query or fragment state;
- one generation counter tied to selected-session identity;
- one active `AbortController` for the pair operation;
- terminal states exactly `succeeded`, `failed`, `cancelled`;
- selected-session mutation cancels active work and invalidates stale async completion;
- explicit lock and observed global `Locked` state clear M54-local token, cancel active work, and clear status;
- `beforeunload` cancels active work;
- HTTP 401 from either request clears only M54's page-memory token and fails visibly.

No session selection, lifecycle state, durable evidence, or server state is mutated by M54.

## Manifest validation

The pair flow fetches the manifest first using authenticated GET, `Accept: application/json`, `cache: no-store`, `credentials: omit`, and the operation abort signal.

Before retaining the response for a possible download, the browser requires:

- HTTP success;
- exact JSON UTF-8 content type;
- exact frozen manifest filename;
- canonical lowercase 64-hex manifest SHA response header;
- decimal safe-integer Content-Length;
- one `arrayBuffer()` body read and exact byte-count equality;
- Web Crypto SHA-256 of the exact response bytes equal to the response SHA header;
- fatal UTF-8 decode and valid JSON;
- exact `app-terminal-evidence-manifest-v1` schema;
- exact selected session id;
- valid manifest self-fingerprint;
- terminal lifecycle status and valid ledger-head hash;
- valid coding-report and causal-trace availability values.

This mirrors the current validation boundary of the existing M43 browser manifest download without replacing that standalone action.

## Signature validation and exact correlation

Only after the manifest passes does M54 fetch the M53 signature with the same bearer/no-store/credentials/abort boundary.

Before either file download is initiated, the browser requires:

- HTTP success;
- exact JSON UTF-8 content type;
- exact frozen signature filename;
- decimal safe-integer Content-Length and exact body byte-count equality;
- response manifest-SHA header exactly equal to the SHA already observed from the manifest bytes;
- canonical `sha256:<64 lowercase hex>` signature-key header;
- exact `ed25519` algorithm header;
- fatal UTF-8 decode and valid JSON;
- exactly the frozen M52 envelope fields `algorithm`, `key_fingerprint`, `manifest_sha256`, `schema_version`, `signature`;
- exact `app-evidence-signature-v1` schema;
- exact `ed25519` body algorithm;
- body key fingerprint exactly equal to the response key header;
- body manifest SHA exactly equal to the already observed manifest SHA;
- canonical base64url-without-padding text for exactly one 64-byte Ed25519 signature;
- raw response text exactly equal to the frozen M52 sorted compact envelope serialization plus one terminal newline.

For a 64-byte value, the first 85 base64url characters encode 510 data bits. The last character therefore carries only 2 data bits followed by 4 required zero pad bits. The only legal final unpadded base64url characters are `A`, `Q`, `g`, or `w`, so the final M54 pattern is:

`^[A-Za-z0-9_-]{85}[AQgw]$`

The raw-envelope equality check rejects duplicate JSON keys, reordering, whitespace changes, missing terminal newline, and other non-M52 serializations even if ordinary `JSON.parse()` would otherwise accept them.

If M53 independently regenerates different manifest bytes between the two requests, correlation fails visibly and **neither download is initiated**.

M54 does not fetch or infer a public key and does not call `crypto.subtle.verify`. The key fingerprint is an identifier, not a trust decision.

## Download and status boundary

Only after both response bodies pass all checks does M54 initiate downloads using the exact response bytes and fixed names:

- `session-evidence-manifest.json`
- `session-evidence-manifest.sig.json`

Temporary anchors are removed and object URLs are revoked.

The browser cannot guarantee filesystem-level atomicity for two user-agent downloads. M54's claim is narrower: **it does not initiate either download until both fetched response bodies form one validated SHA-correlated pair**. Browser policy may still prompt, block, rename, or independently complete only one save.

Successful status text says only that the pair download was **initiated** and reports the correlated manifest SHA plus key-fingerprint identifier. It does not claim completed downloads, browser cryptographic verification, signer trust, or remote App Server identity.

## UI integration

M54 changes only the packaged operator UI surface:

1. `docs/MILESTONE_54_BROWSER_SIGNED_MANIFEST_PAIR_EXPORT.md`;
2. `src/harness_x/app_server/ui/index.html`;
3. new `src/harness_x/app_server/ui/signed_manifest_pair.js`;
4. one exact allowlist entry in `src/harness_x/app_server/ui_assets.py`;
5. focused `tests/test_app_server_operator_signed_manifest_pair_ui.py`.

The new script loads immediately after `/ui/evidence_manifest.js` and before lifecycle export, reload-auth, `app.js`, and bootstrap. That preserves the established auth-submit listener ordering so M54 can capture the page-memory bearer before `app.js` clears the token input.

No CSS change was required.

## Behavioral qualification

The Node behavioral harness executes the packaged M54 JavaScript and proves:

- valid manifest→signature fetch order;
- both requests use the page-memory bearer;
- no download occurs until both responses validate;
- exactly the two frozen filenames are initiated on success;
- signature response-header SHA mismatch suppresses both downloads;
- duplicate/non-canonical envelope serialization suppresses both downloads;
- `"A".repeat(85) + "E"` is rejected: `E` was admitted by a superseded 16-character terminal set but is not canonical for a 64-byte value;
- selected-session change while signature fetch is outstanding suppresses stale downloads;
- HTTP 401 suppresses downloads and clears M54-local bearer eligibility;
- unload cleanup is installed.

A separate Node `--check` regression validates packaged JavaScript syntax where Node is available.

## Authority boundary and explicit non-goals

M54 establishes only current browser-side byte correlation between a validated manifest response hashing to `H` and a canonical M53/M52 signature response whose header and envelope both name `H`.

It does **not** establish signature cryptographic validity, public-key trust, signer/operator identity, App Server remote identity, signature/completion time, server-side atomic generation, filesystem-level two-download atomicity, or semantic truth of evidence claims.

Offline cryptographic verification remains:

`harness-x verify-evidence MANIFEST --signature SIGNATURE --public-key PUBLIC_KEY ...`

with the public key obtained through an external trusted channel.

M54 does not add public-key publication, browser Ed25519 verification, PKI/certificates, trusted timestamps, trust-on-first-use, ZIP/tar/session bundles, one-file wrappers, a server-side atomic pair endpoint, signature persistence, key rotation/revocation, or network trust services.

## Source / diff audit

The last implementation/test head before final documentation-only cleanup was `5abdffd86b265b0a30905c7a6362742a989e8e86`.

Against frozen M53 it was:

- ahead;
- exact merge base frozen M53;
- 13 commits ahead;
- 0 behind;
- exactly 5 changed files;
- no backend/server/signing/verifier/session/runtime/evidence-generation files.

Subsequent movement is confined to this milestone document. The final PR freeze record must use a fresh exact frozen-M53→final-M54 compare.

## Source-audit findings

Qualification kept these findings fail-visible:

1. `JSON.parse()` plus an exact key set still allowed duplicate raw JSON keys; raw canonical-envelope equality was added.
2. A generic 86-character URL-safe signature regex was too weak. An intermediate 16-character terminal set was also too permissive. Final bit-level review correctly reduced the legal final characters for 64 bytes to `AQgw`.
3. A static test briefly banned the literal word `Ed25519`; final qualification instead forbids the actual browser trust operation (`crypto.subtle.verify`) and public-key fetch surfaces.
4. A source-order assertion compared a helper definition rather than the actual download call; it was corrected without production behavior change.
5. The exact pad-bit regression now uses a value ending in `E`, which the superseded 16-character set accepted but the correct 64-byte rule rejects.
6. A process-only freeze-record mistake wrote exact freeze metadata into this tracked document after CI #1407, necessarily moving the branch. That green run therefore remained provisional. This document was corrected to keep exact final SHA/synthetic metadata solely in the PR body, after which a fresh exact-head CI is required.

## Fail-visible qualification history

### CI #1391 — provisional failure

Head `0356583c1c3eb4b5aa0fe4f1138a431d465b1556`, run `32678618405`, job `97291212402`, synthetic `d03138b6cef5aaa98ea5cafe73d6c02da9f5d56e`.

- `1 failed, 674 passed in 125.25s`;
- help/config skipped;
- sole failure was the defective source-order assertion, not runtime fetch/download order.

### CI #1395 — provisional green

Head `b118eaa5a05eac94bbbc21537ee914f9d3534a16`, run `32678811718`, job `97291722132`, synthetic `daa9788bebe07d0d6f4391f1dd87e750c41002ba`.

- `675 passed in 118.98s`;
- help/config PASS;
- superseded by canonical signature-text hardening.

### CI #1397 — provisional green

Head `ee38dbdf0d65249e9c2e041c94bf0df72a9bcb0c`, run `32678876353`, job `97291883663`, synthetic `3ffd72cd2198a6af690aafa51080434d662eafa5`.

- `675 passed in 122.35s`;
- help/config PASS;
- superseded by explicit non-canonical signature-text behavior coverage.

### CI #1399 — provisional green

Head `d6bdc5e6522b533ae6f524b08fd0babde7edd04b`, run `32678956102`, job `97292091380`, synthetic `d3ec7f983abe72c19b99d0efe0cf0f5e5a166a05`.

- `675 passed in 113.75s`;
- help/config PASS;
- superseded by documentation and later pad-bit review.

### CI #1401 — provisional green

Head `628d758a5ab817a2df24004ab7766b42e85112ca`, run `32679213651`, job `97292785839`, synthetic `149f427ec803972e48e59cfa18c6eb4c5c769c15`.

- `675 passed in 95.68s`;
- help/config PASS;
- superseded by bit-level review proving the 16-character terminal set too permissive.

### CI #1407 — corrected-source green, then superseded by documentation movement

Head `72846287bd5fe0732572d3f0b6e3f3c5291b45a5`, run `32679431002`, job `97293368819`, synthetic `88d30d8a1ab0643b8a8278b45f41489c4c0df4aa`.

- exact checkout: `Merge 72846287bd5fe0732572d3f0b6e3f3c5291b45a5 into cc0ba28199005144906dda39837d1ca7828f1da3`;
- Ubuntu 24.04.4 LTS;
- Python 3.12.14;
- Actions Node 24;
- `cryptography 46.0.7`;
- `675 passed in 99.68s`;
- `harness-x --help` PASS;
- config PASS: `valid: system_version=0.1.0-alpha.0`.

This run proved the corrected source, but it is not the freeze gate because the tracked freeze-record edit moved the branch afterward.

## Final freeze gate

The final head must now remain source/document fixed and pass one fresh pull-request Linux CI with:

- exact frozen-M53 merge base and zero behind;
- exactly the same five-file authority boundary;
- full pytest success;
- `harness-x --help` success;
- default config validation success;
- no submitted reviews or actionable review threads.

The exact final head, synthetic merge, final compare totals, and final CI identifiers are recorded only in PR #61 after that run, so recording them cannot move the branch again.
