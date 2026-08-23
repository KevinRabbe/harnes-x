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


def test_snapshot_export_client_is_terminal_authenticated_hashed_and_storage_free() -> None:
    html = _asset_text("/ui/")
    javascript = _asset_text("/ui/snapshot_export.js")
    app = _asset_text("/ui/app.js")
    bootstrap = _asset_text("/ui/bootstrap.js")

    assert 'id="download-session-snapshot"' in html
    assert "Download session snapshot" in html
    assert 'id="snapshot-export-status"' in html
    assert '<script src="/ui/snapshot_export.js" defer></script>' in html
    assert (
        html.index("/ui/lifecycle_export.js")
        < html.index("/ui/snapshot_export.js")
        < html.index("/ui/app.js")
        < html.index("/ui/stream_recovery.js")
        < html.index("/ui/bootstrap.js")
    )
    lifecycle_button = html.index('id="download-lifecycle-ledger"')
    manifest_button = html.index('id="download-evidence-manifest"')
    lifecycle_pill = html.index('id="lifecycle-state"')
    snapshot_button = html.index('id="download-session-snapshot"')
    assert lifecycle_button < manifest_button < lifecycle_pill < snapshot_button

    assert "/snapshot/export" in javascript
    assert "Authorization" in javascript
    assert "Bearer ${snapshotExportState.token}" in javascript
    assert 'Accept: "application/json"' in javascript
    assert 'cache: "no-store"' in javascript
    assert 'credentials: "omit"' in javascript
    assert "new AbortController()" in javascript
    assert "snapshotExportState.generation" in javascript
    assert "snapshotExportTerminalStates" in javascript
    assert 'new Set(["succeeded", "failed", "cancelled"])' in javascript
    assert "response.arrayBuffer()" in javascript
    assert 'crypto.subtle.digest("SHA-256", bytes)' in javascript
    assert 'response.headers.get("Content-Length")' in javascript
    assert 'response.headers.get("X-Harness-X-Snapshot-SHA256")' in javascript
    assert 'response.headers.get("X-Harness-X-Snapshot-Fingerprint")' in javascript
    assert 'response.headers.get("X-Harness-X-Snapshot-Revision")' in javascript
    assert 'new TextDecoder("utf-8", { fatal: true })' in javascript
    assert 'snapshot.schema_version !== "app-session-snapshot-v1"' in javascript
    assert 'snapshot.request?.schema_version !== "app-coding-session-request-v1"' in javascript
    assert "snapshot.session_id !== sessionId" in javascript
    assert "snapshot.fingerprint !== headerFingerprint" in javascript
    assert "snapshot.revision !== declaredRevision" in javascript
    assert 'link.download = "session-snapshot.json"' in javascript
    assert "URL.createObjectURL(blob)" in javascript
    assert "URL.revokeObjectURL(objectUrl)" in javascript
    assert javascript.index("URL.createObjectURL(blob)") < javascript.index("URL.revokeObjectURL(objectUrl)")
    assert 'snapshotExportById("auth-form").addEventListener("submit"' in javascript
    assert 'byId("auth-form").addEventListener("submit"' in app
    assert 'authForm.requestSubmit()' in bootstrap
    assert 'snapshotExportById("lock-button").addEventListener("click"' in javascript
    assert 'window.addEventListener("beforeunload", cancelSnapshotExport)' in javascript
    assert 'snapshotExportById("session-id")' in javascript
    assert 'snapshotExportById("session-status")' in javascript
    assert ".textContent" in javascript

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "innerHTML",
        "window.location.search",
        "?path=",
        "bundle.zip",
        "showSaveFilePicker",
    ):
        assert forbidden not in javascript


def test_snapshot_export_asset_allowlist_remains_exact() -> None:
    assert load_ui_asset("/ui/snapshot_export.js") is not None
    assert load_ui_asset("/ui/snapshot_export.js/../protocol.py") is None
    assert load_ui_asset("/ui/snapshot-export.js") is None
    assert load_ui_asset("/ui/../../etc/passwd") is None


def test_packaged_snapshot_export_client_has_valid_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; JavaScript syntax check requires an available node binary")

    asset = files("harness_x.app_server").joinpath("ui", "snapshot_export.js")
    with as_file(asset) as path:
        completed = subprocess.run(
            [node, "--check", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr
