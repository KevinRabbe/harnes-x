from __future__ import annotations

import json
import shutil
import subprocess
from importlib.resources import as_file, files

import pytest

from harness_x.app_server.ui_assets import load_ui_asset


def _asset_text(path: str) -> str:
    asset = load_ui_asset(path)
    assert asset is not None
    return asset[1].decode("utf-8")


def test_stream_recovery_asset_and_button_are_packaged_after_app_before_bootstrap() -> None:
    html = _asset_text("/ui/")
    recovery = _asset_text("/ui/stream_recovery.js")

    assert 'id="reconnect-streams"' in html
    assert "Reconnect live streams" in html
    assert 'id="stream-recovery-status"' in html
    assert '<script src="/ui/stream_recovery.js" defer></script>' in html
    assert (
        html.index('/ui/app.js')
        < html.index('/ui/stream_recovery.js')
        < html.index('/ui/bootstrap.js')
    )
    assert "scheduleReconnect = function scheduleReconnectWithManualRecovery" in recovery
    assert "abortStreams = function abortStreamsWithRecoveryReset" in recovery


def test_stream_recovery_preserves_m37_policy_and_fail_closed_boundaries() -> None:
    policy = _asset_text("/ui/stream_policy.js")
    app = _asset_text("/ui/app.js")
    recovery = _asset_text("/ui/stream_recovery.js")

    assert "Object.freeze([250, 500, 1000, 2000, 4000])" in policy
    assert "maxReconnectAttempts: reconnectDelaysMs.length" in policy
    assert "return [400, 401, 403, 404].includes(error && error.status);" in app
    assert app.count("if (nonRetriableStreamError(error))") == 2
    assert "if (traceCorrupt) return;" in app
    assert "currentCursor = streamPolicy.advanceCursor" in app

    assert "const delay = streamPolicy.reconnectDelayMs(consecutiveFailures - 1);" in recovery
    assert "if (delay == null && selectionIsCurrent(sessionId, generation))" in recovery
    assert "Number.isSafeInteger(cursor)" in recovery
    assert "runLifecycleStream(sessionId, lifecycle.cursor, generation, 0)" in recovery
    assert "runTraceStream(sessionId, trace.cursor, generation, 0)" in recovery
    assert "const snapshot = await api(`/v1/sessions/${encodeURIComponent(sessionId)}`);" in recovery
    assert "if (!selectionIsCurrent(sessionId, generation)) return;" in recovery
    assert "streamPolicy.isTerminalStatus(snapshot.status)" in recovery
    assert "nonRetriableStreamError(error)" in recovery

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "window.location",
        "?after=0",
        "EventSource(",
    ):
        assert forbidden not in recovery


def test_stream_recovery_asset_allowlist_remains_exact() -> None:
    assert load_ui_asset("/ui/stream_recovery.js") is not None
    assert load_ui_asset("/ui/stream-recovery.js") is None
    assert load_ui_asset("/ui/stream_recovery.js/../protocol.py") is None
    assert load_ui_asset("/ui/../../etc/passwd") is None


