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


def test_selection_restore_is_packaged_after_app_before_stream_recovery() -> None:
    html = _asset_text("/ui/")
    javascript = _asset_text("/ui/selection_restore.js")

    assert "selected session id" in html
    assert "sessionStorage" in html
    assert '<script src="/ui/selection_restore.js" defer></script>' in html
    assert (
        html.index("/ui/reload_auth.js")
        < html.index("/ui/app.js")
        < html.index("/ui/selection_restore.js")
        < html.index("/ui/stream_recovery.js")
        < html.index("/ui/bootstrap.js")
    )
    assert "selectSession = async function selectSessionWithReloadRestore" in javascript
    assert "unlockOperator = async function unlockOperatorWithSelectionRestore" in javascript


def test_selection_restore_storage_and_authority_boundaries_are_explicit() -> None:
    javascript = _asset_text("/ui/selection_restore.js")
    app = _asset_text("/ui/app.js")

    assert 'operatorSelectionStorageKey = "harness-x.operator.selected-session.v1"' in javascript
    assert "operatorSelectionIdPattern = /^app_[0-9a-f]{32}$/" in javascript
    assert "sessionStorage.getItem(operatorSelectionStorageKey)" in javascript
    assert "sessionStorage.setItem(operatorSelectionStorageKey, sessionId)" in javascript
    assert "sessionStorage.removeItem(operatorSelectionStorageKey)" in javascript
    assert 'window.location.hash.startsWith("#bootstrap=")' in javascript
    assert "if (operatorSelectionBootstrapPresentAtLoad) clearStoredOperatorSelection();" in javascript
    assert "await unlockOperatorBeforeSelectionRestore(token);" in javascript
    assert "if (!state.sessions.has(sessionId))" in javascript
    assert "await selectSession(sessionId);" in javascript
    assert 'byId("lock-button").addEventListener("click"' in javascript

    for forbidden in (
        "localStorage",
        "document.cookie",
        "indexedDB",
        "caches.open",
        "serviceWorker",
        "BroadcastChannel",
        "SharedWorker",
        "Authorization",
        "access_token",
        "reload-ticket",
        "?after=",
        "cursor",
        "workspace_root",
        "task-text",
    ):
        assert forbidden not in javascript

    assert 'api(`/v1/sessions/${encoded}`)' in app
    assert 'api(`/v1/sessions/${encoded}/events?after=0&limit=1000`)' in app
    assert 'api(`/v1/sessions/${encoded}/trace?after=0&limit=1000`)' in app
    assert "projectPageCursor(" in app
    assert "state.selectionGeneration" in app
    assert "startStreams(sessionId, cursors.eventAfter, cursors.traceAfter, generation);" in app


def test_selection_restore_asset_allowlist_remains_exact() -> None:
    assert load_ui_asset("/ui/selection_restore.js") is not None
    assert load_ui_asset("/ui/selection-restore.js") is None
    assert load_ui_asset("/ui/selection_restore.js/../protocol.py") is None
    assert load_ui_asset("/ui/../../etc/passwd") is None


