# Milestone 53 — App Server Manifest Signatures

M53 is stacked directly on frozen M52 and connects M52's detached Ed25519 format to the authenticated local App Server without widening the underlying evidence authority.

M52 can sign a downloaded manifest offline, but the App Server itself does not expose a detached signature over the exact manifest bytes it generates. M53 adds an **optional configured signing key at App Server startup** and one authenticated terminal-only signature-download route. The route regenerates the same deterministic M43 manifest from authoritative terminal session state and signs the exact rendered manifest bytes using the already-frozen M52 envelope.

The authority claim remains deliberately narrow: a successful M53 response proves only that the App Server process handling that request possessed the configured private key and signed those exact manifest bytes. It does not establish who owns the key, when the session completed, trusted time, PKI identity, transparency, remote trust, or hardware-backed key custody.

## Frozen stack contract

M53 is based exactly on frozen M52 head:

`0b1e826fbca3f24498f56857ec377a0a959e7469`

This document was the first M53 commit:

`8394d0b19463b4e997bc48a848788b0add071b50`

PR #60 targets the frozen M52 branch and must remain draft/open/unmerged until exact-head qualification and freeze metadata are complete. No merge is authorized.

## Implemented scope

M53 adds:

- optional `harness-x-app-server --evidence-signing-private-key PATH` startup configuration;
- one new App Server layer over frozen M51/M50 transport;
- authenticated terminal-only `GET /v1/sessions/{session_id}/evidence/signature`;
- deterministic `session-evidence-manifest.sig.json` response using frozen M52 `app-evidence-signature-v1`;
- focused startup/key/HTTP/determinism/M52-compatibility tests.

M53 deliberately does **not** add a browser download button, public-key publication endpoint, key registry, durable signature storage, automatic signing at session completion, trusted timestamps, certificate chains, or remote verification.

No session creation/cancellation/retry/resume semantics, lifecycle authority, report/trace/snapshot generation, offline verifier semantics, model/tool execution, memory, budget, controller, or control policy change in M53.

## Startup signing-key boundary

`harness-x-app-server` gains optional:

`--evidence-signing-private-key PATH`

When omitted:

- App Server startup and every pre-M53 route remain inherited;
- no Ed25519 key is loaded;
- the M53 signer loader is not invoked;
- the signature route authenticates normally and then returns structured `404 evidence_signature_not_configured`;
- normal startup JSON retains the existing `app-server-start-v1` fields only.

When supplied:

- the key is loaded exactly once during M53 server construction, before inherited HTTP transport construction;
- M52's bounded regular-file/no-follow private-key boundary is reused unchanged;
- the independent 16 KiB key limit applies;
- only unencrypted Ed25519 PKCS8 PEM is accepted;
- leaf symlinks, nonregular/oversized inputs, malformed PEM, and non-Ed25519 private keys fail startup visibly;
- the corresponding M52 `sha256:<raw-public-key-sha256>` fingerprint is derived once;
- the private-key object and fingerprint remain process memory only;
- the private-key path, raw key bytes, and PEM are absent from normal startup JSON and HTTP responses.

The underlying frozen M44 file reader raises its established parent `PortableEvidenceVerificationError` for path/size boundary failures, while malformed/non-Ed25519 key interpretation raises M52 `EvidenceSigningError`. M53 preserves that error hierarchy rather than wrapping it into a new type.

Signing requires the M52 `evidence-signing` optional dependency. If an operator requests signing without that dependency, startup fails visibly. Unsigned operation remains import-compatible with the base dependency set because `cryptography` primitives are loaded lazily only when signing is requested.

M53 does not add key hot reload or runtime key replacement. Restart with another explicitly selected key is the only key-change mechanism in scope.

## Signature route

Exact route:

`GET /v1/sessions/{session_id}/evidence/signature`

Rules:

