from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from harness_x.app_server import cli as app_server_cli
from harness_x.app_server.conversation_execution import (
    ConversationExecutionCoordinator,
    ConversationExecutionSubmitRequest,
)
from harness_x.app_server.conversation_operator_http_server import LocalOperatorHTTPServer
from harness_x.app_server.service import AppServerService
from harness_x.app_server.ui_assets import load_ui_asset
from harness_x.product import ProjectChatStore


class _Report(BaseModel):
    succeeded: bool
    failure_reason: str | None = None


def _write_report(snapshot, *, succeeded: bool, failure_reason: str | None = None) -> _Report:
    output = Path(snapshot.output_root)
    output.mkdir(parents=True, exist_ok=True)
    report = _Report(succeeded=succeeded, failure_reason=failure_reason)
    (output / "coding-task-report.json").write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _success_runner(snapshot) -> _Report:
    return _write_report(snapshot, succeeded=True)


def _failure_runner(snapshot) -> _Report:
    return _write_report(
        snapshot,
        succeeded=False,
        failure_reason="software-owned verification failed",
    )


class _BlockingRunner:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, snapshot) -> _Report:
        self.calls += 1
        self.started.set()
        if not self.release.wait(timeout=10.0):
            raise TimeoutError("test runner release timed out")
        return _write_report(snapshot, succeeded=True)


class _ServerHarness:
    def __init__(self, root: Path, *, runner=_success_runner) -> None:
        self.service = AppServerService(root / "data", runner=runner)
        self.server = LocalOperatorHTTPServer(self.service, root / "transport", port=0)
        self.server.start_in_thread()

    def close(self) -> None:
        self.server.close()
        self.service.close()


def _request(
    harness: _ServerHarness,
    method: str,
    path: str,
    *,
    body: object | str | None = None,
    token: str | None = "server",
) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", harness.server.port, timeout=10)
    headers: dict[str, str] = {}
    if token == "server":
        headers["Authorization"] = f"Bearer {harness.server.token}"
    elif token is not None:
        headers["Authorization"] = f"Bearer {token}"
    payload: bytes | None = None
    if body is not None:
        payload = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw.decode("utf-8")) if raw else {}


def _create_project(
    harness: _ServerHarness,
    workspace: Path,
    *,
    name: str = "Project",
    model_profile: str | None = None,
) -> dict:
    status, payload = _request(
        harness,
        "POST",
        "/v1/projects",
        body={
            "schema_version": "create-project-request-v1",
            "name": name,
            "workspace_root": str(workspace),
            "default_model_profile": model_profile,
        },
    )
    assert status == 201, payload
    return payload


def _create_chat(harness: _ServerHarness, project_id: str, *, title: str = "Chat") -> dict:
    status, payload = _request(
        harness,
        "POST",
        f"/v1/projects/{project_id}/chats",
        body={"schema_version": "create-chat-request-v1", "title": title},
    )
    assert status == 201, payload
    return payload


def _submission(submission_id: str, text: str) -> dict:
    return {
        "schema_version": "conversation-execution-submit-v1",
        "submission_id": submission_id,
        "role": "user",
        "content": {"type": "text", "text": text},
    }


def _wait_terminal(
    harness: _ServerHarness,
    project_id: str,
    chat_id: str,
    execution_id: str,
    *,
    timeout: float = 10.0,
) -> dict:
    deadline = time.monotonic() + timeout
    path = f"/v1/projects/{project_id}/chats/{chat_id}/executions/{execution_id}"
    while time.monotonic() < deadline:
        status, payload = _request(harness, "GET", path)
        assert status == 200, payload
        if payload["terminal"]:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"conversation execution did not become terminal: {execution_id}")


def _wait_session_terminal(service: AppServerService, session_id: str, *, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = service.session(session_id)
        if snapshot.status.terminal:
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"App Session did not become terminal: {session_id}")


