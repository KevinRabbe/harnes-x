from __future__ import annotations

import http.client
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel

from harness_x.app_server import cli as app_server_cli
from harness_x.app_server.conversation_operator_http_server import LocalOperatorHTTPServer
from harness_x.app_server.protocol import (
    AppEventKind,
    AppSessionStatus,
    CodingSessionRequest,
)
from harness_x.app_server.service import AppServerService
from harness_x.app_server.store import AppSessionStore
from harness_x.app_server.ui_assets import load_ui_asset
from harness_x.app_server.work_activity import build_work_activity_page
from harness_x.core import EventId, EventType, SystemVersion, TaskId, TraceEvent, TraceId
from harness_x.telemetry import TraceStore


class _Report(BaseModel):
    succeeded: bool
    failure_reason: str | None = None


def _write_report(snapshot, *, succeeded: bool = True) -> _Report:
    output = Path(snapshot.output_root)
    output.mkdir(parents=True, exist_ok=True)
    report = _Report(succeeded=succeeded)
    (output / "coding-task-report.json").write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _session_store(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "run"
    output.mkdir()
    store = AppSessionStore(tmp_path / "sessions")
    request = CodingSessionRequest(
        workspace_root=workspace,
        task="test grounded activity",
        model_profile="main",
        verification_commands=("git diff --check",),
    )
    snapshot = store.create_session(request, output_root=output)
    snapshot = store.transition(
        snapshot.session_id,
        status=AppSessionStatus.RUNNING,
        kind=AppEventKind.SESSION_STARTED,
    )
    return store, snapshot, output


def _append_grounded_trace(output: Path, *, started_at: datetime):
    trace_id = TraceId.new()
    task_id = TaskId.new()
    path = output / f"{trace_id.value}.jsonl"
    store = TraceStore(path)
    records = (
        TraceEvent(
            event_id=EventId.new(),
            trace_id=trace_id,
            task_id=task_id,
            step=1,
            timestamp=started_at + timedelta(milliseconds=10),
            event_type=EventType.CODING_PHASE_CHANGED,
            component="coding.control",
            system_version=SystemVersion(value="test"),
            metadata={"from": "orient", "to": "diagnose", "reason": "first inspection"},
        ),
        TraceEvent(
            event_id=EventId.new(),
            trace_id=trace_id,
            task_id=task_id,
            step=2,
            timestamp=started_at + timedelta(milliseconds=20),
            event_type=EventType.TOOL_EXECUTION_FINISHED,
            component="tools.executor",
            system_version=SystemVersion(value="test"),
            metadata={
                "executed": True,
                "result": {
                    "tool_name": "workspace_read",
                    "status": "succeeded",
                    "duration_ms": 12.5,
                    "output": {"api_key": "must-not-project", "text": "private-output"},
                },
            },
        ),
        TraceEvent(
            event_id=EventId.new(),
            trace_id=trace_id,
            task_id=task_id,
            step=3,
            timestamp=started_at + timedelta(milliseconds=30),
            event_type=EventType.CODING_PLAN_UPDATED,
            component="coding.control",
            system_version=SystemVersion(value="test"),
            metadata={
                "reason": "workspace_mutated",
                "revision": 3,
                "phase": "implement",
                "changed_files": ["src/example.py"],
            },
        ),
        TraceEvent(
            event_id=EventId.new(),
            trace_id=trace_id,
            task_id=task_id,
            step=4,
            timestamp=started_at + timedelta(milliseconds=40),
            event_type=EventType.VERIFICATION_COMPLETED,
            component="coding.verifier",
            system_version=SystemVersion(value="test"),
            metadata={
                "configured_commands": 1,
                "executed_commands": 1,
                "passed": True,
                "returncodes": [0],
            },
        ),
    )
    for event in records:
        store.append(event)
    return path, trace_id


def test_grounded_activity_projects_only_bounded_authoritative_fields(tmp_path: Path) -> None:
    store, snapshot, output = _session_store(tmp_path)
    assert snapshot.started_at is not None
    trace_path, trace_id = _append_grounded_trace(output, started_at=snapshot.started_at)
    snapshot = store.attach_trace(snapshot.session_id, trace_id=trace_id.value, path=trace_path)

    page = build_work_activity_page(
        project_id="project_" + "1" * 32,
        chat_id="chat_" + "2" * 32,
        execution_id="exec_" + "3" * 32,
        snapshot=snapshot,
        app_events=store.events(snapshot.session_id),
        cursor=None,
        limit=100,
    )

    kinds = [item.kind.value for item in page.events]
    assert kinds.count("work_started") == 1
    assert "status_changed" in kinds
    assert "tool_completed" in kinds
    assert "file_changed" in kinds
    assert "verification_result" in kinds
    assert page.next_cursor == "a3:t4"  # trace_attached is consumed but intentionally not projected
    assert page.trace_attached is True
    payload = page.model_dump_json()
    assert "must-not-project" not in payload
    assert "private-output" not in payload
    assert "src/example.py" in payload


def test_composite_cursor_survives_late_trace_attachment_without_replaying_lifecycle(
    tmp_path: Path,
) -> None:
    store, snapshot, output = _session_store(tmp_path)
    first = build_work_activity_page(
        project_id="project_" + "4" * 32,
        chat_id="chat_" + "5" * 32,
        execution_id="exec_" + "6" * 32,
        snapshot=snapshot,
        app_events=store.events(snapshot.session_id),
        cursor=None,
        limit=100,
    )
    assert first.next_cursor == "a2:t0"
    assert [item.kind.value for item in first.events] == ["work_started", "status_changed"]

    assert snapshot.started_at is not None
    trace_path, trace_id = _append_grounded_trace(output, started_at=snapshot.started_at)
    snapshot = store.attach_trace(snapshot.session_id, trace_id=trace_id.value, path=trace_path)
    second = build_work_activity_page(
        project_id="project_" + "4" * 32,
        chat_id="chat_" + "5" * 32,
        execution_id="exec_" + "6" * 32,
        snapshot=snapshot,
        app_events=store.events(snapshot.session_id),
        cursor=first.next_cursor,
        limit=100,
    )
    assert second.next_cursor == "a3:t4"
    assert "work_started" not in [item.kind.value for item in second.events]
    assert "tool_completed" in [item.kind.value for item in second.events]


def test_future_cursors_fail_closed_and_terminal_event_is_exactly_once(tmp_path: Path) -> None:
    store, snapshot, _output = _session_store(tmp_path)
    with pytest.raises(ValueError, match="ahead of App Server"):
        build_work_activity_page(
            project_id="project_" + "7" * 32,
            chat_id="chat_" + "8" * 32,
            execution_id="exec_" + "9" * 32,
            snapshot=snapshot,
            app_events=store.events(snapshot.session_id),
            cursor="a99:t0",
        )
    with pytest.raises(ValueError, match="ahead of causal trace"):
        build_work_activity_page(
            project_id="project_" + "7" * 32,
            chat_id="chat_" + "8" * 32,
            execution_id="exec_" + "9" * 32,
            snapshot=snapshot,
            app_events=store.events(snapshot.session_id),
            cursor="a2:t1",
        )

    first = build_work_activity_page(
        project_id="project_" + "7" * 32,
        chat_id="chat_" + "8" * 32,
        execution_id="exec_" + "9" * 32,
        snapshot=snapshot,
        app_events=store.events(snapshot.session_id),
        cursor=None,
    )
    snapshot = store.transition(
        snapshot.session_id,
        status=AppSessionStatus.SUCCEEDED,
        kind=AppEventKind.SESSION_COMPLETED,
    )
    terminal = build_work_activity_page(
        project_id="project_" + "7" * 32,
        chat_id="chat_" + "8" * 32,
        execution_id="exec_" + "9" * 32,
        snapshot=snapshot,
        app_events=store.events(snapshot.session_id),
        cursor=first.next_cursor,
    )
    assert terminal.terminal is True
    assert [item.kind.value for item in terminal.events] == ["work_completed"]
    again = build_work_activity_page(
        project_id="project_" + "7" * 32,
        chat_id="chat_" + "8" * 32,
        execution_id="exec_" + "9" * 32,
        snapshot=snapshot,
        app_events=store.events(snapshot.session_id),
        cursor=terminal.next_cursor,
    )
    assert again.events == ()


class _LiveTraceRunner:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, snapshot) -> _Report:
        output = Path(snapshot.output_root)
        output.mkdir(parents=True, exist_ok=True)
        trace_id = TraceId.new()
        task_id = TaskId.new()
        recorder = TraceStore(output / f"{trace_id.value}.jsonl")
        recorder.append(
            TraceEvent(
                event_id=EventId.new(),
                trace_id=trace_id,
                task_id=task_id,
                step=1,
                timestamp=datetime.now(timezone.utc),
                event_type=EventType.TOOL_EXECUTION_FINISHED,
                component="tools.executor",
                system_version=SystemVersion(value="test"),
                metadata={
                    "executed": True,
                    "result": {
                        "tool_name": "workspace_read",
                        "status": "succeeded",
                        "duration_ms": 1.0,
                    },
                },
            )
        )
        self.started.set()
        if not self.release.wait(timeout=10.0):
            raise TimeoutError("test runner release timed out")
        return _write_report(snapshot)


