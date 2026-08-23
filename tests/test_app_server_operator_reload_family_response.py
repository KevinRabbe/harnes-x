from __future__ import annotations

import shutil
import subprocess
from importlib.resources import as_file, files

import pytest


def test_invalid_successful_family_issue_response_retires_family_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; reload family response test requires node")

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
const family = "Q".repeat(43);
const possibleTicket = "V".repeat(43);
const storage = new Map([[familyKey, family]]);
globalThis.sessionStorage = {
  getItem(name) { return storage.has(name) ? storage.get(name) : null; },
  setItem(name, value) { storage.set(name, String(value)); },
  removeItem(name) { storage.delete(name); },
};
globalThis.crypto = { getRandomValues(bytes) { bytes.fill(3); return bytes; } };
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

const calls = [];
globalThis.fetch = async (url, options) => {
  calls.push({
    url,
    options,
    ticket: storage.get(ticketKey) ?? null,
    family: storage.get(familyKey) ?? null,
  });
  if (url === "/v1/operator/reload-family-ticket") {
    return {
      ok: true,
      status: 200,
      async json() {
        return { schema_version: "wrong-schema", ticket: possibleTicket };
      },
    };
  }
  if (url === "/v1/operator/reload-revoke" || url === "/v1/operator/reload-family-revoke") {
    return { ok: true, status: 204 };
  }
  throw new Error("unexpected fetch");
};

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), { filename: process.argv[1] });

(async () => {
  elements.get("token").value = "bearer-main";
  elements.get("auth-form").handlers.get("submit")({ preventDefault() {} });
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  assert(calls.length === 3, "invalid successful response must trigger ticket and family cleanup");
  assert(calls[0].url === "/v1/operator/reload-family-ticket", "first call must be family issuance");
  assert(calls[1].url === "/v1/operator/reload-revoke", "known possible ticket must receive M50 cleanup");
  assert(JSON.parse(calls[1].options.body).ticket === possibleTicket, "ticket cleanup must target returned possible ticket");
  assert(calls[2].url === "/v1/operator/reload-family-revoke", "invalid successful response must retire family");
  assert(JSON.parse(calls[2].options.body).family === family, "family cleanup must target issuance family");
  assert(!storage.has(ticketKey), "invalid response must not persist ticket");
  assert(!storage.has(familyKey), "invalid successful response must remove family locally");
  assert(reloadAuthState.token === "bearer-main", "credential failure must not lock ordinary bearer authority");

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