def test_packaged_selection_restore_client_has_valid_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; selection restore syntax check requires node")

    asset = files("harness_x.app_server").joinpath("ui", "selection_restore.js")
    with as_file(asset) as path:
        completed = subprocess.run(
            [node, "--check", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr


def test_selection_restore_behavior_in_node_when_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; selection restore behavior test requires node")

    asset = files("harness_x.app_server").joinpath("ui", "selection_restore.js")
    with as_file(asset) as path:
        harness = r'''
const fs = require("fs");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}
const source = fs.readFileSync(process.argv[1], "utf8");
const key = "harness-x.operator.selected-session.v1";
const sessionA = "app_" + "a".repeat(32);
const sessionB = "app_" + "b".repeat(32);

function build(hash = "", initial = []) {
  const storage = new Map(initial);
  const lock = {
    handlers: [],
    addEventListener(type, handler) {
      if (type === "click") this.handlers.push(handler);
    },
  };
  const state = { sessions: new Map() };
  const selectCalls = [];
  const unlockCalls = [];
  const context = {
    window: { location: { hash } },
    sessionStorage: {
      getItem(name) { return storage.has(name) ? storage.get(name) : null; },
      setItem(name, value) { storage.set(name, String(value)); },
      removeItem(name) { storage.delete(name); },
    },
    console: { warn() {} },
    state,
    byId(id) {
      if (id !== "lock-button") throw new Error(`unexpected element ${id}`);
      return lock;
    },
    selectSession: async (sessionId) => { selectCalls.push(sessionId); },
    unlockOperator: async (token) => {
      unlockCalls.push(token);
      if (context.failUnlock) throw new Error("unlock failed");
      state.sessions.clear();
      for (const sessionId of context.nextSessions || []) state.sessions.set(sessionId, {});
    },
    failUnlock: false,
    nextSessions: [],
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: process.argv[1] });
  return { context, storage, lock, state, selectCalls, unlockCalls };
}

(async () => {
  const ordinary = build();
  await ordinary.context.selectSession(sessionA);
  assert(ordinary.storage.get(key) === sessionA, "ordinary canonical selection must persist id");
  assert(ordinary.selectCalls.at(-1) === sessionA, "ordinary selection must delegate to frozen path");

  await ordinary.context.selectSession("not-a-session");
  assert(ordinary.storage.get(key) === sessionA, "malformed ordinary input must not overwrite stored canonical id");
  assert(ordinary.selectCalls.at(-1) === "not-a-session", "wrapper must not become selection authority");

  ordinary.lock.handlers[0]();
  assert(!ordinary.storage.has(key), "explicit lock event must clear stored selection");

  const malformed = build("", [[key, "bad"]]);
  malformed.context.nextSessions = [sessionA];
  await malformed.context.unlockOperator("bearer");
  assert(!malformed.storage.has(key), "malformed stored id must be removed");
  assert(malformed.selectCalls.length === 0, "malformed stored id must not trigger selection request");

  const missing = build("", [[key, sessionB]]);
  missing.context.nextSessions = [sessionA];
  await missing.context.unlockOperator("bearer");
  assert(!missing.storage.has(key), "missing authoritative session must clear stored id");
  assert(missing.selectCalls.length === 0, "missing authoritative session must not be selected");

  const restored = build("", [[key, sessionA]]);
  restored.context.nextSessions = [sessionA, sessionB];
  await restored.context.unlockOperator("bearer");
  assert(restored.unlockCalls.length === 1, "frozen unlock must run exactly once");
  assert(restored.selectCalls.length === 1 && restored.selectCalls[0] === sessionA, "valid stored id must reuse frozen selectSession path");
  assert(restored.storage.get(key) === sessionA, "successful restore keeps canonical selection hint");

  const failedUnlock = build("", [[key, sessionA]]);
  failedUnlock.context.failUnlock = true;
  let failed = false;
  try {
    await failedUnlock.context.unlockOperator("bad-bearer");
  } catch (_error) {
    failed = true;
  }
  assert(failed, "underlying unlock failure must propagate");
  assert(failedUnlock.selectCalls.length === 0, "failed unlock must never trigger selection restore");

  const bootstrap = build("#bootstrap=" + "A".repeat(43), [[key, sessionA]]);
  assert(!bootstrap.storage.has(key), "fresh bootstrap must clear stale stored selection at load");
  bootstrap.context.nextSessions = [sessionA];
  await bootstrap.context.unlockOperator("bootstrap-bearer");
  assert(bootstrap.selectCalls.length === 0, "fresh bootstrap unlock must suppress selection restore");

  const unavailable = build();
  unavailable.context.sessionStorage.getItem = () => { throw new Error("blocked"); };
  unavailable.context.sessionStorage.setItem = () => { throw new Error("blocked"); };
  unavailable.context.sessionStorage.removeItem = () => { throw new Error("blocked"); };
  unavailable.context.nextSessions = [sessionA];
  await unavailable.context.selectSession(sessionA);
  await unavailable.context.unlockOperator("bearer");
  assert(unavailable.selectCalls.length === 1, "storage failure must leave ordinary manual selection usable");
  assert(unavailable.unlockCalls.length === 1, "storage failure must leave authentication usable");

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