def _asset_text(path: str) -> str:
    asset = load_ui_asset(path)
    assert asset is not None
    return asset[1].decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def test_execution_submit_runs_existing_app_session_and_appends_software_terminal_result(
    tmp_path: Path,
) -> None:
    harness = _ServerHarness(tmp_path / "server")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project = _create_project(harness, workspace, model_profile="coder")
        chat = _create_chat(harness, project["project_id"])
        path = f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/executions"
        first_body = _submission("submission_" + "1" * 32, "Implement the requested change")

        status, created = _request(harness, "POST", path, body=first_body)
        assert status == 202, created
        assert created["execution_id"].startswith("exec_")
        assert created["session_id"].startswith("app_")
        assert created["model_profile"] == "coder"
        assert created["verification_policy_id"] == "m69-git-diff-check-v1"

        terminal = _wait_terminal(
            harness,
            project["project_id"],
            chat["chat_id"],
            created["execution_id"],
        )
        assert terminal["status"] == "succeeded"
        assert terminal["terminal"] is True
        assert terminal["result_message_id"].startswith("msg_")

        session = harness.service.session(created["session_id"])
        assert session.request.workspace_root == workspace.resolve()
        assert session.request.task == "Implement the requested change"
        assert session.request.model_profile == "coder"
        assert session.request.verification_commands == ("git diff --check",)

        status, page = _request(
            harness,
            "GET",
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/messages",
        )
        assert status == 200
        assert [item["role"] for item in page["messages"]] == ["user", "assistant"]
        assert page["messages"][0]["content"]["text"] == "Implement the requested change"
        assistant_text = page["messages"][1]["content"]["text"]
        assert "Harness X completed this work successfully." in assistant_text
        assert created["execution_id"] in assistant_text
        assert created["session_id"] in assistant_text
        assert "Status: succeeded" in assistant_text

        # Network/application retries reuse the same durable plan/session/result.
        retry_status, retry = _request(harness, "POST", path, body=first_body)
        assert retry_status == 202
        assert retry["execution_id"] == created["execution_id"]
        assert retry["session_id"] == created["session_id"]
        status, page_after_retry = _request(
            harness,
            "GET",
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/messages",
        )
        assert status == 200 and len(page_after_retry["messages"]) == 2
        assert len(harness.service.sessions()) == 1

        second_body = _submission("submission_" + "2" * 32, "Run a second bounded work turn")
        status, second = _request(harness, "POST", path, body=second_body)
        assert status == 202, second
        second_terminal = _wait_terminal(
            harness,
            project["project_id"],
            chat["chat_id"],
            second["execution_id"],
        )
        assert second_terminal["status"] == "succeeded"
        assert second["execution_id"] != created["execution_id"]
        assert second["session_id"] != created["session_id"]
        status, final_page = _request(
            harness,
            "GET",
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/messages",
        )
        assert status == 200
        assert [item["role"] for item in final_page["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
    finally:
        harness.close()


def test_execution_failure_is_projected_as_software_owned_assistant_failure(tmp_path: Path) -> None:
    harness = _ServerHarness(tmp_path / "server", runner=_failure_runner)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project = _create_project(harness, workspace)
        chat = _create_chat(harness, project["project_id"])
        path = f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/executions"
        status, created = _request(
            harness,
            "POST",
            path,
            body=_submission("submission_" + "3" * 32, "This work fails verification"),
        )
        assert status == 202
        terminal = _wait_terminal(
            harness,
            project["project_id"],
            chat["chat_id"],
            created["execution_id"],
        )
        assert terminal["status"] == "failed"

        status, page = _request(
            harness,
            "GET",
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/messages",
        )
        assert status == 200
        text = page["messages"][-1]["content"]["text"]
        assert "Harness X could not complete this work." in text
        assert "software-owned verification failed" in text
        assert "Status: failed" in text
    finally:
        harness.close()


def test_active_chat_execution_blocks_overtaking_turns_and_archive_but_allows_same_retry(
    tmp_path: Path,
) -> None:
    runner = _BlockingRunner()
    harness = _ServerHarness(tmp_path / "server", runner=runner)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project = _create_project(harness, workspace)
        chat = _create_chat(harness, project["project_id"])
        path = f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/executions"
        first_body = _submission("submission_" + "4" * 32, "Long work turn")
        status, first = _request(harness, "POST", path, body=first_body)
        assert status == 202
        assert runner.started.wait(timeout=3.0)

        status, conflict = _request(
            harness,
            "POST",
            path,
            body=_submission("submission_" + "5" * 32, "Do not overtake"),
        )
        assert status == 409
        assert conflict["error"] == "conversation_execution_conflict"
        assert "active conversation execution" in (conflict.get("detail") or "")

        retry_status, retry = _request(harness, "POST", path, body=first_body)
        assert retry_status == 202
        assert retry["execution_id"] == first["execution_id"]
        assert retry["session_id"] == first["session_id"]
        assert runner.calls == 1

        status, _ = _request(
            harness,
            "POST",
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/archive",
            body={},
        )
        assert status == 409
        status, _ = _request(
            harness,
            "POST",
            f"/v1/projects/{project['project_id']}/archive",
            body={},
        )
        assert status == 409

        runner.release.set()
        terminal = _wait_terminal(
            harness,
            project["project_id"],
            chat["chat_id"],
            first["execution_id"],
        )
        assert terminal["status"] == "succeeded"
        status, archived = _request(
            harness,
            "POST",
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/archive",
            body={},
        )
        assert status == 200 and archived["archived"] is True
    finally:
        runner.release.set()
        harness.close()


def test_submission_identity_auth_and_project_chat_ownership_are_enforced(tmp_path: Path) -> None:
    harness = _ServerHarness(tmp_path / "server")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project = _create_project(harness, workspace)
        first_chat = _create_chat(harness, project["project_id"], title="First")
        second_chat = _create_chat(harness, project["project_id"], title="Second")
        path = f"/v1/projects/{project['project_id']}/chats/{first_chat['chat_id']}/executions"
        body = _submission("submission_" + "6" * 32, "Identity-bound work")

        status, unauthorized = _request(harness, "POST", path, body=body, token=None)
        assert status == 401 and unauthorized["error"] == "unauthorized"

        status, created = _request(harness, "POST", path, body=body)
        assert status == 202
        _wait_terminal(
            harness,
            project["project_id"],
            first_chat["chat_id"],
            created["execution_id"],
        )

        status, conflict = _request(
            harness,
            "POST",
            path,
            body=_submission("submission_" + "6" * 32, "Different text"),
        )
        assert status == 409
        assert "already bound" in (conflict.get("detail") or "")

        status, wrong_owner = _request(
            harness,
            "GET",
            f"/v1/projects/{project['project_id']}/chats/{second_chat['chat_id']}/executions/{created['execution_id']}",
        )
        assert status == 409
        assert "belongs to another project/chat" in (wrong_owner.get("detail") or "")

        status, malformed = _request(harness, "GET", path + "/not-an-execution")
        assert status == 400
        assert malformed["error"] == "invalid_conversation_execution_request"
        status, queried = _request(harness, "GET", path + "?after=0")
        assert status == 400
        assert queried["error"] == "invalid_conversation_execution_request"
    finally:
        harness.close()


def test_binding_loss_recovery_reuses_exact_user_session_and_terminal_result(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AppServerService(tmp_path / "service", runner=(runner := _BlockingRunner()))
    product = ProjectChatStore(service.root / "product")
    lock = threading.RLock()
    root = service.root / "conversation-executions"
    try:
        project = product.create_project(name="Project", workspace_root=workspace)
        chat = product.create_chat(project.project_id, title="Chat")
        request = ConversationExecutionSubmitRequest.model_validate(
            _submission("submission_" + "7" * 32, "Recover exact durable work")
        )
        first = ConversationExecutionCoordinator(service, product, lock, root)
        projection = first.submit(
            project_id=project.project_id,
            chat_id=chat.chat_id,
            request=request,
        )
        assert projection.session_id is not None
        assert runner.started.wait(timeout=3.0)
        original_session_id = projection.session_id
        assert len(service.sessions()) == 1
        assert len(product.messages(chat.chat_id)) == 1

        # Simulate a crash after the already-durable product/session writes but before their
        # append-only M69 bindings survive. Recovery must discover the exact anchors.
        first.store.bindings_path.unlink()
        recovered = ConversationExecutionCoordinator(service, product, lock, root)
        recovered_projection = recovered.projection(projection.execution_id)
        assert recovered_projection.session_id == original_session_id
        assert len(service.sessions()) == 1
        assert len(product.messages(chat.chat_id)) == 1

        runner.release.set()
        terminal_session = _wait_session_terminal(service, original_session_id)
        assert terminal_session.status.value == "succeeded"
        terminal = recovered.projection(projection.execution_id)
        assert terminal.result_message_id is not None
        messages = product.messages(chat.chat_id)
        assert [item.role.value for item in messages] == ["user", "assistant"]
        original_result_id = terminal.result_message_id

        # Simulate the corresponding append-before-bind crash for the terminal assistant row.
        recovered.store.bindings_path.unlink()
        recovered_again = ConversationExecutionCoordinator(service, product, lock, root)
        final = recovered_again.projection(projection.execution_id)
        assert final.session_id == original_session_id
        assert final.result_message_id == original_result_id
        assert len(service.sessions()) == 1
        assert len(product.messages(chat.chat_id)) == 2
    finally:
        runner.release.set()
        service.close()


def test_execution_ui_wrapper_is_allowlisted_safe_retry_aware_and_session_api_free() -> None:
    javascript = _asset_text("/ui/execution_bridge.js")
    bootstrap = _asset_text("/ui/bootstrap.js")

    for fragment in (
        "conversation-execution-submit-v1",
        "/executions",
        "submission_",
        "crypto.getRandomValues",
        "stopImmediatePropagation",
        "pendingSubmission",
        "projection.terminal",
        "setTimeout",
        "activeExecution",
        "Retry will reuse the same submission identity",
    ):
        assert fragment in javascript
    assert "/v1/sessions" not in javascript
    assert "Authorization" not in javascript
    assert "Bearer " not in javascript
    assert "innerHTML" not in javascript
    assert "insertAdjacentHTML" not in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert "document.cookie" not in javascript

    assert "/ui/execution_bridge.js" in bootstrap
    assert "history.replaceState" in bootstrap
    assert bootstrap.index("history.replaceState") < bootstrap.index("await loadConversationExecutionBridge")
    assert 'authSubmit.disabled = true' in bootstrap
    assert app_server_cli.LocalOperatorHTTPServer.__module__.endswith(
        "conversation_operator_http_server"
    )


def test_m69_server_serves_execution_asset_publicly_but_execution_api_stays_authenticated(
    tmp_path: Path,
) -> None:
    harness = _ServerHarness(tmp_path / "server")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", harness.server.port, timeout=10)
        connection.request("GET", "/ui/execution_bridge.js")
        response = connection.getresponse()
        javascript = response.read().decode("utf-8")
        assert response.status == 200
        assert response.getheader("Content-Type") == "text/javascript; charset=utf-8"
        assert harness.server.token not in javascript
        connection.close()

        project = _create_project(harness, workspace)
        chat = _create_chat(harness, project["project_id"])
        path = f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/executions"
        status, payload = _request(harness, "GET", path, token=None)
        assert status == 401 and payload["error"] == "unauthorized"
    finally:
        harness.close()
