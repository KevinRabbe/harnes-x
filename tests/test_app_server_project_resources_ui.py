from __future__ import annotations

from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import BaseModel

from harness_x.app_server.resource_operator_http_server import LocalOperatorHTTPServer
from harness_x.app_server.service import AppServerService
from harness_x.app_server.ui_assets import load_ui_asset


class _Report(BaseModel):
    succeeded: bool = True


def _runner(_snapshot):
    return _Report()


def _asset_text(path: str) -> str:
    asset = load_ui_asset(path)
    assert asset is not None
    return asset[1].decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _resource_javascript() -> str:
    return _asset_text("/ui/resource_bridge.js") + "\n" + _asset_text("/ui/resource_outputs.js")


def test_resource_bridges_are_allowlisted_and_loaded_after_inherited_bridges() -> None:
    assert load_ui_asset("/ui/resource_bridge.js") is not None
    assert load_ui_asset("/ui/resource_outputs.js") is not None
    bootstrap = _asset_text("/ui/bootstrap.js")
    for fragment in (
        'loadWorkspaceBridge("/ui/execution_bridge.js", "execution bridge")',
        'loadWorkspaceBridge("/ui/approval_bridge.js", "approval bridge")',
        'loadWorkspaceBridge("/ui/settings_bridge.js", "settings bridge")',
        'loadWorkspaceBridge("/ui/resource_bridge.js", "resource bridge")',
        'loadWorkspaceBridge("/ui/resource_outputs.js", "resource outputs")',
        "await loadConversationExecutionBridge();",
        "await loadSensitiveApprovalBridge();",
        "await loadProjectSettingsBridge();",
        "await loadProjectResourceBridge();",
        "await loadProjectResourceOutputs();",
    ):
        assert fragment in bootstrap
    assert bootstrap.index("await loadConversationExecutionBridge();") < bootstrap.index(
        "await loadSensitiveApprovalBridge();"
    ) < bootstrap.index("await loadProjectSettingsBridge();") < bootstrap.index(
        "await loadProjectResourceBridge();"
    ) < bootstrap.index("await loadProjectResourceOutputs();") < bootstrap.index(
        "authSubmit.disabled = false;"
    )


def test_resource_ui_is_bounded_relative_and_authority_neutral() -> None:
    javascript = _resource_javascript()
    for fragment in (
        '"Files & attachments"',
        'attachment.type = "file"',
        "attachment.multiple = true",
        '"Reference workspace file"',
        "PROJECT_RESOURCE_MAX_ITEMS = 4",
        "PROJECT_RESOURCE_MAX_ATTACHMENT_BYTES = 1024 * 1024",
        'schema_version: "project-attachment-upload-request-v1"',
        'nextPayload.schema_version = "conversation-execution-submit-v2"',
        "nextPayload.resources = frozen.map",
        'projectResourceExecutionPath(projectId, chatId, executionId, "diff")',
        'projectResourceExecutionPath(projectId, chatId, executionId, "artifacts")',
        "Execution authorship is not proven",
        "not verification or evidence",
    ):
        assert fragment in javascript
    for forbidden in (
        "showOpenFilePicker",
        "showDirectoryPicker",
        "webkitRelativePath",
        "window.open",
        "workspace_root",
        "artifact_root",
        "shell_command",
        "verification_commands",
        "api_key",
        "api_key_env",
        "max_reasoning_steps",
        "max_tool_actions",
        "max_output_tokens",
    ):
        assert forbidden not in javascript


def test_resource_submission_rewrite_freezes_only_allowlisted_reference_shapes() -> None:
    javascript = _asset_text("/ui/resource_bridge.js")
    assert '/^\\/v1\\/projects\\/project_[0-9a-f]{32}\\/chats\\/chat_[0-9a-f]{32}\\/executions$/' in javascript
    for required in (
        'payload.schema_version === "conversation-execution-submit-v1"',
        "projectResourceState.submissionResources.get(payload.submission_id)",
        "projectResourceState.submissionResources.set(payload.submission_id, frozen)",
        'nextPayload.schema_version = "conversation-execution-submit-v2"',
        "projectResourceState.submissionResources.delete(payload.submission_id)",
        "conversationExecutionState.pendingSubmission = null",
        '{ kind: "attachment", attachment_id: item.attachment_id }',
        '{ kind: "workspace_file", source_path: item.source_path }',
    ):
        assert required in javascript
    refs = javascript[javascript.index("function projectResourceReferences()") : javascript.index("function projectResourceSelectionChanged()")]
    for forbidden in ("filename", "media_type", "data_base64", "workspace_root", "absolute_path", "permissions"):
        assert forbidden not in refs


def test_attachment_upload_body_contains_only_bounded_contract_fields() -> None:
    javascript = _asset_text("/ui/resource_bridge.js")
    start = javascript.index('schema_version: "project-attachment-upload-request-v1"')
    end = javascript.index("}),", start)
    body = javascript[start:end]
    for required in ("schema_version", "filename", "media_type", "data_base64"):
        assert required in body
    for forbidden in ("absolute_path", "workspace_root", "destination", "permissions", "command", "environment"):
        assert forbidden not in body


def test_resource_ui_uses_text_safe_dom_and_no_browser_persistence() -> None:
    javascript = _resource_javascript()
    assert "document.createElement" in javascript
    assert ".textContent" in javascript
    for forbidden in (
        "innerHTML",
        "insertAdjacentHTML",
        "outerHTML",
        "document.write",
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "eval(",
        "new Function",
    ):
        assert forbidden not in javascript


def test_artifact_download_is_exact_digest_checked_and_does_not_retain_token() -> None:
    main = _asset_text("/ui/resource_bridge.js")
    outputs = _asset_text("/ui/resource_outputs.js")
    state_body = main[main.index("const projectResourceState = {") : main.index("};", main.index("const projectResourceState = {"))]
    assert "token" not in state_body.casefold()
    for required in (
        "!state.token",
        'Authorization: `Bearer ${state.token}`',
        'credentials: "omit"',
        'redirect: "error"',
        'response.headers.get("Content-Length")',
        'response.headers.get("X-Harness-X-Artifact-SHA256")',
        'response.headers.get("Content-Disposition")',
        "projectResourceSha256Hex(bytes)",
        "URL.createObjectURL",
        "URL.revokeObjectURL",
    ):
        assert required in outputs
    assert outputs.count("state.token") == 2
    assert outputs.count("Authorization") == 1
    assert outputs.count("Bearer ") == 1


def test_final_m74_server_serves_resource_assets_without_exposing_bearer_or_weakening_auth(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        for path in ("/ui/resource_bridge.js", "/ui/resource_outputs.js"):
            with urlopen(Request(server.base_url + path, method="GET"), timeout=3.0) as response:
                javascript = response.read().decode("utf-8")
                assert response.status == 200
                assert response.headers["Content-Type"] == "text/javascript; charset=utf-8"
                assert server.token not in javascript
        with pytest.raises(HTTPError) as exc_info:
            urlopen(Request(server.base_url + "/v1/projects/project_" + "a" * 32 + "/attachments", method="GET"), timeout=3.0)
        assert exc_info.value.code == 401
    finally:
        server.close()
        service.close()
