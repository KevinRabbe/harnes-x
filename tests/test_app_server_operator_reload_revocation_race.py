from __future__ import annotations

import shutil
import subprocess
from importlib.resources import as_file, files

import pytest


def test_lock_revokes_family_and_successful_stale_inflight_mint_when_node_is_available() -> None:
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
const familyKey = "harness-x.operator.reload-family.v1";
const oldTicket = "T".repeat(43);
const replacementTicket = "U".repeat(43);
const family = "F".repeat(43);
const storage = new Map([[key, oldTicket], [familyKey, family]]);
globalThis.sessionStorage = {
  getItem(name) { return storage.has(name) ? storage.get(name) : null; },
  setItem(name, value) { storage.set(name, String(value)); },
  removeItem(name) { storage.delete(name); },
};
globalThis.crypto = { getRandomValues(bytes) { bytes.fill(9); return bytes; } };
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
globalThis.setTimeout = () => 1;
globalThis.clearTimeout = () => {};
globalThis.console = { warn() {} };

let resolveMint;
const calls = [];
globalThis.fetch = (url, options) => {
  calls.push({
    url,
    options,
    ticket: storage.get(key) ?? null,
    family: storage.get(familyKey) ?? null,
  });
  if (url === "/v1/operator/reload-family-ticket") {
    return new Promise((resolve) => { resolveMint = resolve; });
  }
  if (url === "/v1/operator/reload-revoke" || url === "/v1/operator/reload-family-revoke") {
    return Promise.resolve({ ok: true, status: 204 });
  }
  return Promise.reject(new Error("unexpected fetch"));
};

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), { filename: process.argv[1] });

(async () => {
  const form = elements.get("auth-form");
  elements.get("token").value = "bearer-main";
  form.handlers.get("submit")({ preventDefault() {} });
  assert(calls.length === 1 && calls[0].url === "/v1/operator/reload-family-ticket", "auth must start family mint");
  const mintBody = JSON.parse(calls[0].options.body);
  assert(mintBody.previous_ticket === oldTicket, "mint must rotate old ticket");
  assert(mintBody.family === family, "mint must bind the pre-existing family");

  elements.get("lock-button").handlers.get("click")({ preventDefault() {} });
  assert(!storage.has(key), "lock must clear old ticket before cleanup");
  assert(!storage.has(familyKey), "lock must clear family before cleanup");
  assert(reloadAuthState.token === null, "lock must clear bearer state");
  assert(calls.length === 3, "lock must start known-ticket and family cleanup");
  assert(calls[1].url === "/v1/operator/reload-revoke", "first cleanup must retain M50 ticket route");
  assert(JSON.parse(calls[1].options.body).ticket === oldTicket, "ticket cleanup must target old ticket");
  assert(calls[1].ticket === null && calls[1].family === null, "ticket cleanup starts after local clear");
  assert(calls[2].url === "/v1/operator/reload-family-revoke", "lock must revoke the entire family");
  assert(JSON.parse(calls[2].options.body).family === family, "family cleanup must target pre-mint family");
  assert(calls[2].ticket === null && calls[2].family === null, "family cleanup starts after local clear");

  resolveMint({
    ok: true,
    status: 200,
    async json() {
      return { schema_version: "app-operator-reload-family-ticket-v1", ticket: replacementTicket };
    },
  });
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  assert(calls.length === 5, "stale successful mint must retain belt-and-suspenders ticket/family cleanup");
  assert(calls[3].url === "/v1/operator/reload-revoke", "stale returned ticket must still use M50 cleanup");
  assert(JSON.parse(calls[3].options.body).ticket === replacementTicket, "stale returned ticket must be revoked");
  assert(calls[4].url === "/v1/operator/reload-family-revoke", "stale mint must also retire its family");
  assert(JSON.parse(calls[4].options.body).family === family, "stale family cleanup must use original family");
  assert(calls[3].options.headers.Authorization === "Bearer bearer-main", "stale ticket cleanup must use captured bearer");
  assert(calls[4].options.headers.Authorization === "Bearer bearer-main", "stale family cleanup must use captured bearer");
  assert(!storage.has(key), "stale mint response must never repopulate ticket storage");
  assert(!storage.has(familyKey), "stale mint response must never repopulate family storage");
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
