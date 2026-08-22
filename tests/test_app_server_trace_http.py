from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

from pydantic import BaseModel

from harness_x.app_server.http_server import LocalAppHTTPServer
from harness_x.app_server.protocol import AppSessionStatus, CodingSessionRequest
from harness_x.app_server.service import AppServerService
from harness_x.core import EventType, SystemClock, SystemVersion, TaskId, TraceId
from harness_x.telemetry import TraceRecorder, TraceStore


class _Report(BaseModel):
    succeeded: bool = True
    failure_reason: str | None = None


class _PartialTraceRunner:
    def __init__(self) -> None:
        self.partial_written = threading.Event()
        self.release = threading.Event()

    def __call__(self, snapshot):
        output = Path(snapshot.output_root)
        output.mkdir(parents=True, exist_ok=True)
        trace_id = TraceId.new()
        trace_path = output / f"{trace_id.value}.jsonl"
        recorder = TraceRecorder(
            TraceStore(trace_path),
            trace_id,
            TaskId.new(),
            SystemVersion(value="test-app-server-partial-trace"),
            SystemClock(),
        )
        recorder.emit(
            EventType.REASONING_REQUESTED,
            "test.http.partial-trace",
            metadata={"phase": "complete-record"},
        )
        with trace_path.open("ab") as handle:
            handle.write(b'{"record_schema_version":"1"')
            handle.flush()
            os.fsync(handle.fileno())
        self.partial_written.set()
        if not self.release.wait(timeout=5.0):
            raise RuntimeError("partial trace test runner was not released")

        payload = trace_path.read_bytes()
        boundary = payload.rfind(b"\n")
        if boundary < 0:
            raise RuntimeError("partial trace fixture lost its complete record boundary")
        trace_path.write_bytes(payload[: boundary + 1])
        recorder.emit(
            EventType.REASONING_COMPLETED,
            "test.http.partial-trace",
            metadata={"phase": "after-partial-write"},
        )
        report = _Report()
        (output / "coding-task-report.json").write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return report


def _json_request(server: LocalAppHTTPServer, path: str):
    request = Request(
        server.base_url + path,
        headers={"Authorization": f"Bearer {server.token}"},
        method="GET",
    )
    with urlopen(request, timeout=3.0) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _wait_terminal(service: AppServerService, session_id: str):
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        snapshot = service.session(session_id)
        if snapshot.status.terminal:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("session did not become terminal")


def test_http_trace_page_tolerates_only_running_final_partial_record(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = _PartialTraceRunner()
    service = AppServerService(tmp_path / "service", runner=runner)
    server = LocalAppHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        created = service.create_session(
            CodingSessionRequest(
                workspace_root=workspace,
                task="read trace during append",
                model_profile="main",
                verification_commands=("python -m pytest",),
            )
        )
        assert runner.partial_written.wait(timeout=3.0)
        assert service.session(created.session_id).status == AppSessionStatus.RUNNING

        status, running_page = _json_request(
            server, f"/v1/sessions/{created.session_id}/trace"
        )
        assert status == 200
        assert running_page["trace_attached"] is True
        assert running_page["final_partial_line_ignored"] is True
        assert [item["step"] for item in running_page["events"]] == [1]
        assert running_page["events"][0]["event_type"] == "reasoning_requested"

        runner.release.set()
        terminal = _wait_terminal(service, created.session_id)
        assert terminal.status == AppSessionStatus.SUCCEEDED

        status, terminal_page = _json_request(
            server, f"/v1/sessions/{created.session_id}/trace"
        )
        assert status == 200
        assert terminal_page["final_partial_line_ignored"] is False
        assert [item["step"] for item in terminal_page["events"]] == [1, 2]
        assert terminal_page["events"][1]["event_type"] == "reasoning_completed"
    finally:
        runner.release.set()
        server.close()
        service.close()
