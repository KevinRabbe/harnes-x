# Milestone 54 — Browser Signed-Manifest Pair Export

M54 is stacked directly on frozen M53 and closes the narrow operator-UI usability gap left by M53: the server can now produce a detached Ed25519 signature over its current terminal evidence manifest, but the browser offers no safe way to obtain a manifest/signature pair while checking that both downloads refer to the same exact manifest bytes.

M54 adds **one authenticated operator-UI action that fetches the existing M43 manifest and M53 detached signature, validates their byte/header/schema correlation, and only then initiates downloads for the two frozen portable files**.

M54 is not browser-side signature trust. The UI has no trusted public key and does not claim that the displayed fingerprint identifies an operator, installation, or server. Cryptographic signature verification remains the frozen M52 offline verifier with an externally obtained public key.

## Stack

Exact frozen M53 base:

`cc0ba28199005144906dda39837d1ca7828f1da3`

M54 must remain stacked directly on that SHA and PR #60 must remain unchanged, draft/open/unmerged.

This scope document must be the first M54 commit.

## Scope

M54 adds one new operator action:

`Download signed manifest pair`

for the currently selected terminal session.

It uses only existing authenticated endpoints:

- `GET /v1/sessions/{session_id}/evidence/manifest`
- `GET /v1/sessions/{session_id}/evidence/signature`

M54 adds no App Server HTTP route, signing semantics, key management, evidence generation, session mutation, or offline verifier change.

The existing standalone `Download evidence manifest` action remains unchanged and available even when M53 signing is not configured.

## Browser state boundary

The M54 client follows the existing operator-download state pattern:

- bearer token retained only in page memory;
- no localStorage, cookies, IndexedDB, Cache Storage, query parameters, or URL fragments for credentials/evidence;
- one generation counter tied to selected-session identity;
- one active `AbortController` for the pair operation;
- terminal states are exactly `succeeded`, `failed`, `cancelled`;
- selection mutation cancels the active pair operation and invalidates stale async work;
- explicit lock clears the M54-local token, cancels active work, and clears status;
- an observed transition to global `Locked` does the same;
- `beforeunload` cancels active work;
- a 401 from either request clears only M54's page-memory token and fails visibly.

No session selection, lifecycle state, or server state is mutated by M54.

## Manifest validation

The pair flow fetches the manifest first using:

- `GET`;
- `Authorization: Bearer <page-memory-token>`;
- `Accept: application/json`;
- `cache: no-store`;
- `credentials: omit`;
- the operation abort signal.

Before retaining it for download, the browser requires:

- HTTP success;
- exact `Content-Type: application/json; charset=utf-8`;
- exact `Content-Disposition: attachment; filename="session-evidence-manifest.json"`;
- canonical lowercase 64-hex `X-Harness-X-Evidence-Manifest-SHA256`;
- decimal Content-Length;
- one `arrayBuffer()` read;
- exact byte-count equality with Content-Length;
- Web Crypto SHA-256 of the exact bytes equal to the response SHA header;
- fatal UTF-8 decode plus JSON parse;
- exact manifest schema `app-terminal-evidence-manifest-v1`;
- exact selected `session_id`;
- terminal lifecycle status;
- canonical manifest self-fingerprint and lifecycle ledger-head hash;
- valid report/trace availability values.

This is deliberately the same current consistency boundary as the existing M43 browser manifest download; M54 must not weaken that action.

## Signature validation and correlation

Only after the manifest passes all checks does M54 fetch the M53 signature using the same bearer/no-store/credentials/abort boundary.

Before either file is offered for download, the browser requires:

- HTTP success;
- exact `Content-Type: application/json; charset=utf-8`;
- exact `Content-Disposition: attachment; filename="session-evidence-manifest.sig.json"`;
- decimal Content-Length;
- one `arrayBuffer()` read and exact length equality;
- canonical lowercase `X-Harness-X-Evidence-Manifest-SHA256` exactly equal to the already observed manifest SHA-256;
- canonical `X-Harness-X-Evidence-Signature-Key` of `sha256:<64 lowercase hex>`;
- exact `X-Harness-X-Evidence-Signature-Algorithm: ed25519`;
- fatal UTF-8 decode plus JSON parse;
- root object with exactly the frozen M52 envelope fields;
- exact `schema_version: app-evidence-signature-v1`;
- exact `algorithm: ed25519`;
- body key fingerprint exactly equal to the response key-fingerprint header;
- body `manifest_sha256` exactly equal to the already observed manifest SHA-256;
- canonical 86-character base64url-without-padding Ed25519 signature text.