- available only on the local operator App Server layer;
- bearer authentication is required before signing-configuration or session details are disclosed;
- no query parameters;
- no caller-selected path, filename, algorithm, key id, manifest source, session range, or artifact source;
- configured server + unknown session returns structured 404 `unknown_session`;
- running/nonterminal session returns structured 409 `evidence_signature_not_terminal`;
- durable lifecycle/manifest contradiction returns structured 409 `evidence_corruption`;
- when signing is not configured, an authenticated request returns structured 404 `evidence_signature_not_configured`;
- extra path components fall through to inherited 404 behavior.

For a terminal session with signing configured, the handler:

1. fetches the authoritative current snapshot and durable events through the same service/store calls used by M43 manifest export;
2. calls frozen M43 `build_terminal_evidence_manifest()` and `render_terminal_evidence_manifest()`;
3. signs **exactly `rendered_manifest.payload`**, never a reparsed/reconstructed JSON variant;
4. pins the signer input to `rendered_manifest.source_sha256`;
5. constructs the exact frozen M52 `app-evidence-signature-v1` envelope;
6. serializes with the same frozen M52 sorted-key compact-JSON-plus-one-newline encoding;
7. returns those bytes directly without writing a server-side signature file.

The generated signature is regression-tested byte-for-byte against frozen M52 `sign_evidence_manifest()` over a separately downloaded `/evidence/manifest` response using the same private key. The downloaded server signature is also passed through frozen M52 `verify_portable_evidence_with_signature()` with the corresponding public key.

Because Ed25519 and the M43 manifest renderer are deterministic, repeated signature GETs for unchanged terminal evidence and the same configured key return byte-identical bodies.

## Response contract

Successful response:

- `Content-Type: application/json; charset=utf-8`
- `Content-Disposition: attachment; filename="session-evidence-manifest.sig.json"`
- exact `Content-Length`
- `Cache-Control: no-store`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `X-Harness-X-Evidence-Manifest-SHA256: <exact manifest sha256>`
- `X-Harness-X-Evidence-Signature-Key: sha256:<64 lowercase hex>`
- `X-Harness-X-Evidence-Signature-Algorithm: ed25519`
- `Connection: close`

No private key material, key path, signing timestamp, process identifier, hostname, or operator identity is included in the signature response.

## M52 compatibility boundary

M53 reuses M52's exact envelope and signing semantics rather than defining a second format.

A downloaded M53 signature passes frozen M52:

`harness-x verify-evidence MANIFEST --signature SIGNATURE --public-key PUBLIC_KEY ...`

when `MANIFEST` contains the exact corresponding M43 manifest bytes and the supplied public key matches the configured private key.

M53 does not change M52 key generation, offline signing, envelope parsing, signature verification, unsigned verification output, optional dependency boundary, or CLI authority.

## App Server layering

M53 uses a new `LocalOperatorHTTPServer` subclass layered over frozen M51. It intercepts only `/evidence/signature` and delegates every other GET/POST path unchanged to the frozen operator-server stack.

The public App Server package and `harness-x-app-server` CLI route to the M53 subclass. The constructor gains one optional signing-key path. Existing callers that omit it retain inherited behavior.

The M43 `/evidence/manifest` endpoint itself is unchanged by M53.

## Separate-request / atomicity limitation

M53 does **not** create an atomic manifest+signature bundle or persist a terminal signature at completion time. `/evidence/manifest` and `/evidence/signature` are separate authenticated requests, and the signature route independently regenerates the current deterministic M43 manifest before signing it.

Therefore a client must correlate the signature envelope/header `manifest_sha256` with the exact manifest bytes it intends to verify. If mutable report/trace source bytes change between separate requests, the later signature can legitimately correspond to a newly regenerated manifest rather than an earlier downloaded one. Frozen M52 verification fails that mismatch visibly.

M53 makes no atomic-pair, immutable-snapshot, or historical signing-time claim.

## Security and authority boundary

M53 improves evidence-origin usability only relative to an externally trusted public key: it makes the configured App Server process perform the M52 signing operation over the exact manifest it derives from current terminal evidence.

