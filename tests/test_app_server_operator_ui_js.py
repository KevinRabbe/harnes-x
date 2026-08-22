from __future__ import annotations

import json
import shutil
import subprocess
from importlib.resources import as_file, files

import pytest


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; JavaScript qualification requires an available node binary")
    return node


def test_packaged_operator_ui_javascript_has_valid_syntax_when_node_is_available() -> None:
    node = _node()
    ui_root = files("harness_x.app_server").joinpath("ui")
    for filename in ("stream_policy.js", "app.js"):
        asset = ui_root.joinpath(filename)
        with as_file(asset) as path:
            completed = subprocess.run(
                [node, "--check", str(path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        assert completed.returncode == 0, completed.stderr


def test_packaged_stream_reconnect_policy_is_bounded_and_cursor_strict() -> None:
    node = _node()
    asset = files("harness_x.app_server").joinpath("ui", "stream_policy.js")
    script = r"""
const fs = require("fs");
const vm = require("vm");
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), { filename: process.argv[1] });
const policy = globalThis.HarnessXStreamPolicy;
function throws(fn) {
  try { fn(); return false; } catch (_error) { return true; }
}
console.log(JSON.stringify({
  maxReconnectAttempts: policy.maxReconnectAttempts,
  delays: [0, 1, 2, 3, 4, 5].map((index) => policy.reconnectDelayMs(index)),
  firstCursor: policy.advanceCursor("1", 1, 0),
  nextCursor: policy.advanceCursor("2", 2, 1),
  mismatchedIdRejected: throws(() => policy.advanceCursor("2", 3, 1)),
  gapRejected: throws(() => policy.advanceCursor("4", 4, 2)),
  duplicateRejected: throws(() => policy.advanceCursor("2", 2, 2)),
  invalidCurrentRejected: throws(() => policy.advanceCursor("1", 1, -1)),
  terminal: ["succeeded", "failed", "cancelled"].map((value) => policy.isTerminalStatus(value)),
  nonterminal: ["created", "running", "cancel_requested"].map((value) => policy.isTerminalStatus(value)),
}));
"""
    with as_file(asset) as path:
        completed = subprocess.run(
            [node, "-e", script, str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result == {
        "maxReconnectAttempts": 5,
        "delays": [250, 500, 1000, 2000, 4000, None],
        "firstCursor": 1,
        "nextCursor": 2,
        "mismatchedIdRejected": True,
        "gapRejected": True,
        "duplicateRejected": True,
        "invalidCurrentRejected": True,
        "terminal": [True, True, True],
        "nonterminal": [False, False, False],
    }
