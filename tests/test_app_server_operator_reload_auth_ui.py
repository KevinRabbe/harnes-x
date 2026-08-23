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


def test_reload_auth_client_is_packaged_in_qualified_listener_order() -> None:
    html = _asset_text("/ui/")
    javascript = _asset_text("/ui/reload_auth.js")
    app = _asset_text("/ui/app.js")
    bootstrap = _asset_text("/ui/bootstrap.js")

    assert "short-lived one-time reload capability" in html
    assert "sessionStorage" in html
    assert '<script src="/ui/reload_auth.js" defer></script>' in html
    assert (
        html.index("/ui/report.js")
        < html.index("/ui/report_export.js")
        < html.index("/ui/trace_export.js")
        < html.index("/ui/evidence_manifest.js")
        < html.index("/ui/lifecycle_export.js")
        < html.index("/ui/snapshot_export.js")
        < html.index("/ui/reload_auth.js")
        < html.index("/ui/app.js")
        < html.index("/ui/stream_recovery.js")
        < html.index("/ui/bootstrap.js")
    )
    assert 'reloadAuthById("auth-form").addEventListener("submit"' in javascript
    assert 'byId("auth-form").addEventListener("submit"' in app
    assert 'authForm.requestSubmit()' in bootstrap


def test_reload_auth_storage_network_rotation_and_bootstrap_boundaries_are_explicit() -> None:
    javascript = _asset_text("/ui/reload_auth.js")

    assert 'const reloadAuthStorageKey = "harness-x.operator.reload-ticket.v1";' in javascript
    assert 'sessionStorage.getItem(reloadAuthStorageKey)' in javascript
    assert 'sessionStorage.setItem(reloadAuthStorageKey, ticket)' in javascript
    assert 'sessionStorage.removeItem(reloadAuthStorageKey)' in javascript
    assert 'window.location.hash.startsWith("#bootstrap=")' in javascript
    assert "if (reloadAuthBootstrapPresentAtLoad) return;" in javascript
    assert 'fetch("/v1/operator/reload-ticket"' in javascript
    assert 'fetch("/v1/operator/reload"' in javascript
    assert 'fetch("/v1/operator/reload-revoke"' in javascript
    assert 'Authorization: `Bearer ${token}`' in javascript
    assert javascript.count('credentials: "omit"') == 3
    assert javascript.count('cache: "no-store"') == 3
    assert "const previousTicket = storedReloadCapability();" in javascript
    assert 'JSON.stringify({ previous_ticket: previousTicket })' in javascript
    assert 'JSON.stringify({ ticket })' in javascript
    assert "removeStoredReloadCapability();" in javascript
    assert javascript.index("removeStoredReloadCapability();", javascript.index("async function restoreOperatorAfterReload")) < javascript.index('fetch("/v1/operator/reload"')
    assert 'payload.access_token = ""' in javascript
    assert 'authForm.requestSubmit()' in javascript
    assert 'tokenField.value = ""' in javascript
    assert "reloadAuthRenewalIntervalMs = 120000" in javascript
    assert "reloadAuthNetworkRetryMs = 30000" in javascript
    assert "cancelReloadRenewal();" in javascript
    assert 'reloadAuthById("lock-button").addEventListener("click"' in javascript
    assert "reloadAuthState.authGeneration" in javascript
    assert "reloadAuthState.mintGeneration" in javascript

    for forbidden in (
        "localStorage",
        "document.cookie",
        "indexedDB",
        "caches.open",
        "serviceWorker",
        "SharedWorker",
        "window.location.search",
        "history.pushState",
        "history.replaceState",
    ):
        assert forbidden not in javascript

    assert "sessionStorage.setItem(reloadAuthStorageKey, token)" not in javascript
    assert "sessionStorage.setItem(reloadAuthStorageKey, accessToken)" not in javascript
    assert "sessionStorage.setItem(reloadAuthStorageKey, payload.access_token)" not in javascript


def test_reload_auth_asset_allowlist_remains_exact() -> None:
    assert load_ui_asset("/ui/reload_auth.js") is not None
    assert load_ui_asset("/ui/reload-auth.js") is None
    assert load_ui_asset("/ui/reload_auth.js/../protocol.py") is None
    assert load_ui_asset("/ui/../../etc/passwd") is None


