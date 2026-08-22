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


def test_report_export_client_uses_authenticated_exact_byte_download_without_storage() -> None:
    html = _asset_text("/ui/")
    javascript = _asset_text("/ui/report_export.js")
    app = _asset_text("/ui/app.js")
    report = _asset_text("/ui/report.js")
    bootstrap = _asset_text("/ui/bootstrap.js")

    assert 'id="download-report"' in html
    assert 'id="report-export-status"' in html
    assert '<script src="/ui/report_export.js" defer></script>' in html
    assert (
        html.index("/ui/report.js")
        < html.index("/ui/report_export.js")
        < html.index("/ui/app.js")
        < html.index("/ui/bootstrap.js")
    )

    assert "/report/export" in javascript
    assert "Authorization" in javascript
    assert "Bearer ${reportExportState.token}" in javascript
    assert 'cache: "no-store"' in javascript
    assert 'credentials: "omit"' in javascript
    assert "response.arrayBuffer()" in javascript
    assert 'crypto.subtle.digest("SHA-256", bytes)' in javascript
    assert 'response.headers.get("Content-Length")' in javascript
    assert 'response.headers.get("X-Harness-X-Report-SHA256")' in javascript
    assert 'response.headers.get("X-Harness-X-Report-Attestation")' in javascript
    assert 'response.headers.get("X-Harness-X-Artifact-Event-Hash")' in javascript
    assert 'link.download = "coding-task-report.json"' in javascript
    assert "URL.createObjectURL(blob)" in javascript
    assert "URL.revokeObjectURL(objectUrl)" in javascript
    assert javascript.index("URL.createObjectURL(blob)") < javascript.index("URL.revokeObjectURL(objectUrl)")
    assert ".textContent" in javascript

    assert "new AbortController()" in javascript
    assert "controller.signal" in javascript
    assert "reportExportState.generation" in javascript
    assert "reportExportIsCurrent(sessionId, generation)" in javascript
    assert "cancelReportExport()" in javascript
    assert 'window.addEventListener("beforeunload", cancelReportExport)' in javascript

    assert 'reportExportById("auth-form").addEventListener("submit"' in javascript
    assert 'reportById("auth-form").addEventListener("submit"' in report
    assert 'byId("auth-form").addEventListener("submit"' in app
    assert 'authForm.requestSubmit()' in bootstrap

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "innerHTML",
        "window.location.search",
        "?path=",
    ):
        assert forbidden not in javascript


def test_report_export_asset_allowlist_remains_exact() -> None:
    assert load_ui_asset("/ui/report_export.js") is not None
    assert load_ui_asset("/ui/report_export.js/../protocol.py") is None
    assert load_ui_asset("/ui/report-export.js") is None
    assert load_ui_asset("/ui/../../etc/passwd") is None


def test_packaged_report_export_client_has_valid_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; JavaScript syntax check requires an available node binary")

    asset = files("harness_x.app_server").joinpath("ui", "report_export.js")
    with as_file(asset) as path:
        completed = subprocess.run(
            [node, "--check", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr
