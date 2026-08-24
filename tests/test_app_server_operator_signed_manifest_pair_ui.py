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


def test_signed_manifest_pair_is_packaged_without_replacing_standalone_manifest_export() -> None:
    html = _asset_text("/ui/")
    javascript = _asset_text("/ui/signed_manifest_pair.js")
    standalone = _asset_text("/ui/evidence_manifest.js")

    assert 'id="download-evidence-manifest"' in html
    assert 'id="download-signed-manifest-pair"' in html
    assert 'id="signed-manifest-pair-status"' in html
    assert 'Download evidence manifest' in html
    assert 'Download signed manifest pair' in html
    assert '<script src="/ui/evidence_manifest.js" defer></script>' in html
    assert '<script src="/ui/signed_manifest_pair.js" defer></script>' in html
    assert (
        html.index('/ui/evidence_manifest.js')
        < html.index('/ui/signed_manifest_pair.js')
        < html.index('/ui/lifecycle_export.js')
        < html.index('/ui/app.js')
        < html.index('/ui/bootstrap.js')
    )

    assert "/evidence/manifest" in standalone
    assert 'link.download = "session-evidence-manifest.json"' in standalone
    assert "/evidence/manifest" in javascript
    assert "/evidence/signature" in javascript
    assert '"session-evidence-manifest.json"' in javascript
    assert '"session-evidence-manifest.sig.json"' in javascript


def test_signed_manifest_pair_client_pins_exact_correlation_and_m52_envelope_shape() -> None:
    javascript = _asset_text("/ui/signed_manifest_pair.js")

    assert "Authorization: `Bearer ${token}`" in javascript
    assert 'Accept: "application/json"' in javascript
    assert 'cache: "no-store"' in javascript
    assert 'credentials: "omit"' in javascript
    assert "new AbortController()" in javascript
    assert 'crypto.subtle.digest("SHA-256", bytes)' in javascript
    assert 'response.headers.get("Content-Length")' in javascript
    assert 'response.headers.get("X-Harness-X-Evidence-Manifest-SHA256")' in javascript
    assert 'response.headers.get("X-Harness-X-Evidence-Signature-Key")' in javascript
    assert 'response.headers.get("X-Harness-X-Evidence-Signature-Algorithm")' in javascript
    assert '"app-terminal-evidence-manifest-v1"' in javascript
    assert '"app-evidence-signature-v1"' in javascript
    assert 'responseManifestSha !== manifestSha256' in javascript
    assert 'envelope.manifest_sha256 !== manifestSha256' in javascript
    assert 'envelope.key_fingerprint !== keyFingerprint' in javascript
    assert 'envelopeText !== signedManifestPairCanonicalEnvelope(envelope)' in javascript
    assert 'JSON.stringify({' in javascript
    assert ') + "\\n";' in javascript
    assert javascript.index("signedManifestPairFetchManifest(") < javascript.index("signedManifestPairFetchSignature(")
    assert javascript.index("const signature = await signedManifestPairFetchSignature(") < javascript.index("signedManifestPairClickDownload(")

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "window.location",
        "public-key",
        "public_key",
        "crypto.subtle.verify",
        "Ed25519",
        "bundle.zip",
        "?path=",
    ):
        assert forbidden not in javascript


def test_signed_manifest_pair_asset_allowlist_remains_exact() -> None:
    assert load_ui_asset("/ui/signed_manifest_pair.js") is not None
    assert load_ui_asset("/ui/signed-manifest-pair.js") is None
    assert load_ui_asset("/ui/signed_manifest_pair.js/../protocol.py") is None
    assert load_ui_asset("/ui/../../etc/passwd") is None


