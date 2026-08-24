# Milestone 54 — Browser Signed-Manifest Pair Export

M54 is stacked directly on frozen M53 and closes one narrow operator-UI usability gap left by M53: the local App Server can produce a detached Ed25519 signature over its current terminal evidence manifest, but the browser previously offered no fail-closed way to obtain a manifest/signature pair while checking that both responses refer to the same exact manifest bytes.

M54 adds **one authenticated operator-UI action that fetches the existing M43 manifest and M53 detached signature, validates their exact byte/header/schema correlation, and only then initiates downloads for the two frozen portable files**.

M54 is not browser-side signature trust. The UI has no trusted public key and does not claim that the displayed key fingerprint identifies an operator, installation, host, or server. Cryptographic signature verification remains the frozen M52 offline verifier with a public key obtained through an external trusted channel.

## Frozen stack

Exact frozen M53 base:

`cc0ba28199005144906dda39837d1ca7828f1da3`

First M54 commit / scope document:

`635f4a232f252568641df0ae305a54afdcde7e33`

Frozen M54 head:

`72846287bd5fe0732572d3f0b6e3f3c5291b45a5`

Final qualified synthetic merge:

`88d30d8a1ab0643b8a8278b45f41489c4c0df4aa`

M54 remains stacked directly on frozen M53. Frozen M53 PR #60 must remain unchanged, draft/open/unmerged. M54 PR #61 must remain draft/open/unmerged. No merge is authorized.

## Frozen implementation contract

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
- explicit lock clears the M54-local token, cancels active work, and clears status;
- an observed transition to global `Locked` performs the same cleanup;
- `beforeunload` cancels active work;
- HTTP 401 from either request clears only M54's page-memory token and fails visibly.

No session selection, lifecycle state, durable evidence, or server state is mutated by M54.

## Manifest request and validation

The pair flow fetches the manifest first using:

- `GET`;
- `Authorization: Bearer <page-memory-token>`;
- `Accept: application/json`;
- `cache: no-store`;
- `credentials: omit`;
- the operation abort signal.

Before retaining the response for a possible download, the browser requires:

- HTTP success;
- exact `Content-Type: application/json; charset=utf-8`;
- exact `Content-Disposition: attachment; filename="session-evidence-manifest.json"`;
- canonical lowercase 64-hex `X-Harness-X-Evidence-Manifest-SHA256`;
- decimal safe-integer `Content-Length`;
- one `arrayBuffer()` body read;
- exact body byte-count equality with Content-Length;
- Web Crypto SHA-256 of the exact body bytes equal to the response SHA header;
- fatal UTF-8 decode plus JSON parse;
- exact manifest schema `app-terminal-evidence-manifest-v1`;
- exact selected `session_id`;
- canonical manifest self-fingerprint;
- terminal lifecycle status plus canonical ledger-head hash;
- valid coding-report and causal-trace availability values.

This intentionally mirrors the current validation boundary of the existing M43 browser manifest download. M54 does not replace or weaken that standalone action.

## Signature request and exact correlation

Only after the manifest passes all checks does M54 fetch the M53 signature with the same bearer/no-store/credentials/abort boundary.

Before either file download is initiated, the browser requires:

- HTTP success;
- exact `Content-Type: application/json; charset=utf-8`;
- exact `Content-Disposition: attachment; filename="session-evidence-manifest.sig.json"`;
- decimal safe-integer Content-Length;
- one `arrayBuffer()` body read and exact length equality;
- canonical lowercase `X-Harness-X-Evidence-Manifest-SHA256` exactly equal to the SHA-256 already observed from the manifest bytes;
- canonical `X-Harness-X-Evidence-Signature-Key` of `sha256:<64 lowercase hex>`;
- exact `X-Harness-X-Evidence-Signature-Algorithm: ed25519`;
- fatal UTF-8 decode plus JSON parse;
- root object with exactly the frozen M52 fields `algorithm`, `key_fingerprint`, `manifest_sha256`, `schema_version`, `signature`;
- exact `schema_version: app-evidence-signature-v1`;
- exact `algorithm: ed25519`;
- body key fingerprint exactly equal to the response key-fingerprint header;
- body `manifest_sha256` exactly equal to the already observed manifest SHA-256;
- canonical 86-character base64url-without-padding encoding for one 64-byte Ed25519 signature;
- exact raw response text equal to the frozen M52 sorted compact envelope serialization plus one terminal newline.