def test_stream_recovery_client_has_valid_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; JavaScript syntax check requires an available node binary")

    asset = files("harness_x.app_server").joinpath("ui", "stream_recovery.js")
    with as_file(asset) as path:
        completed = subprocess.run(
            [node, "--check", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr


def test_stream_recovery_behavior_in_node_when_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; stream recovery behavior test requires node")

    asset = files("harness_x.app_server").joinpath("ui", "stream_recovery.js")
    with as_file(asset) as path:
        harness = r'''
const fs = require("fs");
const vm = require("vm");

class FakeClassList {
  constructor(initial = []) { this.values = new Set(initial); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  toggle(value, force) {
    if (force === true) this.values.add(value);
    else if (force === false) this.values.delete(value);
    else if (this.values.has(value)) this.values.delete(value);
    else this.values.add(value);
  }
  contains(value) { return this.values.has(value); }
}

const elements = new Map();
function makeElement(id) {
  const element = {
    id,
    disabled: false,
    textContent: "",
    classList: new FakeClassList(id === "reconnect-streams" ? ["hidden"] : []),
    handlers: new Map(),
    addEventListener(type, handler) { this.handlers.set(type, handler); },
  };
  elements.set(id, element);
  return element;
}
for (const id of [
  "reconnect-streams",
  "stream-recovery-status",
  "lifecycle-state",
  "trace-state",
]) makeElement(id);

globalThis.byId = (id) => {
  if (!elements.has(id)) makeElement(id);
  return elements.get(id);
};
const sessionA = "app_" + "a".repeat(32);
const sessionB = "app_" + "b".repeat(32);
globalThis.state = {
  token: "bearer",
  selectedSessionId: sessionA,
  selectionGeneration: 0,
  sessions: new Map([[sessionA, { session_id: sessionA, status: "running" }]]),
};
globalThis.streamPolicy = {
  reconnectDelayMs(index) { return [250, 500, 1000, 2000, 4000][index] ?? null; },
  isTerminalStatus(status) { return ["succeeded", "failed", "cancelled"].includes(String(status)); },
};
globalThis.selectionIsCurrent = (sessionId, generation) => (
  state.selectedSessionId === sessionId && state.selectionGeneration === generation
);
globalThis.streamPillId = (kind) => kind === "lifecycle" ? "lifecycle-state" : "trace-state";
globalThis.setPill = (id, text) => { byId(id).textContent = text; };
let scheduleCalls = 0;
globalThis.scheduleReconnect = function originalScheduleReconnect(
  kind, sessionId, cursor, generation, consecutiveFailures, restart
) {
  scheduleCalls += 1;
  if (!selectionIsCurrent(sessionId, generation)) return;
  const delay = streamPolicy.reconnectDelayMs(consecutiveFailures - 1);
  if (delay == null) setPill(streamPillId(kind), "Disconnected", "failed");
};
let abortCalls = 0;
globalThis.abortStreams = function originalAbortStreams() {
  abortCalls += 1;
  setPill("lifecycle-state", "Idle", "muted");
  setPill("trace-state", "Idle", "muted");
};
let apiCalls = 0;
let apiImpl = async () => ({ session_id: state.selectedSessionId, status: "running" });
globalThis.api = async (...args) => { apiCalls += 1; return apiImpl(...args); };
globalThis.renderSnapshot = (snapshot) => { state.sessions.set(snapshot.session_id, snapshot); };
globalThis.renderSessions = () => {};
const lifecycleCalls = [];
const traceCalls = [];
globalThis.runLifecycleStream = async (...args) => { lifecycleCalls.push(args); };
globalThis.runTraceStream = async (...args) => { traceCalls.push(args); };
globalThis.nonRetriableStreamError = (error) => [400, 401, 403, 404].includes(error && error.status);
globalThis.messageFromError = (error) => error instanceof Error ? error.message : String(error);
let unloadHandler = null;
globalThis.window = {
  addEventListener(type, handler) { if (type === "beforeunload") unloadHandler = handler; },
};

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), { filename: process.argv[1] });

function assert(condition, message) {
  if (!condition) throw new Error(message);
}
function exhausted(kind, cursor) {
  scheduleReconnect(kind, state.selectedSessionId, cursor, state.selectionGeneration, 6, () => {});
}

(async () => {
  const button = byId("reconnect-streams");

  exhausted("lifecycle", 7);
  assert(scheduleCalls === 1, "wrapped schedule must preserve original reconnect call");
  assert(!button.classList.contains("hidden") && !button.disabled, "exhausted lifecycle must become recoverable");
  await recoverDisconnectedStreams();
  assert(lifecycleCalls.length === 1, "lifecycle recovery must restart one lifecycle stream");
  assert(lifecycleCalls[0][1] === 7 && lifecycleCalls[0][3] === 0, "lifecycle must resume exact cursor with failure count zero");
  assert(traceCalls.length === 0, "unaffected trace stream must not restart");

  exhausted("lifecycle", 8);
  exhausted("trace", 4);
  await recoverDisconnectedStreams();
  assert(lifecycleCalls.at(-1)[1] === 8, "lifecycle must retain its independent cursor");
  assert(traceCalls.at(-1)[1] === 4 && traceCalls.at(-1)[3] === 0, "trace must retain its independent cursor and reset failures");

  exhausted("lifecycle", 9);
  let resolveApi;
  apiImpl = () => new Promise((resolve) => { resolveApi = resolve; });
  const apiBeforeDuplicate = apiCalls;
  const first = recoverDisconnectedStreams();
  const second = recoverDisconnectedStreams();
  assert(apiCalls === apiBeforeDuplicate + 1, "duplicate click while in flight must not start another refresh");
  resolveApi({ session_id: sessionA, status: "running" });
  await Promise.all([first, second]);
  assert(lifecycleCalls.at(-1)[1] === 9, "single in-flight recovery must retain cursor");

  exhausted("lifecycle", 10);
  let resolveStale;
  apiImpl = () => new Promise((resolve) => { resolveStale = resolve; });
  const lifecycleBeforeStale = lifecycleCalls.length;
  const stale = recoverDisconnectedStreams();
  state.selectedSessionId = sessionB;
  state.selectionGeneration = 1;
  state.sessions.set(sessionB, { session_id: sessionB, status: "running" });
  resolveStale({ session_id: sessionA, status: "running" });
  await stale;
  assert(lifecycleCalls.length === lifecycleBeforeStale, "stale generation must not restart a stream");

  abortStreams();
  state.selectedSessionId = sessionB;
  state.selectionGeneration = 2;
  state.sessions.set(sessionB, { session_id: sessionB, status: "running" });
  exhausted("trace", 5);
  apiImpl = async () => ({ session_id: sessionB, status: "succeeded" });
  const traceBeforeTerminal = traceCalls.length;
  await recoverDisconnectedStreams();
  assert(traceCalls.length === traceBeforeTerminal, "terminal refresh must not restart trace");
  assert(byId("lifecycle-state").textContent === "Closed", "terminal refresh must close lifecycle pill");
  assert(byId("trace-state").textContent === "Closed", "terminal refresh must close trace pill");

  state.sessions.set(sessionB, { session_id: sessionB, status: "running" });
  exhausted("lifecycle", 12);
  apiImpl = async () => { const error = new Error("unauthorized"); error.status = 401; throw error; };
  await recoverDisconnectedStreams();
  const apiBeforeRejectedRetry = apiCalls;
  await recoverDisconnectedStreams();
  assert(apiCalls === apiBeforeRejectedRetry, "non-retriable recovery refresh error must clear eligibility");

  exhausted("lifecycle", 13);
  assert(typeof unloadHandler === "function", "M46 must register unload cleanup");
  unloadHandler();
  apiImpl = async () => ({ session_id: sessionB, status: "running" });
  const apiBeforeUnloadRetry = apiCalls;
  await recoverDisconnectedStreams();
  assert(apiCalls === apiBeforeUnloadRetry, "unload cleanup must clear recovery eligibility");

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
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"
