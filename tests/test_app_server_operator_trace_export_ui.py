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


def test_trace_export_client_is_terminal_authenticated_and_storage_free() -> None:
    html = _asset_text("/ui/")
    javascript = _asset_text("/ui/trace_export.js")
    app = _asset_text("/ui/app.js")
    bootstrap = _asset_text("/ui/bootstrap.js")

    assert 'id="download-trace"' in html
    assert 'id="trace-export-status"' in html
    assert '<script src="/ui/trace_export.js" defer></script>' in html
    assert (
        html.index("/ui/report_export.js")
        < html.index("/ui/trace_export.js")
        < html.index("/ui/app.js")
        < html.index("/ui/bootstrap.js")
    )

    assert "/trace/export" in javascript
    assert "Authorization" in javascript
    assert "Bearer ${traceExportState.token}" in javascript
    assert 'Accept: "application/x-ndjson"' in javascript
    assert 'cache: "no-store"' in javascript
    assert 'credentials: "omit"' in javascript
    assert "new AbortController()" in javascript
    assert "traceExportState.generation" in javascript
    assert "traceExportTerminalStates" in javascript
    assert 'new Set(["succeeded", "failed", "cancelled"])' in javascript
    assert "response.arrayBuffer()" in javascript
    assert 'crypto.subtle.digest("SHA-256", bytes)' in javascript
    assert 'response.headers.get("Content-Length")' in javascript
    assert 'response.headers.get("X-Harness-X-Trace-ID")' in javascript
    assert 'response.headers.get("X-Harness-X-Trace-SHA256")' in javascript
    assert 'response.headers.get("X-Harness-X-Trace-Records")' in javascript
    assert 'response.headers.get("X-Harness-X-Trace-Final-Event-Hash")' in javascript
    assert 'response.headers.get("X-Harness-X-Trace-Attachment-Event-Hash")' in javascript
    assert 'link.download = "causal-trace.jsonl"' in javascript
    assert "URL.createObjectURL(blob)" in javascript
    assert "URL.revokeObjectURL(objectUrl)" in javascript
    assert javascript.index("URL.createObjectURL(blob)") < javascript.index("URL.revokeObjectURL(objectUrl)")
    assert 'traceExportById("auth-form").addEventListener("submit"' in javascript
    assert 'byId("auth-form").addEventListener("submit"' in app
    assert 'authForm.requestSubmit()' in bootstrap
    assert 'traceExportById("lock-button").addEventListener("click"' in javascript
    assert 'window.addEventListener("beforeunload", cancelTraceExport)' in javascript
    assert ".textContent" in javascript

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "innerHTML",
        "window.location.search",
        "?path=",
    ):
        assert forbidden not in javascript


def test_trace_export_asset_allowlist_remains_exact() -> None:
    assert load_ui_asset("/ui/trace_export.js") is not None
    assert load_ui_asset("/ui/trace_export.js/../protocol.py") is None
    assert load_ui_asset("/ui/trace-export.js") is None
    assert load_ui_asset("/ui/../../etc/passwd") is None


def test_packaged_trace_export_client_has_valid_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; JavaScript syntax check requires an available node binary")

    asset = files("harness_x.app_server").joinpath("ui", "trace_export.js")
    with as_file(asset) as path:
        completed = subprocess.run(
            [node, "--check", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr
