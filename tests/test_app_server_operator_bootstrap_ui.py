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


def test_bootstrap_client_scrubs_fragment_before_exchange_and_reuses_manual_auth() -> None:
    html = _asset_text("/ui/")
    javascript = _asset_text("/ui/bootstrap.js")
    app = _asset_text("/ui/app.js")
    report = _asset_text("/ui/report.js")

    assert '<script src="/ui/bootstrap.js" defer></script>' in html
    assert html.index("/ui/report.js") < html.index("/ui/app.js") < html.index("/ui/bootstrap.js")
    assert 'window.location.hash' in javascript
    assert 'history.replaceState(null, "", "/ui/")' in javascript
    assert 'fetch("/v1/operator/bootstrap"' in javascript
    assert javascript.index("history.replaceState") < javascript.index('fetch("/v1/operator/bootstrap"')
    assert 'credentials: "omit"' in javascript
    assert 'JSON.stringify({ ticket })' in javascript
    assert 'payload.access_token = ""' in javascript
    assert 'authForm.requestSubmit()' in javascript
    assert 'tokenField.value = ""' in javascript
    assert 'byId("auth-form").addEventListener("submit"' in app
    assert 'reportById("auth-form").addEventListener("submit"' in report

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "innerHTML",
        "Authorization",
        "window.location.search",
    ):
        assert forbidden not in javascript


def test_bootstrap_client_asset_allowlist_remains_exact() -> None:
    assert load_ui_asset("/ui/bootstrap.js") is not None
    assert load_ui_asset("/ui/bootstrap.js/../protocol.py") is None
    assert load_ui_asset("/ui/bootstrap.json") is None


def test_packaged_bootstrap_client_has_valid_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; JavaScript syntax check requires an available node binary")

    asset = files("harness_x.app_server").joinpath("ui", "bootstrap.js")
    with as_file(asset) as path:
        completed = subprocess.run(
            [node, "--check", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert completed.returncode == 0, completed.stderr
