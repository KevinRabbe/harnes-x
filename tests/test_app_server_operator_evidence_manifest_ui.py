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


def test_evidence_manifest_client_is_terminal_authenticated_and_storage_free() -> None:
    html = _asset_text("/ui/")
    javascript = _asset_text("/ui/evidence_manifest.js")
    app = _asset_text("/ui/app.js")
    bootstrap = _asset_text("/ui/bootstrap.js")

    assert 'id="download-evidence-manifest"' in html
    assert 'id="evidence-manifest-status"' in html
    assert '<script src="/ui/evidence_manifest.js" defer></script>' in html
    assert (
        html.index("/ui/report.js")
        < html.index("/ui/report_export.js")
        < html.index("/ui/trace_export.js")
        < html.index("/ui/evidence_manifest.js")
        < html.index("/ui/app.js")
        < html.index("/ui/bootstrap.js")
    )

    assert "/evidence/manifest" in javascript
    assert "Authorization" in javascript
    assert "Bearer ${evidenceManifestState.token}" in javascript
    assert 'Accept: "application/json"' in javascript
    assert 'cache: "no-store"' in javascript
    assert 'credentials: "omit"' in javascript
    assert "new AbortController()" in javascript
    assert "evidenceManifestState.generation" in javascript
    assert "evidenceManifestTerminalStates" in javascript
    assert 'new Set(["succeeded", "failed", "cancelled"])' in javascript
    assert "response.arrayBuffer()" in javascript
    assert 'crypto.subtle.digest("SHA-256", bytes)' in javascript
    assert 'response.headers.get("Content-Length")' in javascript
    assert 'response.headers.get("X-Harness-X-Evidence-Manifest-SHA256")' in javascript
    assert 'new TextDecoder("utf-8", { fatal: true })' in javascript
    assert '"app-terminal-evidence-manifest-v1"' in javascript
    assert 'link.download = "session-evidence-manifest.json"' in javascript
    assert "URL.createObjectURL(blob)" in javascript
    assert "URL.revokeObjectURL(objectUrl)" in javascript
    assert javascript.index("URL.createObjectURL(blob)") < javascript.index("URL.revokeObjectURL(objectUrl)")
    assert 'evidenceManifestById("auth-form").addEventListener("submit"' in javascript
    assert 'byId("auth-form").addEventListener("submit"' in app
    assert 'authForm.requestSubmit()' in bootstrap
    assert 'evidenceManifestById("lock-button").addEventListener("click"' in javascript
    assert 'window.addEventListener("beforeunload", cancelEvidenceManifestDownload)' in javascript
    assert 'evidenceManifestById("session-status")' in javascript
    assert ".textContent" in javascript

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "innerHTML",
        "window.location.search",
        "?path=",
        "bundle.zip",
    ):
        assert forbidden not in javascript


def test_evidence_manifest_asset_allowlist_remains_exact() -> None:
    assert load_ui_asset("/ui/evidence_manifest.js") is not None
    assert load_ui_asset("/ui/evidence_manifest.js/../protocol.py") is None
    assert load_ui_asset("/ui/evidence-manifest.js") is None
    assert load_ui_asset("/ui/../../etc/passwd") is None


def test_packaged_evidence_manifest_client_has_valid_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; JavaScript syntax check requires an available node binary")

    asset = files("harness_x.app_server").joinpath("ui", "evidence_manifest.js")
    with as_file(asset) as path:
        completed = subprocess.run(
            [node, "--check", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr
