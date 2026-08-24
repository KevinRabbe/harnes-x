# Milestone 55 — Server-Atomic Signed-Manifest Capsule Export

M55 is stacked directly on frozen M54 and closes the remaining transport/save boundary that M54 documents explicitly: the browser can validate a manifest/signature pair before initiating two downloads, but the pair still comes from two independent HTTP requests and the browser cannot make two filesystem saves atomic.

M55 adds one **server-atomic, single-response, single-save signed-manifest capsule**. The App Server renders one M43 terminal evidence manifest exactly once, signs those exact bytes through the frozen M53/M52 Ed25519 path, wraps the exact manifest bytes plus exact detached-signature-envelope bytes in one deterministic byte-preserving JSON capsule, and returns that capsule through one authenticated endpoint. The browser validates the capsule and initiates one fixed-name download.

Here **server-atomic** is deliberately narrow: manifest rendering, signature generation, capsule construction, and HTTP delivery happen in one request without a manifest/signature inter-request race. M55 does not turn the pre-existing M43 reads of snapshot/lifecycle/report/trace evidence into an operating-system or database transaction, and it does not claim that a browser-initiated file save is durable or filesystem-transactional.

M55 does not make the browser a signature-trust authority. The capsule contains no public key, certificate, identity assertion, timestamp authority, or trust-on-first-use state. Offline cryptographic verification remains the frozen M52 verifier with a public key obtained through an external trusted channel.

## Stack

Exact frozen M54 base:

`72ccc7fd7fcd32ff904d27c1a58ad5c07247365c`

M54 PR #61 must remain frozen draft/open/unmerged at that exact head.

M55 branch:

`agent/milestone-55-server-atomic-signed-manifest-capsule`

The first M55 commit is this scope/authority document. The final M55 head and synthetic merge must be recorded only in PR metadata after exact-head qualification so the tracked document does not self-move the freeze gate.

## New authenticated endpoint

M55 adds exactly:

`GET /v1/sessions/{session_id}/evidence/signed-manifest-capsule`

The route:

- inherits the literal-loopback Host boundary;
- requires the existing persistent bearer before configuration/session disclosure;
- accepts no query parameters;
- accepts no caller-selected path, filename, algorithm, key, source, serialization, artifact selector, or compression option;
- is terminal-only;
- is available only when the existing M53 evidence-signing private key is configured;
- mutates no App Server session, lifecycle, evidence, signing, runtime, or durable state.

Structured failure names remain narrow and fail-visible:

- `evidence_signature_not_configured` when no M53 signer is configured;
- `unknown_session` for missing sessions after authentication;
- `evidence_capsule_not_terminal` for nonterminal sessions;
- `evidence_corruption` for lifecycle/report/trace/manifest inconsistency.

## Server-atomic construction

For one successful request M55 performs exactly one authoritative terminal evidence projection:

1. read the current authoritative session snapshot;
2. read its durable lifecycle events;
3. call the frozen M43 terminal evidence-manifest builder;
4. render the manifest once using the frozen M43 renderer;
5. pass those exact rendered manifest bytes to the already-loaded frozen M53 signer;
6. retain the exact frozen M52 detached-signature envelope bytes returned by that signer;
7. build one deterministic capsule from those two already-retained byte strings.

The manifest is not regenerated between signing and capsule construction. The signature therefore names and covers the exact manifest bytes embedded in the same response. Before wrapping, the M55 renderer also revalidates that the retained signature bytes are the canonical frozen M52 envelope and that its algorithm, key fingerprint, and manifest SHA agree with the retained signature metadata and exact manifest bytes.

M55 adds no signature persistence and no second signing key. It reuses the one M53 signer loaded at process startup.

## Capsule schema

Fixed schema:

`app-signed-manifest-capsule-v1`

Exact fields:

- `schema_version`
- `algorithm`
- `key_fingerprint`
- `manifest_sha256`
- `manifest_payload`
- `signature_payload`

`manifest_payload` is canonical base64url-without-padding encoding of the exact frozen M43 manifest response bytes.

`signature_payload` is canonical base64url-without-padding encoding of the exact frozen M52 signature-envelope bytes, including its required terminal newline.

The capsule duplicates only correlation metadata already present in the M53 response/envelope so clients can fail closed before extracting either payload. The capsule metadata must agree exactly with the decoded signature envelope and decoded manifest SHA.

The capsule itself is deterministic sorted-key compact UTF-8 JSON plus one trailing newline. No generated-at time, random nonce, path, hostname, user, process identifier, or ambient metadata is added.

## Response contract

Fixed filename:

`session-evidence-signed-manifest-pair.json`

Successful response uses:

- `Content-Type: application/json; charset=utf-8`
- fixed `Content-Disposition` filename;
- exact `Content-Length`;
- `Cache-Control: no-store`;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: no-referrer`;
- `X-Harness-X-Evidence-Capsule-SHA256` over the exact capsule bytes;
- `X-Harness-X-Evidence-Manifest-SHA256` over the exact decoded manifest bytes;
- `X-Harness-X-Evidence-Signature-Key` using the existing M52/M53 key fingerprint identifier;
- `X-Harness-X-Evidence-Signature-Algorithm: ed25519`;
- connection close.

The capsule SHA is an integrity/correlation value only. It is not a signature or origin-authentication claim.

## Browser action

M55 adds a new terminal-only operator action:

`Download signed manifest capsule`

The existing M54 `Download signed manifest pair` action remains available and unchanged so M55 does not silently redefine the frozen two-file behavior.

The M55 browser client:

- captures the existing page-memory bearer through the established auth-listener ordering;
- performs one authenticated GET with `cache: no-store`, `credentials: omit`, and an `AbortController`;
- validates fixed content type and filename;
- validates decimal safe-integer `Content-Length` and exact body byte count;
- computes Web Crypto SHA-256 over the exact capsule body and matches the capsule response header;
- uses fatal UTF-8 decode and strict JSON shape checks;
- requires the exact capsule field set and `app-signed-manifest-capsule-v1` schema;
- requires canonical lowercase manifest SHA, canonical M52 key fingerprint, and exact `ed25519` algorithm;
- base64url-decodes both payload fields without accepting padding or noncanonical forms;
- recomputes SHA-256 over the exact decoded manifest bytes and matches both capsule metadata and response header;
- validates the decoded manifest at the same selected-session / terminal / schema / component-availability boundary as M54;
- parses the decoded signature bytes and requires the exact frozen M52 envelope keys, schema, algorithm, key fingerprint, manifest SHA, canonical 64-byte Ed25519 signature text, canonical envelope serialization, and terminal newline;
- requires all decoded/envelope/capsule/response correlation values to agree;
- initiates exactly one fixed-name browser download only after every check passes;
- uses no `crypto.subtle.verify` and fetches no public key;
- clears/cancels on selection mutation, lock, observed global Locked state, HTTP 401, and unload;
- uses no credential/evidence persistence in localStorage, sessionStorage, cookies, IndexedDB, Cache Storage, query, or fragment state.

## Security / authority boundary

A successful M55 browser validation establishes only that one downloaded capsule is internally byte-correlated:

- the capsule body hashes to the declared capsule SHA;
- the decoded manifest bytes hash to the declared manifest SHA;
- the decoded frozen M52 signature envelope names that same manifest SHA and the same key-fingerprint identifier;
- the server created the embedded detached signature from the exact embedded manifest bytes in one request path.

It does **not** establish:

- cryptographic signature validity in the browser;
- trusted public-key ownership or signer identity;
- human/operator identity;
- host/server/network identity;
- signature or completion time;
- uncompromised private-key custody;
- transactional atomicity of the upstream M43 snapshot/lifecycle/report/trace reads;
- semantic truth of report/trace/lifecycle claims;
- successful, durable, or transactionally atomic filesystem persistence after the browser initiates the save.

Anyone holding the configured private key can create indistinguishable valid M52 signatures. Public-key trust remains external.

## Explicit non-goals

M55 does not add:

- public-key publication;
- browser Ed25519 verification;
- PKI/certificates;
- trusted timestamps;
- transparency logs;
- key rotation/revocation registry;
- hardware/KMS custody;
- ZIP/tar archives;
- generic session/evidence bundles;
- snapshot/lifecycle/report/trace payload embedding;
- arbitrary artifact browsing;
- caller-selected filenames/paths;
- signature persistence;
- evidence mutation or repair;
- new CLI verification semantics.

A future milestone may add an offline extractor/verifier for the capsule, but M55 itself must not weaken or replace the frozen M52 separate-file verifier.

## Intended changed surface

M55 should remain confined to:

1. this milestone document;
2. one new capsule model/renderer;
3. one M55 operator HTTP-server subclass layered over frozen M53 transport;
4. narrow public package/CLI routing substitutions to that subclass;
5. additive operator UI markup + one allowlist entry + one new browser client;
6. focused capsule renderer/HTTP/browser/security/compatibility tests.

Frozen M43 manifest generation, M52 signing/verifier implementation, M53 signer implementation, M54 pair client, App Server store/service/protocol/runtime, report/trace/snapshot/lifecycle generation, coding runtime/verifier, model/tool, memory, budget, controller, and control implementation are outside the intended diff.

## Qualification contract

M55 cannot freeze until all of the following are true on one fixed final head:

- exact merge base is frozen M54 `72ccc7fd7fcd32ff904d27c1a58ad5c07247365c`;
- zero commits behind frozen M54;
- source/diff audit confirms the intended authority boundary;
- focused server/renderer/browser tests pass;
- packaged JavaScript syntax/behavior checks pass where Node is available;
- full pytest passes;
- `harness-x --help` passes;
- `harness-x validate-config configs/default.yaml` passes;
- no submitted reviews or actionable review threads remain;
- PR remains draft/open/unmerged;
- exact final head, synthetic merge, compare totals, CI identifiers, and test count are recorded in PR metadata without moving the branch.