For a 64-byte value, the first 85 base64url characters encode 510 data bits. The final character therefore contains only the remaining 2 data bits followed by 4 required zero pad bits. The only legal final unpadded base64url characters are:

`A`, `Q`, `g`, `w`

Accordingly, the frozen M54 signature-text pattern is:

`^[A-Za-z0-9_-]{85}[AQgw]$`

The raw-envelope equality check additionally rejects duplicate JSON object keys, reordering, inserted whitespace, omitted terminal newline, and other non-M52 serializations even if ordinary `JSON.parse()` would otherwise accept them.

If M53 independently regenerates different manifest bytes between the two requests, the response/header/body SHA correlation fails visibly and **neither download is initiated**.

M54 does not fetch or infer a public key and does not call `crypto.subtle.verify`. The key fingerprint shown in status text is an identifier carried by the M52/M53 envelope and response, not a trust decision.

## Download boundary

Only after both response bodies pass all checks does M54 create temporary object URLs and initiate downloads with fixed names:

- `session-evidence-manifest.json`
- `session-evidence-manifest.sig.json`

The exact response bytes are passed to both Blobs. The UI does not reconstruct either portable file for download.

Temporary anchors are removed after click and object URLs are revoked in `finally` cleanup.

The browser cannot guarantee filesystem-level atomicity for two user-agent downloads. M54's claim is deliberately narrower: **it does not initiate either download until it has validated that both fetched bodies form one SHA-correlated pair**. Browser policy may still prompt, block, rename, or independently complete only one of the two file saves.

## Status disclosure

Successful status text reports only:

- that the signed pair download was **initiated**;
- the exact correlated manifest SHA-256;
- the M52/M53 key fingerprint identifier.

It does not claim the downloads completed, that a signature was cryptographically verified in the browser, that the signer is trusted, or that the App Server has a remotely authenticated identity.

Failures remain text-only and fail-visible; response/server strings are never injected through HTML.

## UI integration

M54 changes only the packaged operator UI surface:

1. one `Download signed manifest pair` button in the existing lifecycle/evidence action row;
2. one `signed-manifest-pair-status` text surface;
3. one packaged `/ui/signed_manifest_pair.js` asset;
4. one exact `ui_assets.py` allowlist entry;
5. focused static/Node qualification tests.

Final script ordering places `/ui/signed_manifest_pair.js` immediately after `/ui/evidence_manifest.js` and before `/ui/lifecycle_export.js`, `reload_auth.js`, `app.js`, and `bootstrap.js`.

This preserves the established M40 submit-listener ordering: M54 captures the page-memory bearer on auth submit before `app.js` clears the token input and before bootstrap-triggered submit is dispatched.

No CSS change was required.

## Behavioral qualification

The Node behavioral harness exercises the packaged M54 asset rather than a rewritten test implementation.

It proves:

- a valid manifest followed by a matching canonical M52 signature performs exactly two authenticated fetches in manifest→signature order;
- no download click occurs until both fetches and all validations have completed;
- success initiates exactly the frozen manifest and signature filenames;
- success status exposes the correlated SHA and key identifier without a trust claim;
- signature response-header SHA mismatch suppresses both downloads and fails visibly;
- duplicate/non-canonical envelope serialization suppresses both downloads;
- an otherwise URL-safe 86-character signature ending in `E`—which the earlier 16-character terminal set incorrectly admitted—now fails the exact 64-byte pad-bit rule and suppresses both downloads;
- selected-session change while the signature request is outstanding suppresses stale downloads;
- HTTP 401 suppresses downloads and clears M54-local bearer eligibility;
- unload cleanup is installed.

A separate Node `--check` regression validates the packaged JavaScript syntax when Node is available.

## Authority boundary

M54 establishes only browser-side **current byte correlation** between:

1. one successfully validated M43 manifest response whose exact bytes hash to SHA `H`; and
2. one M53 signature response whose response header and canonical M52 envelope both name the same `H`.

It does not establish:

- that the Ed25519 signature cryptographically verifies;
- ownership or trust of the named public-key fingerprint;
- App Server identity outside the current authenticated loopback interaction;
- atomic server-side generation of the two responses;
- signature time or completion time;
- durable filesystem atomicity of the two browser downloads;
- semantic truth of the evidence contents.

Offline cryptographic verification remains:

`harness-x verify-evidence MANIFEST --signature SIGNATURE --public-key PUBLIC_KEY ...`

with the public key obtained through an external trusted channel.

## Explicit non-goals

M54 does not add:

- public-key publication/download;
- browser Ed25519 verification;
- certificate/PKI trust;
- trusted timestamping;
- browser trust-on-first-use;
- a ZIP/tar/session bundle;
- one-file wrapper format;
- a server-side atomic pair endpoint;
- signature persistence;
- key rotation/revocation;
- remote access or a network trust service.

## Exact frozen M53 → M54 diff

Base:

`cc0ba28199005144906dda39837d1ca7828f1da3`

Head:

`72846287bd5fe0732572d3f0b6e3f3c5291b45a5`

- status: ahead;
- exact merge base: frozen M53;
- commits ahead: 14;
- commits behind: 0;
- changed files: 5;
- additions: 1134;
- deletions: 0.

Changed files are exactly:

1. `docs/MILESTONE_54_BROWSER_SIGNED_MANIFEST_PAIR_EXPORT.md`;
2. `src/harness_x/app_server/ui/index.html`;
3. `src/harness_x/app_server/ui/signed_manifest_pair.js`;
4. `src/harness_x/app_server/ui_assets.py`;
5. `tests/test_app_server_operator_signed_manifest_pair_ui.py`.

No App Server HTTP/server/service/store/protocol/session/runtime, M43 evidence builder/renderer, M44–M54 offline verifier/signing implementation, report/trace/snapshot generation, coding runtime/verifier, model/tool, memory, budget, controller, or control implementation changed.

## Source-audit findings

Qualification deliberately kept the following findings visible:

1. The initial signature-body validation used `JSON.parse()` plus an exact key set. Duplicate keys could still survive JSON parsing. The final client requires the raw body to equal the canonical frozen M52 sorted compact serialization plus terminal newline, rejecting duplicates and alternate serialization.
2. The initial signature text check accepted any 86 URL-safe characters. It was then narrowed to a 16-character terminal set, but final bit-level review showed that a 64-byte value has only 2 data bits in the last base64url symbol. The frozen client therefore permits only `A`, `Q`, `g`, or `w` as the final character.
3. A static test initially banned the literal word `Ed25519`, overreaching beyond the actual authority boundary. The final test forbids the trust operation (`crypto.subtle.verify`) and public-key fetch surfaces instead.
4. One source-order assertion compared the helper function definition against the later signature-fetch call and failed despite correct runtime order. The final assertion pins the actual manifest-download call site.
5. The final pad-bit regression deliberately uses `"A".repeat(85) + "E"`: `E` was accepted by the superseded 16-character terminal set but is non-canonical for a 64-byte value. Both downloads must be suppressed.

No backend production change was required by these source-audit findings.

## Fail-visible qualification history

### CI #1391 — provisional failure

Head `0356583c1c3eb4b5aa0fe4f1138a431d465b1556`.

- run id: `32678618405`;
- job id: `97291212402`;
- synthetic merge: `d03138b6cef5aaa98ea5cafe73d6c02da9f5d56e`;
- Ubuntu 24.04.4 LTS;
- Python 3.12.14;
- Actions Node 24;
- pytest: `1 failed, 674 passed in 125.25s`;
- help/config skipped after pytest failure.

The sole failure was the source-order test comparing the first occurrence of the download helper name—its function definition—against the later signature-fetch call. The runtime implementation order was not the defect.

### CI #1395 — provisional green

Head `b118eaa5a05eac94bbbc21537ee914f9d3534a16`.

