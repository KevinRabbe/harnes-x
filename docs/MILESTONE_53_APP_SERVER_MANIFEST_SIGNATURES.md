# Milestone 53 — App Server Manifest Signatures

M53 is stacked directly on frozen M52 and connects M52's detached Ed25519 format to the authenticated local App Server without widening the underlying evidence authority.

M52 can sign a downloaded manifest offline, but the App Server itself does not yet expose a detached signature over the exact manifest bytes it generates. M53 adds an **optional configured signing key at App Server startup** and one authenticated terminal-only signature-download route. The route regenerates the same deterministic M43 manifest from authoritative terminal session state and signs the exact rendered manifest bytes using the already-defined M52 envelope.

The authority claim remains deliberately narrow: a successful M53 response proves that the App Server process handling that request possessed the configured private key and signed those exact manifest bytes. It does not establish who owns the key, when the session completed, trusted time, PKI identity, transparency, remote trust, or hardware-backed key custody.

## Scope

M53 adds:

- optional `harness-x-app-server --evidence-signing-private-key PATH` startup configuration;
- one new App Server layer over frozen M51/M50 transport;
- authenticated terminal-only `GET /v1/sessions/{session_id}/evidence/signature`;
- deterministic `session-evidence-manifest.sig.json` response using M52 `app-evidence-signature-v1`;
- focused startup/key/HTTP/determinism/compatibility tests.

M53 deliberately does **not** add a browser download button, public-key publication endpoint, key registry, durable signature storage, automatic signing at session completion, trusted timestamps, certificate chains, or remote verification.

No session creation/cancellation/retry/resume semantics, lifecycle authority, report/trace/snapshot generation, offline verifier semantics, model/tool execution, memory, budget, controller, or control policy change in M53.

## Startup signing-key boundary

`harness-x-app-server` gains optional:

`--evidence-signing-private-key PATH`

When omitted:

- App Server startup and every pre-M53 route behave as before;
- no Ed25519 key is loaded;
- no M52 cryptographic primitive is invoked during startup;
- the signature route authenticates normally and then returns a structured not-configured response rather than creating a key or silently using another source.

When supplied:

- the file is loaded exactly once during server construction through M52's bounded regular-file/no-follow private-key boundary;
- the existing independent 16 KiB key limit applies;
- only unencrypted Ed25519 PKCS8 PEM is accepted;
- symlink/nonregular/oversized/malformed/non-Ed25519 key inputs fail App Server startup visibly;
- the corresponding M52 `sha256:<raw-public-key-sha256>` fingerprint is derived once;
- the private-key object and fingerprint remain process memory only;
- the private-key path, raw key bytes, and PEM are never returned by HTTP, written into sessions/evidence, or printed by normal server startup output.

The signing option therefore requires the M52 `evidence-signing` optional dependency. An operator who requests signing without that dependency receives a visible startup error; an unsigned deployment does not require or import the cryptographic primitive.

M53 does not add key hot reload or runtime key replacement. Restart with a different explicitly selected key is the only key-change mechanism in scope.

## Signature route

Exact route:

`GET /v1/sessions/{session_id}/evidence/signature`

Rules:

- available only on the local operator App Server layer;
- bearer authentication is required before configuration/session details are returned;
- no query parameters;
- no caller-selected path, filename, algorithm, key id, manifest source, session range, or artifact source;
- unknown session remains a structured 404;
- running/nonterminal session returns structured 409;
- durable lifecycle/manifest contradiction returns structured 409 evidence corruption;
- when signing is not configured, authenticated requests return structured 404 `evidence_signature_not_configured`;
- extra path components fall through to inherited routing/404 behavior.

For a terminal session with signing configured, the handler must:

1. fetch the authoritative current session snapshot and durable events through the same service/store calls used by M43 manifest export;
2. call the frozen M43 `build_terminal_evidence_manifest()` and `render_terminal_evidence_manifest()` path;
3. sign **exactly `rendered_manifest.payload`**, not a reparsed/reconstructed variant;
4. construct the exact M52 `app-evidence-signature-v1` envelope with the configured key fingerprint and `rendered_manifest.source_sha256`;
5. serialize the envelope using the same deterministic M52 sorted-key compact-JSON-plus-one-newline representation;
6. return those bytes directly without writing a server-side signature file.

The generated signature must be byte-for-byte identical to running frozen M52 `sign-evidence` over a separately downloaded `/evidence/manifest` response using the same private key.

Because Ed25519 is deterministic and the M43 manifest is deterministic, repeated signature GETs for unchanged terminal state and the same configured key return byte-identical bodies.

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

No private key material, key path, signing timestamp, process identifier, hostname, or operator identity is included in headers/body.

## M52 compatibility boundary

M53 reuses M52's exact envelope semantics rather than defining a second signature format.

A downloaded M53 signature must pass frozen M52:

`harness-x verify-evidence MANIFEST --signature SIGNATURE --public-key PUBLIC_KEY ...`

when `MANIFEST` is the exact M43 manifest bytes corresponding to the signed response and the supplied public key matches the configured private key.

M53 does not change M52 key generation, offline signing, envelope parsing, signature verification, unsigned verification output, optional dependency boundary, or CLI authority.

## App Server layering

M53 uses a new `LocalOperatorHTTPServer` subclass layered over frozen M51. It intercepts only `/evidence/signature` and delegates every other GET/POST path unchanged to the frozen M51 stack.

The public App Server package and `harness-x-app-server` CLI route to the M53 subclass. The constructor gains only one optional signing-key path/config value. Existing callers that omit it retain the pre-M53 behavior.

The M43 `/evidence/manifest` endpoint itself remains byte-for-byte behaviorally unchanged. M53 does not alter its response headers or body.

## Security and authority boundary

M53 improves evidence-origin usability only relative to an externally trusted public key: it makes the configured App Server process perform the M52 signing operation over the exact manifest it derives from current terminal state.

It does **not** prove:

- the human/operator identity behind the key;
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
- signing lifecycle/report/trace/snapshot files individually;
- key rotation/revocation registry;
- multiple simultaneous keys;
- passphrase/keychain/KMS integration;
- trusted timestamping;
- network/remote verification;
- ZIP/session bundles.

## Deterministic acceptance

Before freeze, M53 must prove:

- exact frozen M52 base `0b1e826fbca3f24498f56857ec377a0a959e7469`;
- this scope document is the first M53 commit;
- frozen M52 PR #59 remains unchanged, draft/open/unmerged;
- unsigned App Server startup does not load M52 cryptographic primitives and preserves inherited routes;
- configured startup accepts only bounded/no-follow unencrypted Ed25519 private PEM and derives the M52 fingerprint;
- malformed/non-Ed25519/symlink/oversized key input fails startup visibly;
- private-key path/material is absent from normal HTTP/startup output;
- signature route requires bearer auth, rejects query parameters, is terminal-only, and distinguishes not-configured/unknown-session/evidence-corruption conditions;
- route signs the exact frozen M43 rendered manifest bytes;
- response body is deterministic and byte-identical to frozen M52 offline signing of the corresponding downloaded manifest with the same key;
- response headers exactly expose manifest SHA, public-key fingerprint identifier, algorithm, fixed filename, and no-store safety headers;
- downloaded signature verifies with frozen M52 offline verification and the matching public key;
- `/evidence/manifest` and every non-M53 route remain behaviorally inherited;
- no browser/session/runtime/evidence-generation/verifier/model/tool/memory/budget/controller/control authority changes;
- exact M52→M53 diff remains narrow and source-audited;
- exact-head Linux CI passes including installed `harness-x --help` and `harness-x validate-config configs/default.yaml`.