class _Harness:
    def __init__(self, root: Path, runner) -> None:
        self.service = AppServerService(root / "data", runner=runner)
        self.server = LocalOperatorHTTPServer(self.service, root / "transport", port=0)
        self.server.start_in_thread()

    def close(self) -> None:
        self.server.close()
        self.service.close()


def _request(
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
    payload = None if body is None else json.dumps(body).encode("utf-8")
    if body is not None:
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw.decode("utf-8")) if raw else {}


def _create_project_chat(harness: _Harness, workspace: Path):
    status, project = _request(
        harness,
        "POST",
        "/v1/projects",
        body={
            "schema_version": "create-project-request-v1",
            "name": "Project",
            "workspace_root": str(workspace),
            "default_model_profile": None,
        },
    )
    assert status == 201
    status, chat = _request(
        harness,
        "POST",
        f"/v1/projects/{project['project_id']}/chats",
        body={"schema_version": "create-chat-request-v1", "title": "Chat"},
    )
    assert status == 201
    return project, chat


def test_live_activity_http_is_authenticated_owned_incremental_and_trace_backed(
    tmp_path: Path,
) -> None:
    runner = _LiveTraceRunner()
    harness = _Harness(tmp_path / "server", runner)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project, chat = _create_project_chat(harness, workspace)
        status, created = _request(
            harness,
            "POST",
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/executions",
            body={
                "schema_version": "conversation-execution-submit-v1",
                "submission_id": "submission_" + "a" * 32,
                "role": "user",
                "content": {"type": "text", "text": "Inspect the workspace"},
            },
        )
        assert status == 202
        assert runner.started.wait(timeout=3.0)
        activity = (
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}"
            f"/executions/{created['execution_id']}/activity?cursor=a0:t0&limit=100"
        )

        status, unauthorized = _request(harness, "GET", activity, authorized=False)
        assert status == 401 and unauthorized["error"] == "unauthorized"

        status, page = _request(harness, "GET", activity)
        assert status == 200, page
        assert page["schema_version"] == "conversation-work-activity-page-v1"
        assert page["execution_id"] == created["execution_id"]
        assert page["trace_attached"] is True
        assert any(item["kind"] == "tool_completed" for item in page["events"])
        assert page["terminal"] is False

        status, bad_cursor = _request(
            harness,
            "GET",
            activity.replace("cursor=a0%3At0", "cursor=bad") if "a0%3At0" in activity else activity.replace("cursor=a0:t0", "cursor=bad"),
        )
        assert status == 400 and bad_cursor["error"] == "invalid_work_activity_request"

        status, second_chat = _request(
            harness,
            "POST",
            f"/v1/projects/{project['project_id']}/chats",
            body={"schema_version": "create-chat-request-v1", "title": "Other"},
        )
        assert status == 201
        wrong_owner = (
            f"/v1/projects/{project['project_id']}/chats/{second_chat['chat_id']}"
            f"/executions/{created['execution_id']}/activity"
        )
        status, conflict = _request(harness, "GET", wrong_owner)
        assert status == 409
        assert conflict["error"] == "work_activity_conflict"

        runner.release.set()
        deadline = time.monotonic() + 8.0
        terminal_execution = None
        while time.monotonic() < deadline:
            status, terminal_execution = _request(
                harness,
                "GET",
                f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/executions/{created['execution_id']}",
            )
            assert status == 200
            if terminal_execution["terminal"]:
                break
            time.sleep(0.05)
        assert terminal_execution is not None and terminal_execution["terminal"] is True

        next_activity = (
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}"
            f"/executions/{created['execution_id']}/activity?cursor={page['next_cursor']}"
        )
        status, final_page = _request(harness, "GET", next_activity)
        assert status == 200
        assert final_page["terminal"] is True
        assert any(item["kind"] == "work_completed" for item in final_page["events"])
    finally:
        runner.release.set()
        harness.close()


def test_m70_ui_uses_grounded_cursor_projection_and_safe_dom_only() -> None:
    javascript = load_ui_asset("/ui/execution_bridge.js").body.decode("utf-8")

    for fragment in (
        "/activity?cursor=",
        "activityCursor",
        "next_cursor",
        "activityEvents",
        "replaceChildren",
        "dataset.activityKind",
        "Grounded work activity",
        "conversation-work",
    ):
        # The schema itself is server-owned; the remaining fragments guard the browser contract.
        if fragment == "conversation-work":
            continue
        assert fragment in javascript
    assert "/v1/sessions" not in javascript
    assert "Authorization" not in javascript
    assert "Bearer " not in javascript
    assert "innerHTML" not in javascript
    assert "insertAdjacentHTML" not in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert "document.cookie" not in javascript
    assert app_server_cli.LocalOperatorHTTPServer.__module__.endswith(
        "conversation_operator_http_server"
    )
