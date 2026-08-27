from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import BaseModel

from harness_x.app_server.reliability_operator_http_server import LocalOperatorHTTPServer
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


def test_reliability_bridge_is_allowlisted_and_loaded_last() -> None:
    assert load_ui_asset("/ui/reliability_bridge.js") is not None
    bootstrap = _asset_text("/ui/bootstrap.js")
    assert 'loadWorkspaceBridge("/ui/reliability_bridge.js", "reliability bridge")' in bootstrap
    required = (
        "await loadConversationExecutionBridge();",
        "await loadSensitiveApprovalBridge();",
        "await loadProjectSettingsBridge();",
        "await loadProjectResourceBridge();",
        "await loadProjectResourceOutputs();",
        "await loadEverydayReliabilityBridge();",
        "authSubmit.disabled = false;",
    )
    positions = [bootstrap.index(fragment) for fragment in required]
    assert positions == sorted(positions)


def test_everyday_activity_reconnect_is_bounded_and_explicit() -> None:
    javascript = _asset_text("/ui/reliability_bridge.js")
    for required in (
        "EVERYDAY_ACTIVITY_RETRY_DELAYS_MS = Object.freeze([250, 500, 1000, 2000, 4000])",
        "retryIndex >= EVERYDAY_ACTIVITY_RETRY_DELAYS_MS.length",
        "everydayReliabilityState.activityFailures += 1",
        '"Reconnect activity"',
        "conversationExecutionSchedulePollWithBoundedRecovery",
        "everydayReliabilityIsRetriableActivityError",
        'typeof error.status === "number" && error.status < 500',
        "everydayReliabilityActivityValidated()",
        "activityExhausted = true",
    ):
        assert required in javascript
    assert "setInterval(" not in javascript
    assert "while (true" not in javascript
    assert "1200, 1200" not in javascript


def test_recovery_actions_are_execution_scoped_and_do_not_accept_session_authority() -> None:
    javascript = _asset_text("/ui/reliability_bridge.js")
    for required in (
        'new Set(["reliability", "stop", "retry"])',
        'schema_version: "conversation-execution-stop-v1"',
        'schema_version: "conversation-execution-retry-v1"',
        "submission_id: pending.submissionId",
        "Retry will reuse the same retry submission identity.",
        '"Stop"',
        '"Retry"',
        '"Continue"',
        "The prior model/tool process was not resumed",
    ):
        assert required in javascript
    stop_start = javascript.index("async function everydayReliabilityStop()")
    stop_end = javascript.index("function everydayReliabilityRetryPending", stop_start)
    stop_body = javascript[stop_start:stop_end]
    for forbidden in ("session_id", "/v1/sessions", "workspace_root", "output_root"):
        assert forbidden not in stop_body

    retry_start = javascript.index("async function everydayReliabilityRetry()")
    retry_end = javascript.index("function everydayReliabilityContinue", retry_start)
    retry_body = javascript[retry_start:retry_end]
    for forbidden in (
        "settings_revision",
        "settings_fingerprint",
        "verification_commands",
        "resources:",
        "workspace_root",
        "output_root",
        "approval",
    ):
        assert forbidden not in retry_body


def test_reliability_bridge_reconciles_ambiguous_submission_from_authoritative_list() -> None:
    javascript = _asset_text("/ui/reliability_bridge.js")
    for required in (
        "everydayReliabilityReconcileExecutionList(path, page)",
        "item.submission_id === pending.submissionId",
        "conversationExecutionState.pendingSubmission = null",
        "projectResourceState.submissionResources.delete(pending.submissionId)",
        "item.submission_id === retry.submissionId",
        "everydayReliabilityState.retryPending = null",
    ):
        assert required in javascript
    assert "pending.text" in javascript
    assert "daily-composer-text" in javascript


def test_continue_and_shortcuts_reuse_visible_actions_without_stop_shortcut() -> None:
    javascript = _asset_text("/ui/reliability_bridge.js")
    for required in (
        "function everydayReliabilityContinue()",
        "text.focus();",
        "daily-composer\").requestSubmit();",
        "button.click();",
        "event.isComposing",
        "target instanceof HTMLInputElement",
        "target instanceof HTMLTextAreaElement",
        "send.disabled",
    ):
        assert required in javascript
    keyboard = javascript[javascript.index('document.addEventListener("keydown"') :]
    assert "everydayReliabilityStop" not in keyboard
    assert 'event.key === "Escape"' not in keyboard
    assert "daily-reliability-stop" not in keyboard


def test_reliability_ui_uses_safe_dom_and_no_browser_persistence_or_credential_access() -> None:
    javascript = _asset_text("/ui/reliability_bridge.js")
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
        "state.token",
        "Bearer ",
        "Authorization",
        "eval(",
        "new Function",
        "showOpenFilePicker",
        "showDirectoryPicker",
        "window.open",
    ):
        assert forbidden not in javascript


def test_reliability_bridge_has_valid_javascript_syntax_when_node_is_available(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available for JavaScript syntax qualification")
    source = tmp_path / "reliability_bridge.js"
    source.write_text(_asset_text("/ui/reliability_bridge.js"), encoding="utf-8")
    completed = subprocess.run(
        [node, "--check", str(source)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_final_m75_server_serves_reliability_asset_without_weakening_api_auth(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        with urlopen(
            Request(server.base_url + "/ui/reliability_bridge.js", method="GET"),
            timeout=3.0,
        ) as response:
            javascript = response.read().decode("utf-8")
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/javascript; charset=utf-8"
            assert server.token not in javascript
        protected = (
            server.base_url
            + "/v1/projects/project_"
            + "a" * 32
            + "/chats/chat_"
            + "b" * 32
            + "/executions/exec_"
            + "c" * 32
            + "/reliability"
        )
        with pytest.raises(HTTPError) as exc_info:
            urlopen(Request(protected, method="GET"), timeout=3.0)
        assert exc_info.value.code == 401
    finally:
        server.close()
        service.close()
