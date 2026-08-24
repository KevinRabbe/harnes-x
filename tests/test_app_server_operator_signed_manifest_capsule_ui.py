from __future__ import annotations

import shutil
import subprocess
from importlib.resources import as_file, files

import pytest

from harness_x.app_server.ui_assets import load_ui_asset


def _asset_text(path: str) -> str:
    asset = load_ui_asset(path)
    assert asset is not None
    return asset[1].decode("utf-8")


def test_signed_manifest_capsule_is_additive_and_ordered_after_frozen_m54_pair() -> None:
    html = _asset_text("/ui/")
    pair = _asset_text("/ui/signed_manifest_pair.js")
    capsule = _asset_text("/ui/signed_manifest_capsule.js")

    assert 'id="download-signed-manifest-pair"' in html
    assert 'id="download-signed-manifest-capsule"' in html
    assert 'id="signed-manifest-pair-status"' in html
    assert 'id="signed-manifest-capsule-status"' in html
    assert "Download signed manifest pair" in html
    assert "Download signed manifest capsule" in html
    assert '<script src="/ui/signed_manifest_pair.js" defer></script>' in html
    assert '<script src="/ui/signed_manifest_capsule.js" defer></script>' in html
    assert (
        html.index('/ui/evidence_manifest.js')
        < html.index('/ui/signed_manifest_pair.js')
        < html.index('/ui/signed_manifest_capsule.js')
        < html.index('/ui/lifecycle_export.js')
        < html.index('/ui/app.js')
        < html.index('/ui/bootstrap.js')
    )

    assert "/evidence/manifest" in pair
    assert "/evidence/signature" in pair
    assert '"session-evidence-manifest.json"' in pair
    assert '"session-evidence-manifest.sig.json"' in pair
    assert "/evidence/signed-manifest-capsule" in capsule
    assert 'link.download = "session-evidence-signed-manifest-pair.json"' in capsule


def test_signed_manifest_capsule_client_pins_one_response_and_exact_nested_contracts() -> None:
    javascript = _asset_text("/ui/signed_manifest_capsule.js")

    assert "Authorization: `Bearer ${token}`" in javascript
    assert 'Accept: "application/json"' in javascript
    assert 'cache: "no-store"' in javascript
    assert 'credentials: "omit"' in javascript
    assert "new AbortController()" in javascript
    assert javascript.count("await fetch(") == 1
    assert 'response.headers.get("Content-Length")' not in javascript  # inherited M54 helper
    assert 'signedManifestPairLength(response, "signed manifest capsule")' in javascript
    assert 'response.headers.get("X-Harness-X-Evidence-Capsule-SHA256")' in javascript
    assert 'response.headers.get("X-Harness-X-Evidence-Manifest-SHA256")' in javascript
    assert 'response.headers.get("X-Harness-X-Evidence-Signature-Key")' in javascript
    assert 'response.headers.get("X-Harness-X-Evidence-Signature-Algorithm")' in javascript
    assert '"app-signed-manifest-capsule-v1"' in javascript
    assert '"app-terminal-evidence-manifest-v1"' in javascript
    assert '"app-evidence-signature-v1"' in javascript
    assert 'capsuleText !== signedManifestCapsuleCanonical(capsule)' in javascript
    assert 'observedManifestSha256 !== manifestSha256' in javascript
    assert 'envelope.manifest_sha256 !== manifestSha256' in javascript
    assert 'envelope.key_fingerprint !== keyFingerprint' in javascript
    assert 'envelopeText !== signedManifestPairCanonicalEnvelope(envelope)' in javascript
    assert "signedManifestPairSignaturePattern.test(envelope.signature || \"\")" in javascript
    assert 'signedManifestCapsuleBase64urlEncode(decoded) !== text' in javascript
    assert javascript.index("signedManifestCapsuleFetch(") < javascript.index(
        "signedManifestCapsuleClickDownload(capsule.bytes)"
    )

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "window.location",
        "public-key",
        "public_key",
        "crypto.subtle.verify",
        "bundle.zip",
        "?path=",
    ):
        assert forbidden not in javascript


def test_signed_manifest_capsule_asset_allowlist_remains_exact() -> None:
    assert load_ui_asset("/ui/signed_manifest_capsule.js") is not None
    assert load_ui_asset("/ui/signed-manifest-capsule.js") is None
    assert load_ui_asset("/ui/signed_manifest_capsule.js/../protocol.py") is None
    assert load_ui_asset("/ui/../../etc/passwd") is None


def test_signed_manifest_capsule_client_has_valid_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; JavaScript syntax check requires an available node binary")

    asset = files("harness_x.app_server").joinpath("ui", "signed_manifest_capsule.js")
    with as_file(asset) as path:
        completed = subprocess.run(
            [node, "--check", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr


def test_signed_manifest_capsule_behavior_in_node_when_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; signed-manifest capsule behavior test requires node")

    pair_asset = files("harness_x.app_server").joinpath("ui", "signed_manifest_pair.js")
    capsule_asset = files("harness_x.app_server").joinpath("ui", "signed_manifest_capsule.js")
    with as_file(pair_asset) as pair_path, as_file(capsule_asset) as capsule_path:
        harness = r'''
const fs = require("fs");
const vm = require("vm");
const nodeCrypto = require("crypto");

const elements = new Map();
function makeElement(id) {
  const element = {
    id,
    disabled: false,
    textContent: "",
    value: "",
    handlers: new Map(),
    addEventListener(type, handler) {
      if (!this.handlers.has(type)) this.handlers.set(type, []);
      this.handlers.get(type).push(handler);
    },
  };
  elements.set(id, element);
  return element;
}
for (const id of [
  "download-signed-manifest-pair",
  "signed-manifest-pair-status",
  "download-signed-manifest-capsule",
  "signed-manifest-capsule-status",
  "session-id",
  "session-status",
  "auth-form",
  "token",
  "lock-button",
  "auth-state",
]) makeElement(id);

globalThis.document = {
  getElementById(id) {
    if (!elements.has(id)) makeElement(id);
    return elements.get(id);
  },
  createElement(tag) {
    if (tag !== "a") throw new Error(`unexpected element ${tag}`);
    return {
      href: "",
      download: "",
      click() { downloads.push(this.download); },
      remove() {},
    };
  },
  body: { append() {} },
};
class FakeMutationObserver {
  constructor(callback) { this.callback = callback; observers.push(this); }
  observe() {}
}
const observers = [];
globalThis.MutationObserver = FakeMutationObserver;
const unloadHandlers = [];
globalThis.window = {
  addEventListener(type, handler) { if (type === "beforeunload") unloadHandlers.push(handler); },
};

const downloads = [];
let objectUrlCounter = 0;
globalThis.URL.createObjectURL = () => `blob:test-${++objectUrlCounter}`;
globalThis.URL.revokeObjectURL = () => {};

class HeadersLike {
  constructor(values) {
    this.values = new Map(Object.entries(values).map(([key, value]) => [key.toLowerCase(), String(value)]));
  }
  get(name) { return this.values.get(String(name).toLowerCase()) ?? null; }
}
class FakeResponse {
  constructor(status, headers, bytes, jsonPayload = null) {
    this.status = status;
    this.ok = status >= 200 && status < 300;
    this.headers = new HeadersLike(headers);
    this.bytes = bytes;
    this.jsonPayload = jsonPayload;
  }
  async arrayBuffer() { return this.bytes.slice(0); }
  async json() {
    if (this.jsonPayload !== null) return this.jsonPayload;
    return JSON.parse(new TextDecoder().decode(this.bytes));
  }
}
function bytes(text) { return new TextEncoder().encode(text).buffer; }
function sha256(textOrBytes) {
  return nodeCrypto.createHash("sha256").update(textOrBytes).digest("hex");
}
function base64url(text) {
  return Buffer.from(text, "utf8").toString("base64url");
}

const sessionA = "app_" + "a".repeat(32);
const sessionB = "app_" + "b".repeat(32);
const key = "sha256:" + "b".repeat(64);
const signatureText = "A".repeat(86);
const manifestBody = JSON.stringify({
  schema_version: "app-terminal-evidence-manifest-v1",
  session_id: sessionA,
  fingerprint: "c".repeat(64),
  lifecycle: { status: "succeeded", ledger_head_hash: "d".repeat(64) },
  coding_report: { availability: "available" },
  causal_trace: { availability: "not_available" },
}) + "\n";
const manifestHash = sha256(manifestBody);
const signatureBody = JSON.stringify({
  algorithm: "ed25519",
  key_fingerprint: key,
  manifest_sha256: manifestHash,
  schema_version: "app-evidence-signature-v1",
  signature: signatureText,
}) + "\n";
function capsuleBody(options = {}) {
  const capsule = {
    algorithm: "ed25519",
    key_fingerprint: key,
    manifest_payload: options.manifestPayload || base64url(manifestBody),
    manifest_sha256: options.manifestHash || manifestHash,
    schema_version: "app-signed-manifest-capsule-v1",
    signature_payload: options.signaturePayload || base64url(signatureBody),
  };
  if (options.reverseOrder) {
    return JSON.stringify({
      signature_payload: capsule.signature_payload,
      schema_version: capsule.schema_version,
      manifest_sha256: capsule.manifest_sha256,
      manifest_payload: capsule.manifest_payload,
      key_fingerprint: capsule.key_fingerprint,
      algorithm: capsule.algorithm,
    }) + "\n";
  }
  return JSON.stringify(capsule) + "\n";
}
function capsuleResponse(options = {}) {
  const body = capsuleBody(options);
  return new FakeResponse(200, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Disposition": 'attachment; filename="session-evidence-signed-manifest-pair.json"',
    "Content-Length": new TextEncoder().encode(body).byteLength,
    "X-Harness-X-Evidence-Capsule-SHA256": sha256(body),
    "X-Harness-X-Evidence-Manifest-SHA256": options.headerManifestHash || manifestHash,
    "X-Harness-X-Evidence-Signature-Key": key,
    "X-Harness-X-Evidence-Signature-Algorithm": "ed25519",
  }, bytes(body));
}

const fetchCalls = [];
let fetchImpl = null;
globalThis.fetch = async (url, options) => {
  fetchCalls.push([url, options]);
  if (!fetchImpl) throw new Error("fetchImpl not installed");
  return fetchImpl(url, options);
};

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), { filename: process.argv[1] });
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"), { filename: process.argv[2] });

function assert(condition, message) {
  if (!condition) throw new Error(message);
}
function fire(id, type, event = {}) {
  for (const handler of document.getElementById(id).handlers.get(type) || []) handler(event);
}
function unlock() {
  document.getElementById("token").value = "bearer";
  fire("auth-form", "submit", { preventDefault() {} });
}
function select(sessionId, status = "succeeded") {
  document.getElementById("session-id").textContent = sessionId;
  document.getElementById("session-status").textContent = status;
}

(async () => {
  select(sessionA);
  unlock();
  fetchImpl = async () => capsuleResponse();
  await downloadSignedManifestCapsule();
  assert(fetchCalls.length === 1, "success must perform exactly one capsule fetch");
  assert(fetchCalls[0][0].endsWith(`/v1/sessions/${sessionA}/evidence/signed-manifest-capsule`), "must use exact M55 route");
  assert(fetchCalls[0][1].headers.Authorization === "Bearer bearer", "must use page-memory bearer");
  assert(fetchCalls[0][1].credentials === "omit", "must omit ambient credentials");
  assert(downloads.length === 1, "success must initiate exactly one save");
  assert(downloads[0] === "session-evidence-signed-manifest-pair.json", "must use fixed capsule filename");
  assert(document.getElementById("signed-manifest-capsule-status").textContent.includes(manifestHash), "success status must expose correlated manifest SHA");
  assert(document.getElementById("signed-manifest-capsule-status").textContent.includes(key), "success status must expose key identifier without trust claim");

  downloads.length = 0;
  fetchCalls.length = 0;
  fetchImpl = async () => capsuleResponse({ headerManifestHash: "e".repeat(64) });
  await downloadSignedManifestCapsule();
  assert(downloads.length === 0, "header/body manifest SHA mismatch must suppress save");
  assert(document.getElementById("signed-manifest-capsule-status").textContent.includes("manifest SHA does not match"), "mismatch must fail visibly");

  downloads.length = 0;
  fetchImpl = async () => capsuleResponse({ reverseOrder: true });
  await downloadSignedManifestCapsule();
  assert(downloads.length === 0, "noncanonical capsule serialization must suppress save");
  assert(document.getElementById("signed-manifest-capsule-status").textContent.includes("canonical M55 serialization"), "noncanonical capsule must fail visibly");

  downloads.length = 0;
  const noncanonicalManifestPayload = base64url(manifestBody).slice(0, -1) + "B";
  fetchImpl = async () => capsuleResponse({ manifestPayload: noncanonicalManifestPayload });
  await downloadSignedManifestCapsule();
  assert(downloads.length === 0, "noncanonical base64url must suppress save");

  downloads.length = 0;
  let resolveFetch;
  fetchImpl = async () => new Promise((resolve) => { resolveFetch = resolve; });
  const stale = downloadSignedManifestCapsule();
  while (!resolveFetch) await new Promise((resolve) => setImmediate(resolve));
  document.getElementById("session-id").textContent = sessionB;
  resolveFetch(capsuleResponse());
  await stale;
  assert(downloads.length === 0, "selection change while capsule is in flight must suppress save");

  select(sessionA);
  unlock();
  downloads.length = 0;
  fetchImpl = async () => new FakeResponse(
    401,
    { "Content-Type": "application/json; charset=utf-8" },
    bytes('{"error":"unauthorized"}\n'),
    { error: "unauthorized" },
  );
  await downloadSignedManifestCapsule();
  assert(downloads.length === 0, "401 must not save anything");
  assert(document.getElementById("download-signed-manifest-capsule").disabled, "401 must clear capsule bearer eligibility");

  assert(unloadHandlers.length >= 2, "M54 and M55 must both register unload cleanup");
  for (const handler of unloadHandlers) handler();
  console.log("ok");
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
'''
        completed = subprocess.run(
            [node, "-e", harness, str(pair_path), str(capsule_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"