It does **not** prove:

- human/operator identity behind the key;
- that the key belongs uniquely to this Harness X installation;
- session-completion time or signature time;
- that the key was uncompromised;
- remote machine identity;
- certificate/PKI trust;
- transparency-log inclusion;
- remote attestation or hardware custody;
- semantic truth of task/verifier/report/trace/lifecycle claims.

Anyone holding the configured private key can produce indistinguishable valid M52 signatures over arbitrary self-consistent manifest bytes. Public-key trust and private-key custody remain external operator responsibilities.

## Non-goals

M53 does not add:

- browser signature download;
- public-key download/discovery;
- automatic signature persistence;
- atomic manifest/signature bundle export;
- signing lifecycle/report/trace/snapshot files individually;
- key rotation/revocation registry;
- multiple simultaneous keys;
- passphrase/keychain/KMS integration;
- trusted timestamping;
- network/remote verification;
- ZIP/session bundles.

## Source-complete diff audit

Immediately before this final contract update, exact frozen M52 → source-complete M53 candidate `8738b3034865f8a79f947dc73e716bc92b3996ec` was:

- status: ahead
- exact merge base: frozen M52 `0b1e826fbca3f24498f56857ec377a0a959e7469`
- commits ahead: 13
- commits behind: 0
- changed files: 8
- additions: 911
- deletions: 4

Changed source is confined to:

1. this M53 scope/contract document;
2. one-line public operator-server import substitution in `src/harness_x/app_server/__init__.py`;
3. optional startup-key wiring in `src/harness_x/app_server/cli.py`;
4. new `src/harness_x/app_server/evidence_signature.py`;
5. new `src/harness_x/app_server/signed_evidence_operator_http_server.py`;
6. three focused M53 test files.

No App Server store/service/protocol/session/runtime, M43 manifest builder/renderer, M44–M52 verifier/signing implementation, browser UI, report/trace/snapshot generation, coding runtime/verifier, model/tool, memory, budget, controller, or control implementation changed.

The final exact M52→M53 diff is re-audited after this documentation-only commit and recorded in the PR freeze record.

## Source / test audit findings

Qualification deliberately remained fail-visible and found only test-harness defects after the initial implementation:

1. the first HTTP fixture attempted to create nested workspace/output directories without `parents=True`, so three tests failed before any route/signing logic executed;
2. path/size tests initially over-specified the M52 subclass instead of preserving the frozen M44 parent exception boundary;
3. an unsigned-construction test called inherited `server.close()` without ever starting `serve_forever()`, which blocks inside `HTTPServer.shutdown()`; final test cleanup closes the unstarted socket directly with `server.httpd.server_close()`;
4. the oversized-key test initially expected generic wording instead of frozen M44's exact `exceeds 16384 byte limit` message;
5. the fake startup transport initially used invented token/info filenames; final test aligns with frozen real transport names `access-token` and `server-info.json` so the startup-schema compatibility assertion is meaningful.

None of these findings required a production implementation change after the M53 route/startup implementation was complete.

No submitted PR reviews or inline review threads were present during the source-complete audit. Frozen M52 PR #59 was independently rechecked and remained draft/open/unmerged on exact frozen head `0b1e826fbca3f24498f56857ec377a0a959e7469`.

## Fail-visible qualification history

### CI #1362 — provisional failure

Head `aa9465e87c062f95ff2b31883ae0115f4a72420a`

- run id: `32673772739`
- job id: `97278497201`
- synthetic merge: `ef66777e4467adc031391bd7e71a697b510e9ad1`
- Ubuntu 24.04.4 LTS / Python 3.12.14 / Actions Node 24
- pytest: `3 failed, 658 passed in 90.09s`
- help/config skipped
- all three failures were nested-directory fixture `FileNotFoundError`s before M53 route/signing execution.

### CI #1364 — provisional failure

Head `66e7c4a6e6dae73f3478720d054250adf793edb8`

- run id: `32674215447`
- job id: `97279575187`
- synthetic merge: `f7d345d250561f9985a654a4196ce52c49125846`
- Ubuntu 24.04.4 LTS / Python 3.12.14 / Actions Node 24
- pytest: `3 failed, 662 passed in 116.03s`
- help/config skipped
- same pre-fix nested-directory fixture defect.

### CI #1366 / #1368 / #1370 — superseded hanging qualification runs

These runs were launched on intermediate heads while the unsigned-construction boundary test still called inherited `server.close()` without a running serve loop. Source audit identified that test-only deadlock and replaced cleanup with direct `server.httpd.server_close()`.

At the final-contract audit these historical runs still reported `in_progress` and are not freeze evidence:

- #1366 — head `9594c2fdbb1f88b5b2e5eba0366678c30bab4ef5`, run `32674267358`, observed job `97279705058`;
- #1368 — head `e532dafe28d89d6769faa2133dc3e1eb14df722c`, run `32674288741`;
- #1370 — head `c5535144ebfe81b94177e17aba9718c90a70d1ce`, run `32674391113`, observed job `97280005327`.

### CI #1372 — provisional failure

Head `32e87e33b6d40508b74fc4c6d6235539936aa165`

- run id: `32674652390`
- job id: `97280647183`
- synthetic merge: `a869d115bbb63e158cea01df6c6daae6680a7226`
- Ubuntu 24.04.4 LTS / Python 3.12.14 / Actions Node 24
- pytest: `1 failed, 669 passed in 118.75s`
- help/config skipped
- all M53 HTTP/signing/startup behavior passed; the only failure was test regex text for the correctly rejected >16 KiB key.

### CI #1374 — provisional failure

Head `77fbf8cd19db80f59d6406a43a609866bde8e992`

- run id: `32674757234`
- job id: `97280910096`
- synthetic merge: `2f263fc036024cfcb98eaf50373f1de74fddafa2`
- Ubuntu 24.04.4 LTS / Python 3.12.14 / Actions Node 24
- pytest: `1 failed, 669 passed in 116.44s`
- help/config skipped
- same already-identified oversized-key assertion wording; this run raced the subsequent fix.

### CI #1376 — source-complete green candidate

Head `8738b3034865f8a79f947dc73e716bc92b3996ec`

- run id: `32674801494`
- job id: `97281015308`
- synthetic merge: `82731b1b7f670c560d6c344a92f0354c81be01c3`
- Ubuntu 24.04.4 LTS
- Python 3.12.14
- GitHub Actions Node 24
- `cryptography 46.0.7`
- pytest: `670 passed in 101.91s (0:01:41)`
- `harness-x --help`: PASS
- `harness-x validate-config configs/default.yaml`: PASS
- config output: `valid: system_version=0.1.0-alpha.0`

CI #1376 proves the source-complete implementation but is intentionally not the freeze gate because this final contract update moves the branch head. Freeze requires a fresh exact-head Linux CI after this documentation-only commit.

## Freeze acceptance

M53 is freeze-eligible only after the final documentation head proves all of the following:

- exact frozen M52 base and merge base;
- unsigned startup avoids signer loading and preserves startup JSON fields;
- configured startup uses bounded/no-follow Ed25519 private-key loading and fails visibly on unsafe/invalid keys;
- no private-key path/material in normal startup or HTTP output;
- signature route requires bearer auth, rejects query parameters, is terminal-only, and maps durable evidence corruption visibly;
- exact frozen M43 rendered bytes are signed;
- deterministic response bytes and exact headers;
- byte identity with frozen M52 offline signing using the same key;
- successful frozen M52 offline verification with matching public key;
- inherited non-M53 routes and M43 manifest behavior remain untouched by the M53 diff;
- explicit separate-request/non-atomic pairing limitation remains documented;
- exact final diff remains narrow;
- no reviews/threads requiring action;
- exact-head Linux CI passes pytest, installed `harness-x --help`, and `harness-x validate-config configs/default.yaml`.
