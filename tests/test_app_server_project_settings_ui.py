from __future__ import annotations

from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import BaseModel

from harness_x.app_server.project_settings_operator_http_server import LocalOperatorHTTPServer
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


def test_project_settings_bridge_is_allowlisted_and_loaded_before_automatic_unlock() -> None:
    assert load_ui_asset("/ui/settings_bridge.js") is not None
    bootstrap = _asset_text("/ui/bootstrap.js")

    for fragment in (
        'loadWorkspaceBridge("/ui/execution_bridge.js", "execution bridge")',
        'loadWorkspaceBridge("/ui/approval_bridge.js", "approval bridge")',
        'loadWorkspaceBridge("/ui/settings_bridge.js", "settings bridge")',
        "await loadConversationExecutionBridge();",
        "await loadSensitiveApprovalBridge();",
        "await loadProjectSettingsBridge();",
    ):
        assert fragment in bootstrap

    assert bootstrap.index("await loadConversationExecutionBridge();") < bootstrap.index(
        "await loadSensitiveApprovalBridge();"
    ) < bootstrap.index("await loadProjectSettingsBridge();") < bootstrap.index(
        "authSubmit.disabled = false;"
    )


def test_project_settings_ui_exposes_only_named_policy_choices_and_safe_projection_fields() -> None:
    javascript = _asset_text("/ui/settings_bridge.js")

    for fragment in (
        '"Project settings"',
        '"Model profile"',
        '"Verification strategy"',
        '"Autonomy profile"',
        '"Project instructions"',
        '"Test connection"',
        '"diff_check"',
        '"pytest"',
        '"pytest_and_diff_check"',
        '"standard"',
        '"cautious"',
        'api("/v1/model-profiles")',
        '/settings`',
        '/settings/test-connection`',
        'schema_version: "replace-project-settings-request-v1"',
        'schema_version: "model-profile-connection-test-request-v1"',
        "profile.provider",
        "profile.model",
        "profile.capabilities",
        "profile.requires_api_key",
        "profile.connection_test_supported",
    ):
        assert fragment in javascript

    for forbidden in (
        "Authorization",
        "Bearer ",
        "api_key_env",
        '"base_url"',
        "verification_commands",
        "max_reasoning_steps",
        "max_tool_actions",
        "max_output_tokens",
        "allow_remote_endpoint",
        "shell_command",
    ):
        assert forbidden not in javascript


def test_project_settings_request_bodies_contain_only_allowlisted_operator_fields() -> None:
    javascript = _asset_text("/ui/settings_bridge.js")

    save_start = javascript.index('schema_version: "replace-project-settings-request-v1"')
    save_end = javascript.index("}),", save_start)
    save_body = javascript[save_start:save_end]
    for required in (
        "schema_version",
        "model_profile",
        "verification_strategy",
        "project_instructions",
        "autonomy_profile",
    ):
        assert required in save_body
    for forbidden in (
        "profile_id",
        "base_url",
        "api_key",
        "api_key_env",
        "verification_commands",
        "max_tool_actions",
        "max_output_tokens",
    ):
        assert forbidden not in save_body

    probe_start = javascript.index('schema_version: "model-profile-connection-test-request-v1"')
    probe_end = javascript.index("}),", probe_start)
    probe_body = javascript[probe_start:probe_end]
    assert "schema_version" in probe_body and "profile_id" in probe_body
    for forbidden in (
        "model_profile",
        "base_url",
        "api_key",
        "api_key_env",
        "project_instructions",
        "verification_strategy",
        "autonomy_profile",
    ):
        assert forbidden not in probe_body


def test_project_settings_ui_uses_text_safe_dom_operations_and_no_browser_persistence() -> None:
    javascript = _asset_text("/ui/settings_bridge.js")

    assert ".textContent" in javascript
    assert "insertAdjacentElement" in javascript
    for forbidden in (
        "innerHTML",
        "insertAdjacentHTML",
        "outerHTML",
        "document.write",
        "localStorage",
        "sessionStorage",
        "document.cookie",
    ):
        assert forbidden not in javascript


def test_final_m73_server_serves_settings_bridge_without_exposing_bearer_or_weakening_api_auth(
    tmp_path: Path,
) -> None:
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        with urlopen(
            Request(server.base_url + "/ui/settings_bridge.js", method="GET"),
            timeout=3.0,
        ) as response:
            javascript = response.read().decode("utf-8")
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/javascript; charset=utf-8"
            assert server.token not in javascript

        with pytest.raises(HTTPError) as exc_info:
            urlopen(Request(server.base_url + "/v1/model-profiles", method="GET"), timeout=3.0)
        assert exc_info.value.code == 401
    finally:
        server.close()
        service.close()
