from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import BaseModel

from harness_x.app_server.operator_http_server import LocalOperatorHTTPServer
from harness_x.app_server.service import AppServerService
from harness_x.app_server.ui_assets import load_ui_asset


class _Report(BaseModel):
    succeeded: bool = True
    failure_reason: str | None = None


def _runner(snapshot):
    output = Path(snapshot.output_root)
    output.mkdir(parents=True, exist_ok=True)
    report = _Report()
    (output / "coding-task-report.json").write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return report


def _get_text(server: LocalOperatorHTTPServer, path: str, *, authorized: bool = False):
    headers: dict[str, str] = {}
    if authorized:
        headers["Authorization"] = f"Bearer {server.token}"
    request = Request(server.base_url + path, headers=headers, method="GET")
    with urlopen(request, timeout=3.0) as response:
        return response.status, response.headers, response.read().decode("utf-8")


def test_operator_ui_assets_are_public_but_session_api_remains_authenticated(
    tmp_path: Path,
) -> None:
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        status, headers, html = _get_text(server, "/ui/")
        assert status == 200
        assert headers["Content-Type"] == "text/html; charset=utf-8"
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
        csp = headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp
        assert "script-src 'self'" in csp
        assert "style-src 'self'" in csp
        assert "connect-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "'unsafe-inline'" not in csp
        assert server.token not in html
        policy_script = '<script src="/ui/stream_policy.js" defer></script>'
        app_script = '<script src="/ui/app.js" defer></script>'
        assert policy_script in html
        assert app_script in html
        assert html.index(policy_script) < html.index(app_script)
        assert '<link rel="stylesheet" href="/ui/styles.css">' in html

        with pytest.raises(HTTPError) as exc_info:
            _get_text(server, "/v1/sessions")
        assert exc_info.value.code == 401
    finally:
        server.close()
        service.close()


def test_operator_ui_client_keeps_bearer_auth_in_memory_and_uses_safe_dom_rendering(
    tmp_path: Path,
) -> None:
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        status, _, javascript = _get_text(server, "/ui/app.js")
        assert status == 200
        assert "Authorization" in javascript
        assert "Bearer ${state.token}" in javascript
        assert "/events/stream?after=" in javascript
        assert "/trace/stream?after=" in javascript
        assert "response.body.getReader()" in javascript
        assert "streamReconnectTimers" in javascript
        assert "reconnectDelayMs" in javascript
        assert "advanceCursor" in javascript
        assert "clearTimeout" in javascript
        assert ".textContent" in javascript
        assert "innerHTML" not in javascript
        assert "localStorage" not in javascript
        assert "sessionStorage" not in javascript
        assert "document.cookie" not in javascript
        assert "EventSource" not in javascript
        assert server.token not in javascript

        policy_status, _, policy = _get_text(server, "/ui/stream_policy.js")
        assert policy_status == 200
        assert "maxReconnectAttempts" in policy
        assert "non-contiguous stream cursor" in policy
        assert server.token not in policy
    finally:
        server.close()
        service.close()


def test_operator_ui_asset_allowlist_cannot_read_arbitrary_package_paths() -> None:
    assert load_ui_asset("/ui/") is not None
    assert load_ui_asset("/ui/stream_policy.js") is not None
    assert load_ui_asset("/ui/app.js") is not None
    assert load_ui_asset("/ui/styles.css") is not None
    assert load_ui_asset("/ui/../protocol.py") is None
    assert load_ui_asset("/ui_assets.py") is None
    assert load_ui_asset("/etc/passwd") is None


def test_operator_ui_preserves_host_header_validation(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        request = Request(
            server.base_url + "/ui/",
            headers={"Host": "attacker.invalid"},
            method="GET",
        )
        with pytest.raises(HTTPError) as exc_info:
            urlopen(request, timeout=3.0)
        assert exc_info.value.code == 400
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "invalid_host"
    finally:
        server.close()
        service.close()


def test_operator_server_inherits_authenticated_session_creation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        request = Request(
            server.base_url + "/v1/sessions",
            data=json.dumps(
                {
                    "workspace_root": str(workspace),
                    "task": "operator UI launch path",
                    "model_profile": "main",
                    "verification_commands": ["python -m pytest"],
                }
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {server.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=3.0) as response:
            created = json.loads(response.read().decode("utf-8"))
        assert response.status == 202
        assert created["session_id"].startswith("app_")
        assert service.session(created["session_id"]).request.task == "operator UI launch path"
        assert server.ui_url == f"{server.base_url}/ui/"
    finally:
        server.close()
        service.close()
