from __future__ import annotations

import hashlib
import http.client
import json
import threading
import time
from pathlib import Path

from pydantic import BaseModel

from harness_x.app_server.approval_runner import ApprovalAwareHarnessCodingRunner
from harness_x.app_server.reliability_operator_http_server import LocalOperatorHTTPServer
from harness_x.app_server.sensitive_approval import SensitiveActionApprovalBroker
from harness_x.app_server.service import AppServerService


class _Report(BaseModel):
    succeeded: bool
    failure_reason: str | None = None


class _SequenceRunner:
    def __init__(self, outcomes: list[bool]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, snapshot) -> _Report:
        index = self.calls
        self.calls += 1
        succeeded = self._outcomes[index] if index < len(self._outcomes) else True
        report = _Report(
            succeeded=succeeded,
            failure_reason=None if succeeded else "scripted_failure",
        )
        output = Path(snapshot.output_root)
        output.mkdir(parents=True, exist_ok=True)
        (output / "coding-task-report.json").write_text(
            report.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        return report


class _BlockingRunner:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, snapshot) -> _Report:
        self.started.set()
        self.release.wait(timeout=8.0)
        report = _Report(succeeded=False, failure_reason="stopped_fixture")
        output = Path(snapshot.output_root)
        output.mkdir(parents=True, exist_ok=True)
        (output / "coding-task-report.json").write_text(
            report.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        return report


class _Harness:
    def __init__(self, root: Path, delegate) -> None:
        broker = SensitiveActionApprovalBroker(root / "data" / "sensitive-approvals")
        runner = ApprovalAwareHarnessCodingRunner(broker)
        runner.delegate = delegate
        self.delegate = delegate
        self.broker = broker
        self.service = AppServerService(root / "data", runner=runner)
        self.server = LocalOperatorHTTPServer(self.service, root / "transport", port=0)
        self.server.start_in_thread()

    def close(self) -> None:
        self.server.close()
        self.service.close()


def _json(
    harness: _Harness,
    method: str,
    path: str,
    *,
    body: object | None = None,
    authorized: bool = True,
) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", harness.server.port, timeout=10)
    headers: dict[str, str] = {}
    if authorized:
        headers["Authorization"] = f"Bearer {harness.server.token}"
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw.decode("utf-8")) if raw else {}


def _create_project_chat(harness: _Harness, workspace: Path) -> tuple[dict, dict]:
    status, project = _json(
        harness,
        "POST",
        "/v1/projects",
        body={
            "schema_version": "create-project-request-v1",
            "name": "Reliability project",
            "workspace_root": str(workspace),
            "default_model_profile": None,
        },
    )
    assert status == 201, project
    status, chat = _json(
        harness,
        "POST",
        f"/v1/projects/{project['project_id']}/chats",
        body={"schema_version": "create-chat-request-v1", "title": "Recovery"},
    )
    assert status == 201, chat
    return project, chat


def _execution_base(project: dict, chat: dict) -> str:
    return f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/executions"


def _submit(
    harness: _Harness,
    project: dict,
    chat: dict,
    *,
    suffix: str,
    resources: list[dict] | None = None,
) -> dict:
    body: dict[str, object] = {
        "schema_version": "conversation-execution-submit-v2" if resources else "conversation-execution-submit-v1",
        "submission_id": "submission_" + suffix * 32,
        "role": "user",
        "content": {"type": "text", "text": "Repair the reliability fixture"},
    }
    if resources:
        body["resources"] = resources
    status, projection = _json(
        harness,
        "POST",
        _execution_base(project, chat),
        body=body,
    )
    assert status == 202, projection
    return projection


def _wait_terminal(
    harness: _Harness,
    project: dict,
    chat: dict,
    execution_id: str,
) -> dict:
    path = f"{_execution_base(project, chat)}/{execution_id}"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        status, projection = _json(harness, "GET", path)
        assert status == 200, projection
        if projection["terminal"]:
            return projection
        time.sleep(0.02)
    raise AssertionError("execution did not become terminal")


