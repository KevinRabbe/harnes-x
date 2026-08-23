from __future__ import annotations

import shutil
import subprocess
from importlib.resources import as_file, files

import pytest


def test_stale_same_family_response_schedules_one_reconciliation_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; reload family overlap test requires node")

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
const oldTicket = "T".repeat(43);
const newerTicket = "B".repeat(43);
const staleTicket = "A".repeat(43);
const reconciledTicket = "C".repeat(43);
const family = "F".repeat(43);
const storage = new Map([[ticketKey, oldTicket], [familyKey, family]]);
globalThis.sessionStorage = {
  getItem(name) { return storage.has(name) ? storage.get(name) : null; },
  setItem(name, value) { storage.set(name, String(value)); },
  removeItem(name) { storage.delete(name); },
};
globalThis.crypto = { getRandomValues(bytes) { bytes.fill(4); return bytes; } };
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
globalThis.console = { warn() {} };

let timerId = 0;
const timers = new Map();
globalThis.setTimeout = (callback, delay) => {
  timerId += 1;
  timers.set(timerId, { callback, delay });
  return timerId;
};
globalThis.clearTimeout = (id) => timers.delete(id);

const deferred = [];
const calls = [];
globalThis.fetch = (url, options) => {
  calls.push({ url, options });
  if (url === "/v1/operator/reload-family-ticket") {
    return new Promise((resolve) => deferred.push(resolve));
  }
  if (url === "/v1/operator/reload-revoke") {
    return Promise.resolve({ ok: true, status: 204 });
  }
  return Promise.reject(new Error("unexpected fetch"));
};

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), { filename: process.argv[1] });

(async () => {
  reloadAuthState.token = "bearer-main";
  const first = mintReloadCapability("bearer-main", family);
  const second = mintReloadCapability("bearer-main", family);
  assert(calls.length === 2, "two overlapping mints must be in flight");

  deferred[1]({
    ok: true,
    status: 200,
    async json() {
      return { schema_version: "app-operator-reload-family-ticket-v1", ticket: newerTicket };
    },
  });
  await second;
  assert(storage.get(ticketKey) === newerTicket, "newer browser generation must store its ticket");

  deferred[0]({
    ok: true,
    status: 200,
    async json() {
      return { schema_version: "app-operator-reload-family-ticket-v1", ticket: staleTicket };
    },
  });
  await first;
  await new Promise(setImmediate);

  const staleRevokes = calls.filter((call) => call.url === "/v1/operator/reload-revoke");
  assert(staleRevokes.length === 1, "stale returned ticket must receive exact cleanup");
  assert(JSON.parse(staleRevokes[0].options.body).ticket === staleTicket, "stale cleanup must target exact returned ticket");
  assert(storage.get(familyKey) === family, "stale overlap must not retire current family");

  const zeroTimers = [...timers.entries()].filter(([, item]) => item.delay === 0);
  assert(zeroTimers.length === 1, "stale same-family response must schedule one immediate reconciliation");
  const [reconcileTimerId, reconcileTimer] = zeroTimers[0];
  timers.delete(reconcileTimerId);
  reconcileTimer.callback();
  assert(calls.length === 4, "reconciliation must start one additional family issuance after ticket cleanup");
  const reconcileCall = calls[3];
  assert(reconcileCall.url === "/v1/operator/reload-family-ticket", "reconciliation must use family issuance route");
  const reconcileBody = JSON.parse(reconcileCall.options.body);
  assert(reconcileBody.previous_ticket === newerTicket, "reconciliation must rotate browser-current ticket");
  assert(reconcileBody.family === family, "reconciliation must stay in current family");

  deferred[2]({
    ok: true,
    status: 200,
    async json() {
      return { schema_version: "app-operator-reload-family-ticket-v1", ticket: reconciledTicket };
    },
  });
  await new Promise(setImmediate);
  await new Promise(setImmediate);
  assert(storage.get(ticketKey) === reconciledTicket, "reconciliation must restore one browser-current ticket");
  assert(storage.get(familyKey) === family, "reconciliation must preserve family");
  assert([...timers.values()].some((item) => item.delay === 120000), "reconciliation success must return to normal renewal cadence");

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
