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


def test_report_viewer_is_packaged_and_uses_safe_authenticated_rendering() -> None:
    html = _asset_text("/ui/")
    javascript = _asset_text("/ui/report.js")

    assert 'id="report-content"' in html
    assert 'id="report-metadata"' in html
    assert '<script src="/ui/report.js" defer></script>' in html
    assert "/v1/sessions/${encodeURIComponent(sessionId)}/report" in javascript
    assert "Authorization" in javascript
    assert "Bearer ${reportState.token}" in javascript
    assert ".textContent" in javascript
    assert "JSON.stringify(payload.report, null, 2)" in javascript
    assert "MutationObserver" in javascript
    assert 'reportById("session-status")' in javascript
    assert "innerHTML" not in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert "document.cookie" not in javascript
    assert "window.location" not in javascript


def test_report_viewer_asset_allowlist_remains_exact() -> None:
    assert load_ui_asset("/ui/report.js") is not None
    assert load_ui_asset("/ui/report.js/../protocol.py") is None
    assert load_ui_asset("/ui/report.json") is None


def test_packaged_report_viewer_has_valid_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; JavaScript syntax check requires an available node binary")

    asset = files("harness_x.app_server").joinpath("ui", "report.js")
    with as_file(asset) as path:
        completed = subprocess.run(
            [node, "--check", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr
