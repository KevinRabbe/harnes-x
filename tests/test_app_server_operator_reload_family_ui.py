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


def test_reload_family_client_boundary_is_explicit() -> None:
    javascript = _asset_text("/ui/reload_auth.js")

    assert 'const reloadAuthFamilyStorageKey = "harness-x.operator.reload-family.v1";' in javascript
    assert "const bytes = new Uint8Array(32);" in javascript
    assert "crypto.getRandomValues(bytes);" in javascript
    assert 'fetch("/v1/operator/reload-family-ticket"' in javascript
    assert 'body: JSON.stringify({ previous_ticket: previousTicket, family })' in javascript
    assert 'fetch("/v1/operator/reload-family-revoke"' in javascript
    assert 'body: JSON.stringify({ family })' in javascript
    assert "const family = storedReloadFamily();" in javascript
    assert "removeStoredReloadFamily();" in javascript

    lock_start = javascript.index('reloadAuthById("lock-button").addEventListener("click"')
    lock_body = javascript[lock_start:]
    assert lock_body.index("removeStoredReloadFamily();") < lock_body.index(
        "void revokeReloadFamily(token, family)"
    )

    restore_start = javascript.index("async function restoreOperatorAfterReload")
    restore_end = javascript.index('reloadAuthById("auth-form").addEventListener', restore_start)
    restore_body = javascript[restore_start:restore_end]
    assert "removeStoredReloadFamily();" not in restore_body
    assert "revokeReloadFamily" not in restore_body


def test_transport_loss_without_known_ticket_can_still_revoke_family_on_lock_when_node_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; reload family behavior test requires node")

    asset = files("harness_x.app_server").joinpath("ui", "reload_auth.js")
    with as_file(asset) as path:
        harness = r'''
const fs = require("fs");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const ticketKey = "harness-x.operator.reload-ticket.v1";
const familyKey = "harness-x.operator.reload-family.v1";
const storage = new Map();
globalThis.sessionStorage = {
  getItem(name) { return storage.has(name) ? storage.get(name) : null; },
  setItem(name, value) { storage.set(name, String(value)); },
  removeItem(name) { storage.delete(name); },
};
globalThis.crypto = {
  getRandomValues(bytes) {
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = 255 - index;
    return bytes;
  },
};
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
globalThis.window = { location: { hash: "" }, addEventListener() {} };

let timerId = 0;
const timers = new Map();
globalThis.setTimeout = (callback, delay) => {
  timerId += 1;
  timers.set(timerId, { callback, delay });
  return timerId;
};
globalThis.clearTimeout = (id) => timers.delete(id);
globalThis.console = { warn() {} };

const calls = [];
globalThis.fetch = async (url, options) => {
  calls.push({
    url,
    options,
    ticket: storage.get(ticketKey) ?? null,
    family: storage.get(familyKey) ?? null,
  });
  if (url === "/v1/operator/reload-family-ticket") {
    throw new Error("response lost after possible server-side issuance");
  }
  if (url === "/v1/operator/reload-family-revoke") {
    return { ok: true, status: 204 };
  }
  throw new Error("unexpected fetch");
};

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), { filename: process.argv[1] });

(async () => {
  elements.get("token").value = "bearer-main";
  elements.get("auth-form").handlers.get("submit")({ preventDefault() {} });
  await new Promise(setImmediate);

  assert(calls.length === 1 && calls[0].url === "/v1/operator/reload-family-ticket", "auth must attempt family issuance");
  const family = storage.get(familyKey);
  assert(typeof family === "string" && /^[A-Za-z0-9_-]{43}$/.test(family), "family must exist before issuance");
  assert(JSON.parse(calls[0].options.body).family === family, "possibly-lost issuance must be bound to known family");
  assert(!storage.has(ticketKey), "transport loss must not invent a ticket value");
  assert([...timers.values()].some((item) => item.delay === 30000), "transport loss must schedule bounded retry");

  elements.get("lock-button").handlers.get("click")({ preventDefault() {} });
  assert(!storage.has(ticketKey), "lock must remain ticketless");
  assert(!storage.has(familyKey), "lock must clear family synchronously");
  assert(reloadAuthState.token === null, "lock must clear page-memory bearer");
  assert(timers.size === 0, "lock must cancel retry");
  assert(calls.length === 2, "ticketless lock must issue only family cleanup");
  assert(calls[1].url === "/v1/operator/reload-family-revoke", "ticketless lock must use family cleanup route");
  assert(calls[1].ticket === null && calls[1].family === null, "family cleanup must begin after local clear");
  assert(calls[1].options.headers.Authorization === "Bearer bearer-main", "family cleanup must use captured page-memory bearer");
  assert(JSON.parse(calls[1].options.body).family === family, "family cleanup must target pre-issuance family");

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
