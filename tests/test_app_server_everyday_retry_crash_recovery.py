from __future__ import annotations

import http.client
import json
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


class _Harness:
    def __init__(self, root: Path, delegate: _SequenceRunner) -> None:
        broker = SensitiveActionApprovalBroker(root / "data" / "sensitive-approvals")
        runner = ApprovalAwareHarnessCodingRunner(broker)
        runner.delegate = delegate
        self.delegate = delegate
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
) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", harness.server.port, timeout=10)
    headers = {
        "Authorization": f"Bearer {harness.server.token}",
        "Accept": "application/json",
    }
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
            "name": "Retry crash recovery",
            "workspace_root": str(workspace),
            "default_model_profile": None,
        },
    )
    assert status == 201, project
    status, chat = _json(
        harness,
        "POST",
        f"/v1/projects/{project['project_id']}/chats",
        body={"schema_version": "create-chat-request-v1", "title": "Prepared retry"},
    )
    assert status == 201, chat
    return project, chat


def _execution_base(project: dict, chat: dict) -> str:
    return f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/executions"


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


def test_retry_record_before_plan_crash_resumes_same_execution_after_server_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "server"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first_delegate = _SequenceRunner([False])
    first = _Harness(root, first_delegate)
    retry_submission_id = "submission_" + "b" * 32
    try:
        project, chat = _create_project_chat(first, workspace)
        status, source = _json(
            first,
            "POST",
            _execution_base(project, chat),
            body={
                "schema_version": "conversation-execution-submit-v1",
                "submission_id": "submission_" + "a" * 32,
                "role": "user",
                "content": {"type": "text", "text": "Fail once, then retry"},
            },
        )
        assert status == 202, source
        source = _wait_terminal(first, project, chat, source["execution_id"])
        assert source["status"] == "failed"

        coordinator = first.server.conversation
        original_append_plan = coordinator.store.append_plan

        def crash_before_plan_append(plan):
            if Path(plan.output_root).name.startswith("conversation_reliability_"):
                raise RuntimeError("simulated retry crash after durable prepared intent")
            return original_append_plan(plan)

        coordinator.store.append_plan = crash_before_plan_append  # type: ignore[method-assign]
        retry_path = f"{_execution_base(project, chat)}/{source['execution_id']}/retry"
        retry_body = {
            "schema_version": "conversation-execution-retry-v1",
            "submission_id": retry_submission_id,
        }
        status, failed_attempt = _json(first, "POST", retry_path, body=retry_body)
        assert status == 409, failed_attempt
        assert failed_attempt["error"] == "conversation_reliability_corruption"

        prepared = coordinator.retry_store.record_for_submission(retry_submission_id)
        assert prepared is not None
        assert prepared.source_execution_id == source["execution_id"]
        assert coordinator.store.plan_for_submission(retry_submission_id) is None
        assert coordinator.settings_execution_store.snapshot(prepared.execution_id) is not None
        assert coordinator.resource_execution_store.snapshot(prepared.execution_id) is not None
        prepared_execution_id = prepared.execution_id
        assert first_delegate.calls == 1
    finally:
        first.close()

    second_delegate = _SequenceRunner([True])
    second = _Harness(root, second_delegate)
    try:
        retry_path = f"{_execution_base(project, chat)}/{source['execution_id']}/retry"
        retry_body = {
            "schema_version": "conversation-execution-retry-v1",
            "submission_id": retry_submission_id,
        }
        status, recovered = _json(second, "POST", retry_path, body=retry_body)
        assert status == 202, recovered
        assert recovered["source_execution_id"] == source["execution_id"]
        assert recovered["execution"]["execution_id"] == prepared_execution_id

        terminal = _wait_terminal(second, project, chat, prepared_execution_id)
        assert terminal["status"] == "succeeded"
        assert second_delegate.calls == 1

        coordinator = second.server.conversation
        record = coordinator.retry_store.record(prepared_execution_id)
        assert record is not None
        assert record.submission_id == retry_submission_id
        assert coordinator.store.plan_for_submission(retry_submission_id).execution_id == prepared_execution_id
    finally:
        second.close()
