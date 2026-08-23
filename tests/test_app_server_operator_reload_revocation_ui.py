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


def test_reload_auth_lock_revocation_boundary_is_explicit() -> None:
    javascript = _asset_text("/ui/reload_auth.js")

    assert 'fetch("/v1/operator/reload-revoke"' in javascript
    assert 'fetch("/v1/operator/reload-family-revoke"' in javascript
    assert 'Authorization: `Bearer ${token}`' in javascript
    assert 'body: JSON.stringify({ ticket })' in javascript
    assert 'body: JSON.stringify({ family })' in javascript
    assert 'credentials: "omit"' in javascript
    assert 'cache: "no-store"' in javascript

    lock_start = javascript.index('reloadAuthById("lock-button").addEventListener("click"')
    lock_body = javascript[lock_start:]
    assert "const token = reloadAuthState.token;" in lock_body
    assert "const ticket = storedReloadCapability();" in lock_body
    assert "const family = storedReloadFamily();" in lock_body
    for cleanup in (
        "void revokeReloadCapability(token, ticket)",
        "void revokeReloadFamily(token, family)",
    ):
        assert lock_body.index("reloadAuthState.token = null;") < lock_body.index(cleanup)
        assert lock_body.index("removeStoredReloadCapability();") < lock_body.index(cleanup)
        assert lock_body.index("removeStoredReloadFamily();") < lock_body.index(cleanup)
        assert lock_body.index("cancelReloadRenewal();") < lock_body.index(cleanup)

    assert 'window.addEventListener("beforeunload"' not in javascript
    assert 'window.addEventListener("unload"' not in javascript
    assert 'window.addEventListener("pagehide"' not in javascript


def test_reload_auth_lock_revokes_after_local_clear_and_failure_stays_locked_when_node_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; reload revocation behavior test requires node")

    asset = files("harness_x.app_server").joinpath("ui", "reload_auth.js")
    with as_file(asset) as path:
        harness = r'''
const fs = require("fs");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const key = "harness-x.operator.reload-ticket.v1";
const familyKey = "harness-x.operator.reload-family.v1";
const ticket = "R".repeat(43);
const family = "F".repeat(43);
const storage = new Map([[key, ticket], [familyKey, family]]);
globalThis.sessionStorage = {
  getItem(name) { return storage.has(name) ? storage.get(name) : null; },
  setItem(name, value) { storage.set(name, String(value)); },
  removeItem(name) { storage.delete(name); },
};
globalThis.crypto = { getRandomValues(bytes) { bytes.fill(7); return bytes; } };
globalThis.btoa = (value) => Buffer.from(value, "binary").toString("base64");

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
globalThis.window = {
  location: { hash: "" },
  addEventListener() {},
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
globalThis.fetch = async (url, options) => {
  calls.push({
    url,
    options,
    ticketAtFetch: storage.get(key) ?? null,
    familyAtFetch: storage.get(familyKey) ?? null,
  });
  if (url === "/v1/operator/reload-revoke" || url === "/v1/operator/reload-family-revoke") {
    throw new Error("simulated network failure");
  }
  if (url === "/v1/operator/reload-family-ticket") {
    return {
      ok: true,
      status: 200,
      async json() {
        return { schema_version: "app-operator-reload-family-ticket-v1", ticket };
      },
    };
  }
  throw new Error("unexpected fetch");
};
globalThis.console = { warn() {} };

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), { filename: process.argv[1] });

(async () => {
  const form = elements.get("auth-form");
  const tokenField = elements.get("token");
  tokenField.value = "bearer-page-memory";
  form.handlers.get("submit")({ preventDefault() {} });
  await new Promise(setImmediate);
  assert(storage.get(key) === ticket, "auth must retain issued reload ticket");
  assert(storage.get(familyKey) === family, "auth must retain the existing family");
  assert(timers.size === 1, "auth must schedule renewal");

  elements.get("lock-button").handlers.get("click")({ preventDefault() {} });
  assert(!storage.has(key), "lock must clear ticket storage synchronously");
  assert(!storage.has(familyKey), "lock must clear family storage synchronously");
  assert(timers.size === 0, "lock must cancel renewal synchronously");
  assert(reloadAuthState.token === null, "lock must clear page-memory bearer before network completion");
  assert(calls.length === 3, "lock must attempt known-ticket and family cleanup after issuance");

  const ticketRevoke = calls[1];
  assert(ticketRevoke.url === "/v1/operator/reload-revoke", "lock must retain exact M50 ticket route");
  assert(ticketRevoke.ticketAtFetch === null && ticketRevoke.familyAtFetch === null, "local state must be clear before ticket cleanup");
  assert(ticketRevoke.options.headers.Authorization === "Bearer bearer-page-memory", "ticket cleanup must use captured bearer");
  assert(ticketRevoke.options.credentials === "omit", "ticket cleanup must omit ambient credentials");
  assert(ticketRevoke.options.cache === "no-store", "ticket cleanup must disable cache");
  assert(JSON.parse(ticketRevoke.options.body).ticket === ticket, "ticket cleanup must contain captured ticket only");

  const familyRevoke = calls[2];
  assert(familyRevoke.url === "/v1/operator/reload-family-revoke", "lock must call exact family route");
  assert(familyRevoke.ticketAtFetch === null && familyRevoke.familyAtFetch === null, "local state must be clear before family cleanup");
  assert(familyRevoke.options.headers.Authorization === "Bearer bearer-page-memory", "family cleanup must use captured bearer");
  assert(familyRevoke.options.credentials === "omit", "family cleanup must omit ambient credentials");
  assert(familyRevoke.options.cache === "no-store", "family cleanup must disable cache");
  assert(JSON.parse(familyRevoke.options.body).family === family, "family cleanup must contain captured family only");

  await new Promise(setImmediate);
  assert(!storage.has(key), "failed cleanup must never restore capability");
  assert(!storage.has(familyKey), "failed cleanup must never restore family");
  assert(reloadAuthState.token === null, "failed cleanup must never restore bearer");
  assert(timers.size === 0, "failed cleanup must never restart renewal");

  process.stdout.write("ok");
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