- run id: `32678811718`;
- job id: `97291722132`;
- synthetic merge: `daa9788bebe07d0d6f4391f1dd87e750c41002ba`;
- Ubuntu 24.04.4 LTS;
- Python 3.12.14;
- Actions Node 24;
- pytest: `675 passed in 118.98s (0:01:58)`;
- `harness-x --help`: PASS;
- config validation: PASS, `valid: system_version=0.1.0-alpha.0`.

This run was superseded by canonical signature-text hardening.

### CI #1397 — provisional green

Head `ee38dbdf0d65249e9c2e041c94bf0df72a9bcb0c`.

- run id: `32678876353`;
- job id: `97291883663`;
- synthetic merge: `3ffd72cd2198a6af690aafa51080434d662eafa5`;
- Ubuntu 24.04.4 LTS;
- Python 3.12.14;
- Actions Node 24;
- pytest: `675 passed in 122.35s (0:02:02)`;
- `harness-x --help`: PASS;
- config validation: PASS.

This run was superseded by a dedicated non-canonical signature-text behavioral regression.

### CI #1399 — green provisional

Head `d6bdc5e6522b533ae6f524b08fd0babde7edd04b`.

- run number: `1399`;
- run id: `32678956102`;
- job id: `97292091380`;
- synthetic merge: `d3ec7f983abe72c19b99d0efe0cf0f5e5a166a05`;
- exact checkout: `Merge d6bdc5e6522b533ae6f524b08fd0babde7edd04b into cc0ba28199005144906dda39837d1ca7828f1da3`;
- Ubuntu 24.04.4 LTS;
- Python 3.12.14;
- Actions Node 24;
- `cryptography 46.0.7`;
- pytest: `675 passed in 113.75s (0:01:53)`;
- `harness-x --help`: PASS;
- `harness-x validate-config configs/default.yaml`: PASS;
- config output: `valid: system_version=0.1.0-alpha.0`.

This run was superseded first by final contract documentation and then by the final pad-bit source audit.

### CI #1401 — green but superseded by source audit

Head `628d758a5ab817a2df24004ab7766b42e85112ca`.

- run number: `1401`;
- run id: `32679213651`;
- job id: `97292785839`;
- synthetic merge: `149f427ec803972e48e59cfa18c6eb4c5c769c15`;
- exact checkout: `Merge 628d758a5ab817a2df24004ab7766b42e85112ca into cc0ba28199005144906dda39837d1ca7828f1da3`;
- Ubuntu 24.04.4 LTS;
- Python 3.12.14;
- Actions Node 24;
- `cryptography 46.0.7`;
- pytest: `675 passed in 95.68s (0:01:35)`;
- `harness-x --help`: PASS;
- config validation: PASS, `valid: system_version=0.1.0-alpha.0`.

CI #1401 is not freeze evidence. Post-run bit-level source audit found the 16-character terminal base64url set too permissive for a 64-byte value, so source and regression tests moved again.

### FINAL exact-head qualification — CI #1407

Exact frozen M54 head:

`72846287bd5fe0732572d3f0b6e3f3c5291b45a5`

- run number: `1407`;
- run id: `32679431002`;
- job id: `97293368819`;
- workflow conclusion: success;
- synthetic merge: `88d30d8a1ab0643b8a8278b45f41489c4c0df4aa`;
- exact checkout: `Merge 72846287bd5fe0732572d3f0b6e3f3c5291b45a5 into cc0ba28199005144906dda39837d1ca7828f1da3`;
- Ubuntu 24.04.4 LTS;
- Python 3.12.14;
- Actions Node 24;
- `cryptography 46.0.7`;
- pytest: `675 passed in 99.68s (0:01:39)`;
- `harness-x --help`: PASS;
- `harness-x validate-config configs/default.yaml`: PASS;
- config output: `valid: system_version=0.1.0-alpha.0`.

CI #1407 is the M54 freeze gate.

## Frozen PR state

M54 PR #61 must remain **draft / open / unmerged** on exact frozen M53 base `cc0ba28199005144906dda39837d1ca7828f1da3` and exact frozen M54 head `72846287bd5fe0732572d3f0b6e3f3c5291b45a5`.

No merge was performed or authorized.
