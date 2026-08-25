from __future__ import annotations

from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import BaseModel

from harness_x.app_server.product_operator_http_server import LocalOperatorHTTPServer
from harness_x.app_server.service import AppServerService
from harness_x.app_server.ui_assets import load_ui_asset


class _Report(BaseModel):
    succeeded: bool = True
    failure_reason: str | None = None


def _runner(_snapshot):
    return _Report()


def _asset_text(path: str) -> str:
    asset = load_ui_asset(path)
    assert asset is not None
    return asset[1].decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def test_everyday_workspace_is_primary_and_advanced_operator_is_preserved() -> None:
    html = _asset_text("/ui/")

    assert "<h1>Projects &amp; Chats</h1>" in html
    assert 'id="daily-surface"' in html
    assert 'id="show-workspace"' in html
    assert 'class="surface-tab surface-tab--selected"' in html
    assert 'id="advanced-surface" class="hidden"' in html
    assert "Local Operator" in html
    assert "Conversation-to-execution wiring arrives in M69" in html

    inherited_ids = (
        "auth-form",
        "token",
        "lock-button",
        "session-list",
        "new-session-form",
        "session-view",
        "download-report",
        "download-trace",
        "download-lifecycle-ledger",
        "download-evidence-manifest",
        "download-signed-manifest-pair",
        "download-signed-manifest-capsule",
        "download-session-snapshot",
    )
    for element_id in inherited_ids:
        assert f'id="{element_id}"' in html


def test_everyday_workspace_assets_and_script_order_preserve_bootstrap_wrappers() -> None:
    html = _asset_text("/ui/")
    assert load_ui_asset("/ui/workspace.js") is not None
    assert load_ui_asset("/ui/workspace.css") is not None
    assert '<link rel="stylesheet" href="/ui/workspace.css">' in html

    app = '<script src="/ui/app.js" defer></script>'
    selection = '<script src="/ui/selection_restore.js" defer></script>'
    recovery = '<script src="/ui/stream_recovery.js" defer></script>'
    workspace = '<script src="/ui/workspace.js" defer></script>'
    bootstrap = '<script src="/ui/bootstrap.js" defer></script>'
    for tag in (app, selection, recovery, workspace, bootstrap):
        assert tag in html
    assert html.index(app) < html.index(selection) < html.index(recovery) < html.index(workspace) < html.index(bootstrap)


def test_everyday_workspace_client_uses_only_m67_product_contract_for_chat_submit() -> None:
    javascript = _asset_text("/ui/workspace.js")

    for fragment in (
        '/v1/projects?include_archived=true',
        '/v1/product/restoration',
        '/chats?include_archived=true',
        '/messages',
        '/open',
        '/rename',
        '/archive',
        '/restore',
        'schema_version: "create-project-request-v1"',
        'schema_version: "create-chat-request-v1"',
        'schema_version: "rename-project-request-v1"',
        'schema_version: "rename-chat-request-v1"',
        'schema_version: "append-user-message-request-v1"',
        'role: "user"',
        'type: "text"',
        "chat history is non-contiguous",
        "Harness X execution from chat is not connected until M69",
    ):
        assert fragment in javascript

    assert "/v1/sessions" not in javascript
    assert "Authorization" not in javascript
    assert "Bearer " not in javascript


def test_everyday_workspace_client_renders_user_controlled_content_without_html_sinks() -> None:
    javascript = _asset_text("/ui/workspace.js")

    assert ".textContent" in javascript
    assert "innerHTML" not in javascript
    assert "insertAdjacentHTML" not in javascript
    assert "document.write" not in javascript
    assert "localStorage" not in javascript
    assert "document.cookie" not in javascript

    for user_field in (
        "project.name",
        "project.workspace_root",
        "chat.title",
        "message.content.text",
    ):
        assert user_field in javascript


def test_everyday_workspace_exposes_explicit_empty_loading_error_and_restore_states() -> None:
    html = _asset_text("/ui/")
    javascript = _asset_text("/ui/workspace.js")

    for element_id in (
        "daily-locked",
        "daily-loading",
        "daily-error",
        "daily-no-project",
        "daily-no-chat",
        "daily-archived-projects",
        "daily-archived-project-list",
        "daily-archived-chats",
        "daily-archived-chat-list",
        "daily-composer-status",
    ):
        assert f'id="{element_id}"' in html

    assert "dailyRestoreProject" in javascript
    assert "dailyRestoreChat" in javascript
    assert "dailySetBusy" in javascript
    assert "dailySetError" in javascript


def test_product_server_serves_everyday_assets_without_weakening_authentication(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        with urlopen(Request(server.base_url + "/ui/", method="GET"), timeout=3.0) as response:
            html = response.read().decode("utf-8")
            assert response.status == 200
            assert "Projects &amp; Chats" in html
            assert server.token not in html

        with urlopen(Request(server.base_url + "/ui/workspace.js", method="GET"), timeout=3.0) as response:
            javascript = response.read().decode("utf-8")
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/javascript; charset=utf-8"
            assert server.token not in javascript

        with urlopen(Request(server.base_url + "/ui/workspace.css", method="GET"), timeout=3.0) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/css; charset=utf-8"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(Request(server.base_url + "/v1/projects", method="GET"), timeout=3.0)
        assert exc_info.value.code == 401
    finally:
        server.close()
        service.close()