def test_packaged_reload_auth_client_has_valid_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; reload auth syntax check requires node")

    asset = files("harness_x.app_server").joinpath("ui", "reload_auth.js")
    with as_file(asset) as path:
        completed = subprocess.run(
            [node, "--check", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr


def test_reload_auth_behavior_in_node_when_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; reload auth behavior test requires node")

    asset = files("harness_x.app_server").joinpath("ui", "reload_auth.js")
    with as_file(asset) as path:
        harness = r'''
const fs = require("fs");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}
function fakeResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return structuredClone(payload); },
  };
}

const storage = new Map();
globalThis.sessionStorage = {
  getItem(key) { return storage.has(key) ? storage.get(key) : null; },
  setItem(key, value) { storage.set(key, String(value)); },
  removeItem(key) { storage.delete(key); },
};

const elements = new Map();
function makeElement(id) {
  const element = {
    id,
    value: "",
    textContent: "",
    handlers: new Map(),
    addEventListener(type, handler) { this.handlers.set(type, handler); },
  };
  elements.set(id, element);
  return element;
}
for (const id of ["auth-form", "token", "lock-button", "auth-error"]) makeElement(id);
globalThis.document = { getElementById(id) { return elements.get(id); } };

let domReady = null;
globalThis.window = {
  location: { hash: "" },
  addEventListener(type, handler) { if (type === "DOMContentLoaded") domReady = handler; },
};

let timerId = 0;
const timers = new Map();
globalThis.setTimeout = (callback, delay) => {
  timerId += 1;
  timers.set(timerId, { callback, delay });
  return timerId;
};
globalThis.clearTimeout = (id) => timers.delete(id);

const calls = [];
const responses = [];
globalThis.fetch = async (url, options) => {
  calls.push({ url, options, stored: storage.get("harness-x.operator.reload-ticket.v1") ?? null });
  if (responses.length === 0) throw new Error("unexpected fetch");
  return responses.shift();
};
globalThis.console = { warn() {} };

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), { filename: process.argv[1] });
assert(typeof domReady === "function", "client must defer restoration until DOMContentLoaded");

const form = elements.get("auth-form");
const token = elements.get("token");
const lock = elements.get("lock-button");
form.requestSubmit = () => {
  const handler = form.handlers.get("submit");
  assert(typeof handler === "function", "submit listener must be registered");
  handler({ preventDefault() {} });
};

(async () => {
  const key = "harness-x.operator.reload-ticket.v1";
  const ticketA = "A".repeat(43);
  const ticketB = "B".repeat(43);
  const ticketC = "C".repeat(43);

  responses.push(fakeResponse(200, { schema_version: "app-operator-reload-ticket-v1", ticket: ticketA }));
  token.value = "bearer-main";
  form.handlers.get("submit")({ preventDefault() {} });
  await new Promise(setImmediate);
  assert(calls.length === 1 && calls[0].url === "/v1/operator/reload-ticket", "manual auth must mint reload ticket");
  assert(calls[0].options.headers.Authorization === "Bearer bearer-main", "issuance must use page-memory bearer");
  assert(calls[0].options.credentials === "omit", "issuance must omit ambient credentials");
  assert(storage.get(key) === ticketA, "only reload ticket must persist");
  assert([...storage.values()].every((value) => value !== "bearer-main"), "bearer must never persist");
  assert([...timers.values()].some((item) => item.delay === 120000), "successful auth must schedule bounded renewal");

  storage.set(key, ticketB);
  responses.push(fakeResponse(200, { schema_version: "app-operator-reload-v1", access_token: "bearer-restored" }));
  responses.push(fakeResponse(200, { schema_version: "app-operator-reload-ticket-v1", ticket: ticketC }));
  token.value = "";
  await restoreOperatorAfterReload();
  assert(calls[1].url === "/v1/operator/reload", "reload path must redeem capability");
  assert(calls[1].stored === null, "capability must be removed before redemption fetch");
  assert(calls[1].options.credentials === "omit", "redemption must omit ambient credentials");
  await new Promise(setImmediate);
  assert(calls[2].url === "/v1/operator/reload-ticket", "restored auth must rotate next ticket through existing submit listener");
  assert(calls[2].options.headers.Authorization === "Bearer bearer-restored", "rotation must use recovered bearer only in memory");
  assert(storage.get(key) === ticketC, "rotated capability must replace redeemed capability");
  assert(token.value === "", "temporary DOM bearer field must be cleared");
  assert([...storage.values()].every((value) => !String(value).startsWith("bearer-")), "recovered bearer must never persist");

  responses.push(fakeResponse(204, null));
  lock.handlers.get("click")({ preventDefault() {} });
  assert(!storage.has(key), "lock must synchronously clear reload capability");
  assert(timers.size === 0, "lock must cancel renewal timer");
  assert(calls[3].url === "/v1/operator/reload-revoke", "lock must revoke the captured reload capability");
  assert(calls[3].stored === null, "revocation must start only after local capability removal");
  assert(calls[3].options.headers.Authorization === "Bearer bearer-restored", "revocation must use page-memory bearer");
  assert(JSON.parse(calls[3].options.body).ticket === ticketC, "revocation must target the current tab capability");

  console.log = process.stdout.write.bind(process.stdout);
  console.log("ok");
})().catch((error) => {
  process.stderr.write(String(error.stack || error));
  process.exit(1);
});
'''
        completed = subprocess.run(
            [node, "-e", harness, str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "ok"