def test_retry_clones_frozen_settings_and_resource_bytes_and_is_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resource = workspace / "context.txt"
    resource.write_text("first immutable context\n", encoding="utf-8")
    harness = _Harness(tmp_path / "server", _SequenceRunner([False, True]))
    try:
        project, chat = _create_project_chat(harness, workspace)
        source = _submit(
            harness,
            project,
            chat,
            suffix="a",
            resources=[{"kind": "workspace_file", "source_path": "context.txt"}],
        )
        source = _wait_terminal(harness, project, chat, source["execution_id"])
        assert source["status"] == "failed"

        coordinator = harness.server.conversation
        source_plan = coordinator.store.plan(source["execution_id"])
        source_settings = coordinator.settings_execution_store.snapshot(source["execution_id"])
        source_resources = coordinator.resource_execution_store.snapshot(source["execution_id"])
        assert source_settings is not None
        assert source_resources is not None and len(source_resources.items) == 1
        old_digest = source_resources.items[0].sha256

        resource.write_text("changed after failed attempt\n", encoding="utf-8")
        assert hashlib.sha256(resource.read_bytes()).hexdigest() != old_digest

        retry_path = f"{_execution_base(project, chat)}/{source['execution_id']}/retry"
        request = {
            "schema_version": "conversation-execution-retry-v1",
            "submission_id": "submission_" + "b" * 32,
        }
        status, result = _json(harness, "POST", retry_path, body=request)
        assert status == 202, result
        retried = result["execution"]
        assert retried["execution_id"] != source["execution_id"]
        assert result["source_execution_id"] == source["execution_id"]

        status, duplicate = _json(harness, "POST", retry_path, body=request)
        assert status == 202, duplicate
        assert duplicate["execution"]["execution_id"] == retried["execution_id"]

        retry_plan = coordinator.store.plan(retried["execution_id"])
        retry_record = coordinator.retry_store.record(retried["execution_id"])
        retry_settings = coordinator.settings_execution_store.snapshot(retried["execution_id"])
        retry_resources = coordinator.resource_execution_store.snapshot(retried["execution_id"])
        assert retry_record is not None
        assert retry_record.source_execution_id == source["execution_id"]
        assert retry_plan.task == source_plan.task
        assert retry_plan.request == source_plan.request
        assert retry_settings is not None
        assert retry_settings.settings_revision == source_settings.settings_revision
        assert retry_settings.settings_fingerprint == source_settings.settings_fingerprint
        assert retry_settings.model_profile == source_settings.model_profile
        assert retry_settings.verification_commands == source_settings.verification_commands
        assert retry_resources is not None and len(retry_resources.items) == 1
        assert retry_resources.items[0].sha256 == old_digest
        assert retry_resources.items[0].resource_id == source_resources.items[0].resource_id
        assert retry_resources.rendered_context != source_resources.rendered_context
        assert retried["execution_id"] in retry_resources.rendered_context

        source_approval = harness.broker.context_for_output_root(source_plan.output_root)
        retry_approval = harness.broker.context_for_output_root(retry_plan.output_root)
        assert source_approval is not None and retry_approval is not None
        assert source_approval.execution_id != retry_approval.execution_id

        terminal_retry = _wait_terminal(harness, project, chat, retried["execution_id"])
        assert terminal_retry["status"] == "succeeded"
        assert harness.delegate.calls == 2
    finally:
        harness.close()


def test_stop_resolves_owned_session_and_repeated_requests_do_not_hard_kill(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    delegate = _BlockingRunner()
    harness = _Harness(tmp_path / "server", delegate)
    try:
        project, chat = _create_project_chat(harness, workspace)
        execution = _submit(harness, project, chat, suffix="c")
        assert delegate.started.wait(timeout=5.0)
        base = f"{_execution_base(project, chat)}/{execution['execution_id']}"

        status, reliability = _json(harness, "GET", base + "/reliability")
        assert status == 200, reliability
        assert reliability["can_stop"] is True
        assert reliability["terminal"] is False

        stop_body = {"schema_version": "conversation-execution-stop-v1"}
        status, first = _json(harness, "POST", base + "/stop", body=stop_body)
        assert status == 200, first
        assert first["status"] == "cancel_requested"
        status, second = _json(harness, "POST", base + "/stop", body=stop_body)
        assert status == 200, second
        assert second["status"] == "cancel_requested"

        wrong_project = "project_" + "f" * 32
        wrong = base.replace(project["project_id"], wrong_project) + "/stop"
        status, rejected = _json(harness, "POST", wrong, body=stop_body)
        assert status == 409, rejected
        assert rejected["error"] == "conversation_reliability_conflict"

        delegate.release.set()
        terminal = _wait_terminal(harness, project, chat, execution["execution_id"])
        assert terminal["status"] == "cancelled"
        status, after = _json(harness, "POST", base + "/stop", body=stop_body)
        assert status == 200, after
        assert after["terminal"] is True
        assert after["can_stop"] is False
        assert after["can_retry"] is True
    finally:
        delegate.release.set()
        harness.close()


def test_reliability_routes_are_authenticated_query_free_and_classify_restart_interruption(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = _Harness(tmp_path / "server", _SequenceRunner([False]))
    try:
        project, chat = _create_project_chat(harness, workspace)
        execution = _submit(harness, project, chat, suffix="d")
        execution = _wait_terminal(harness, project, chat, execution["execution_id"])
        base = f"{_execution_base(project, chat)}/{execution['execution_id']}/reliability"

        status, unauthorized = _json(harness, "GET", base, authorized=False)
        assert status == 401 and unauthorized["error"] == "unauthorized"
        status, queried = _json(harness, "GET", base + "?retry=true")
        assert status == 400
        assert queried["error"] == "invalid_conversation_reliability_request"

        session_id = execution["session_id"]
        original_session = harness.service.session

        def interrupted_session(candidate: str):
            snapshot = original_session(candidate)
            if candidate != session_id:
                return snapshot
            return snapshot.model_copy(
                update={"failure_reason": "app_server_restart_interrupted_running_session"}
            )

        harness.service.session = interrupted_session  # type: ignore[method-assign]
        status, reliability = _json(harness, "GET", base)
        assert status == 200, reliability
        assert reliability["interrupted_by_restart"] is True
        assert reliability["can_retry"] is True
        assert reliability["can_continue"] is True
        assert reliability["can_stop"] is False
    finally:
        harness.close()