If M53 independently regenerates a different manifest between the two requests, the SHA correlation fails visibly and **neither download is initiated**.

M54 does not fetch or infer a public key and does not call Web Crypto Ed25519 verification. The key fingerprint shown in status text is an identifier supplied by the signed envelope/response, not a trust decision.

## Download boundary

Only after both response bodies pass validation does M54 create temporary object URLs and initiate downloads with fixed names:

- `session-evidence-manifest.json`
- `session-evidence-manifest.sig.json`

The exact response bytes are used for both Blobs; the UI does not reconstruct either JSON file.

Object URLs are revoked in `finally` cleanup and temporary anchors are removed after click.

The browser cannot guarantee filesystem-level atomicity for two user-agent downloads. M54's claim is narrower: **it does not initiate either download until it has validated that both fetched bodies form one SHA-correlated pair**. Browser download policy may still prompt, block, rename, or independently complete one of the two saves.

## Status disclosure

On success, status text may show:

- that a signed pair download was initiated;
- the exact manifest SHA-256;
- the M52/M53 key fingerprint.

It must not say `trusted`, `verified signer`, `authentic server`, or equivalent language implying public-key trust.

Failures remain text-only and fail-visible; no response body or server error is injected as HTML.

## UI integration

M54 adds only:

- one button and one status element in the existing lifecycle/evidence panel;
- one packaged JavaScript asset for the pair-download client;
- one exact `ui_assets.py` allowlist entry;
- focused static/Node qualification tests.

Script ordering must preserve the existing M40 bootstrap/auth-submit listener behavior. The M54 client must register its auth listener before `app.js` clears the token field and before bootstrap-triggered submit is dispatched.

No CSS change is required unless exact existing layout behavior proves insufficient.

## Authority boundary

M54 proves only browser-side **current byte correlation** between one successfully validated M43 manifest response and one M53 signature response whose envelope/header names the same manifest SHA-256.

It does not prove:

- the Ed25519 signature cryptographically verifies;
- ownership/trust of the named public-key fingerprint;
- App Server identity outside the current authenticated loopback interaction;
- atomic server-side generation of the two responses;
- trusted completion/signature time;
- durable filesystem atomicity of the two downloads;
- semantic truth of evidence contents.

Offline cryptographic verification remains:

`harness-x verify-evidence MANIFEST --signature SIGNATURE --public-key PUBLIC_KEY ...`

with the public key obtained through an external trusted channel.

## Non-goals

M54 does not add:

- public-key publication/download;
- browser Ed25519 verification;
- certificate/PKI trust;
- trusted timestamping;
- automatic browser trust-on-first-use;
- a ZIP/tar/session bundle;
- one-file wrapper format;
- server-side atomic pair endpoint;
- signature persistence;
- key rotation/revocation;
- remote access or network trust service.

## Deterministic acceptance

Before freeze, M54 must prove:

- exact frozen M53 base `cc0ba28199005144906dda39837d1ca7828f1da3`;
- this document is the first M54 commit;
- frozen M53 PR #60 remains unchanged, draft/open/unmerged;
- standalone M43 manifest download remains present/unchanged;
- new pair action is terminal/auth gated;
- manifest exact-byte length/SHA/schema/session checks;
- signature exact-byte length/header/envelope checks;
- exact manifest-SHA equality across observed manifest bytes, M53 response header, and M52 envelope body;
- mismatch prevents both download clicks;
- fixed filenames and exact response bytes saved only after both checks pass;
- selection/lock/401/unload cancellation and stale-generation rejection;
- no persistent browser credential/evidence storage;
- no public-key fetch or browser trust claim;
- no App Server/session/runtime/evidence/verifier/signing implementation changes;
- exact M53→M54 diff remains narrow;
- exact-head Linux CI passes pytest, installed `harness-x --help`, config validation, and Node syntax qualification where Node is available.
