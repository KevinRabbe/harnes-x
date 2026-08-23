from __future__ import annotations

import shutil
import subprocess
from importlib.resources import as_file, files

import pytest


def test_lock_revokes_successful_stale_inflight_mint_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; reload revocation race test requires node")

    asset = files("harness_x.app_server").joinpath("ui", "reload_auth.js")
    with as_file(asset) as path:
        harness = r'''
const fs = require("fs");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const key = "harness-x.operator.reload-ticket.v1";
const oldTicket = "T".repeat(43);
const replacementTicket = "U".repeat(43);
const storage = new Map([[key, oldTicket]]);
globalThis.sessionStorage = {
  getItem(name) { return storage.has(name) ? storage.get(name) : null; },
  setItem(name, value) { storage.set(name, String(value)); },
  removeItem(name) { storage.delete(name); },
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
globalThis.window = { location: { hash: "" }, addEventListener() {} };
globalThis.setTimeout = () => 1;
globalThis.clearTimeout = () => {};
globalThis.console = { warn() {} };

let resolveMint;
const calls = [];
globalThis.fetch = (url, options) => {
  calls.push({ url, options, stored: storage.get(key) ?? null });
  if (url === "/v1/operator/reload-ticket") {
    return new Promise((resolve) => { resolveMint = resolve; });
  }
  if (url === "/v1/operator/reload-revoke") {
    return Promise.resolve({ ok: true, status: 204 });
  }
  return Promise.reject(new Error("unexpected fetch"));
};

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), { filename: process.argv[1] });

(async () => {
  const form = elements.get("auth-form");
  elements.get("token").value = "bearer-main";
  form.handlers.get("submit")({ preventDefault() {} });
  assert(calls.length === 1 && calls[0].url === "/v1/operator/reload-ticket", "auth must start mint");
  assert(JSON.parse(calls[0].options.body).previous_ticket === oldTicket, "mint must rotate old ticket");

  elements.get("lock-button").handlers.get("click")({ preventDefault() {} });
  assert(!storage.has(key), "lock must clear old ticket before cleanup");
  assert(reloadAuthState.token === null, "lock must clear bearer state");
  assert(calls.length === 2 && calls[1].url === "/v1/operator/reload-revoke", "lock must revoke known old ticket");
  assert(JSON.parse(calls[1].options.body).ticket === oldTicket, "first revocation must target old ticket");
  assert(calls[1].stored === null, "old ticket must already be absent when revocation starts");

  resolveMint({
    ok: true,
    status: 200,
    async json() {
      return { schema_version: "app-operator-reload-ticket-v1", ticket: replacementTicket };
    },
  });
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  assert(calls.length === 3, "stale successful mint must trigger one replacement cleanup");
  assert(calls[2].url === "/v1/operator/reload-revoke", "replacement must use exact revocation route");
  assert(JSON.parse(calls[2].options.body).ticket === replacementTicket, "stale replacement must be revoked");
  assert(calls[2].options.headers.Authorization === "Bearer bearer-main", "stale cleanup must use captured page-memory bearer");
  assert(!storage.has(key), "stale mint response must never repopulate storage");
  assert(reloadAuthState.token === null, "stale mint response must never restore bearer state");

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