def test_signed_manifest_pair_client_has_valid_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; JavaScript syntax check requires an available node binary")

    asset = files("harness_x.app_server").joinpath("ui", "signed_manifest_pair.js")
    with as_file(asset) as path:
        completed = subprocess.run(
            [node, "--check", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr


def test_signed_manifest_pair_behavior_in_node_when_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; signed-manifest pair behavior test requires node")

    asset = files("harness_x.app_server").joinpath("ui", "signed_manifest_pair.js")
    with as_file(asset) as path:
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
    addEventListener(type, handler) { this.handlers.set(type, handler); },
  };
  elements.set(id, element);
  return element;
}
for (const id of [
  "download-signed-manifest-pair",
  "signed-manifest-pair-status",
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
let unloadHandler = null;
globalThis.window = {
  addEventListener(type, handler) { if (type === "beforeunload") unloadHandler = handler; },
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
function sha256(text) { return nodeCrypto.createHash("sha256").update(text).digest("hex"); }

const sessionA = "app_" + "a".repeat(32);
const sessionB = "app_" + "b".repeat(32);
const key = "sha256:" + "b".repeat(64);
const signatureText = "A".repeat(86);
function makeManifest(sessionId = sessionA) {
  const body = JSON.stringify({
    schema_version: "app-terminal-evidence-manifest-v1",
    session_id: sessionId,
    fingerprint: "c".repeat(64),
    lifecycle: { status: "succeeded", ledger_head_hash: "d".repeat(64) },
    coding_report: { availability: "available" },
    causal_trace: { availability: "not_available" },
  }) + "\n";
  const hash = sha256(body);
  return {
    hash,
    body,
    response: new FakeResponse(200, {
      "Content-Type": "application/json; charset=utf-8",
      "Content-Disposition": 'attachment; filename="session-evidence-manifest.json"',
      "Content-Length": new TextEncoder().encode(body).byteLength,
      "X-Harness-X-Evidence-Manifest-SHA256": hash,
    }, bytes(body)),
  };
}
function makeSignature(manifestHash, options = {}) {
  const envelope = {
    algorithm: "ed25519",
    key_fingerprint: key,
    manifest_sha256: options.envelopeHash || manifestHash,
    schema_version: "app-evidence-signature-v1",
    signature: signatureText,
  };
  let body = JSON.stringify(envelope) + "\n";
  if (options.noncanonicalDuplicate) {
    body = '{"algorithm":"ed25519","algorithm":"ed25519","key_fingerprint":"'
      + key + '","manifest_sha256":"' + manifestHash
      + '","schema_version":"app-evidence-signature-v1","signature":"'
      + signatureText + '"}\n';
  }
  return new FakeResponse(200, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Disposition": 'attachment; filename="session-evidence-manifest.sig.json"',
    "Content-Length": new TextEncoder().encode(body).byteLength,
    "X-Harness-X-Evidence-Manifest-SHA256": options.headerHash || manifestHash,
    "X-Harness-X-Evidence-Signature-Key": options.headerKey || key,
    "X-Harness-X-Evidence-Signature-Algorithm": options.algorithm || "ed25519",
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

function assert(condition, message) {
  if (!condition) throw new Error(message);
}
function unlock() {
  document.getElementById("token").value = "bearer";
  document.getElementById("auth-form").handlers.get("submit")({ preventDefault() {} });
}
function select(sessionId, status = "succeeded") {
  document.getElementById("session-id").textContent = sessionId;
  document.getElementById("session-status").textContent = status;
}

(async () => {
  select(sessionA);
  unlock();
  const manifest = makeManifest();
  const signature = makeSignature(manifest.hash);
  const queue = [manifest.response, signature];
  fetchImpl = async () => queue.shift();
  await downloadSignedManifestPair();
  assert(fetchCalls.length === 2, "success must perform exactly manifest then signature fetch");
  assert(fetchCalls[0][0].endsWith(`/v1/sessions/${sessionA}/evidence/manifest`), "manifest must be fetched first");
  assert(fetchCalls[1][0].endsWith(`/v1/sessions/${sessionA}/evidence/signature`), "signature must be fetched second");
  assert(fetchCalls.every(([, options]) => options.headers.Authorization === "Bearer bearer"), "both requests must use the page-memory bearer");
  assert(downloads.length === 2, "success must initiate two downloads only after validation");
  assert(downloads[0] === "session-evidence-manifest.json", "manifest filename must remain frozen");
  assert(downloads[1] === "session-evidence-manifest.sig.json", "signature filename must remain frozen");
  assert(document.getElementById("signed-manifest-pair-status").textContent.includes(manifest.hash), "success status must expose correlated manifest SHA");
  assert(document.getElementById("signed-manifest-pair-status").textContent.includes(key), "success status must expose signer identifier without claiming trust");

  downloads.length = 0;
  fetchCalls.length = 0;
  const mismatchQueue = [manifest.response, makeSignature(manifest.hash, { headerHash: "e".repeat(64) })];
  fetchImpl = async () => mismatchQueue.shift();
  await downloadSignedManifestPair();
  assert(downloads.length === 0, "signature header mismatch must suppress both downloads");
  assert(document.getElementById("signed-manifest-pair-status").textContent.includes("different manifest bytes"), "mismatch must fail visibly");

  downloads.length = 0;
  const duplicateQueue = [manifest.response, makeSignature(manifest.hash, { noncanonicalDuplicate: true })];
  fetchImpl = async () => duplicateQueue.shift();
  await downloadSignedManifestPair();
  assert(downloads.length === 0, "duplicate/noncanonical envelope must suppress both downloads");
  assert(document.getElementById("signed-manifest-pair-status").textContent.includes("canonical M52 envelope"), "noncanonical envelope must fail visibly");

  downloads.length = 0;
  let resolveSignature;
  let callIndex = 0;
  fetchImpl = async () => {
    callIndex += 1;
    if (callIndex === 1) return manifest.response;
    return new Promise((resolve) => { resolveSignature = resolve; });
  };
  const stale = downloadSignedManifestPair();
  while (!resolveSignature) await new Promise((resolve) => setImmediate(resolve));
  document.getElementById("session-id").textContent = sessionB;
  resolveSignature(signature);
  await stale;
  assert(downloads.length === 0, "selection change while signature is in flight must suppress downloads");

  select(sessionA);
  unlock();
  downloads.length = 0;
  fetchImpl = async () => new FakeResponse(401, { "Content-Type": "application/json; charset=utf-8" }, bytes('{}\n'));
  await downloadSignedManifestPair();
  assert(downloads.length === 0, "401 must not download anything");
  assert(document.getElementById("download-signed-manifest-pair").disabled, "401 must clear pair-client bearer eligibility");

  assert(typeof unloadHandler === "function", "M54 must register unload cleanup");
  unloadHandler();
  console.log("ok");
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
'''
        completed = subprocess.run(
            [node, "-e", harness, str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"
