from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import BaseModel

from harness_x.app_server.http_server import LocalAppHTTPServer
from harness_x.app_server.protocol import AppSessionStatus
from harness_x.app_server.service import AppServerService
from harness_x.core import EventType, SystemClock, SystemVersion, TaskId, TraceId
from harness_x.telemetry import TraceRecorder, TraceStore


class _Report(BaseModel):
    succeeded: bool = True
    failure_reason: str | None = None


def _write_report(output: Path) -> _Report:
    report = _Report()
    (output / "coding-task-report.json").write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return report


def _runner(snapshot):
    output = Path(snapshot.output_root)
    output.mkdir(parents=True, exist_ok=True)
    return _write_report(output)


def _trace_recorder(output: Path) -> TraceRecorder:
    output.mkdir(parents=True, exist_ok=True)
    trace_id = TraceId.new()
    return TraceRecorder(
        TraceStore(output / f"{trace_id.value}.jsonl"),
        trace_id,
        TaskId.new(),
        SystemVersion(value="test-app-server-http"),
        SystemClock(),
    )


def _trace_runner(snapshot):
    output = Path(snapshot.output_root)
    recorder = _trace_recorder(output)
    recorder.emit(
        EventType.REASONING_REQUESTED,
        "test.http.trace",
        metadata={"phase": "terminal"},
    )
    return _write_report(output)


class _LiveTraceRunner:
    def __init__(self) -> None:
        self.first_emitted = threading.Event()
        self.release = threading.Event()

    def __call__(self, snapshot):
        output = Path(snapshot.output_root)
        recorder = _trace_recorder(output)
        recorder.emit(
            EventType.REASONING_REQUESTED,
            "test.http.live-trace",
            metadata={"phase": "first"},
        )
        self.first_emitted.set()
        if not self.release.wait(timeout=5.0):
            raise RuntimeError("live trace test runner was not released")
        recorder.emit(
            EventType.REASONING_COMPLETED,
            "test.http.live-trace",
            metadata={"phase": "second"},
        )
        return _write_report(output)


def _json_request(
    server: LocalAppHTTPServer,
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    authorized: bool = True,
):
    data = None
    headers: dict[str, str] = {}
    if authorized:
        headers["Authorization"] = f"Bearer {server.token}"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(server.base_url + path, data=data, headers=headers, method=method)
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


def _read_sse_event(response) -> str:
    lines: list[str] = []
    while True:
        raw = response.readline()
        if not raw:
            break
        text = raw.decode("utf-8")
        if text == "\n":
            break
        lines.append(text)
    return "".join(lines)


def test_http_health_is_local_public_but_session_state_requires_token(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalAppHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        status, health = _json_request(server, "/v1/health", authorized=False)
        assert status == 200
        assert health["ok"] is True

        with pytest.raises(HTTPError) as exc_info:
            _json_request(server, "/v1/sessions", authorized=False)
        assert exc_info.value.code == 401
    finally:
        server.close()
        service.close()


def test_http_create_observe_events_and_sse_terminal_stream(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalAppHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        status, created = _json_request(
            server,
            "/v1/sessions",
            method="POST",
            payload={
                "workspace_root": str(workspace),
                "task": "repair feature",
                "model_profile": "main",
                "verification_commands": ["python -m pytest"],
            },
        )
        assert status == 202
        session_id = created["session_id"]
        terminal = _wait_terminal(service, session_id)
        assert terminal.status == AppSessionStatus.SUCCEEDED

        status, snapshot = _json_request(server, f"/v1/sessions/{session_id}")
        assert status == 200
        assert snapshot["status"] == "succeeded"
        assert snapshot["coding_report_path"].endswith("coding-task-report.json")

        status, page = _json_request(server, f"/v1/sessions/{session_id}/events?after=1")
        assert status == 200
        assert page["after"] == 1
        assert page["events"]
        assert all(item["sequence"] > 1 for item in page["events"])

        request = Request(
            server.base_url + f"/v1/sessions/{session_id}/events/stream?after=0",
            headers={"Authorization": f"Bearer {server.token}"},
            method="GET",
        )
        with urlopen(request, timeout=3.0) as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert "event: session_created" in body
        assert "event: session_completed" in body
        assert "data: {" in body
    finally:
        server.close()
        service.close()


def test_http_trace_stream_tails_authoritative_trace_while_session_is_running(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = _LiveTraceRunner()
    service = AppServerService(tmp_path / "service", runner=runner)
    server = LocalAppHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        _, created = _json_request(
            server,
            "/v1/sessions",
            method="POST",
            payload={
                "workspace_root": str(workspace),
                "task": "stream live trace",
                "model_profile": "main",
                "verification_commands": ["python -m pytest"],
            },
        )
        session_id = created["session_id"]
        assert runner.first_emitted.wait(timeout=3.0)
        assert service.session(session_id).status == AppSessionStatus.RUNNING

        request = Request(
            server.base_url + f"/v1/sessions/{session_id}/trace/stream?after=0",
            headers={
                "Authorization": f"Bearer {server.token}",
                "Accept": "text/event-stream",
            },
            method="GET",
        )
        with urlopen(request, timeout=5.0) as response:
            first = _read_sse_event(response)
            assert response.status == 200
            assert "id: 1\n" in first
            assert "event: trace_event\n" in first
            assert '"event_type":"reasoning_requested"' in first
            assert service.session(session_id).status == AppSessionStatus.RUNNING
            runner.release.set()
            remainder = response.read().decode("utf-8")

        terminal = _wait_terminal(service, session_id)
        assert terminal.status == AppSessionStatus.SUCCEEDED
        assert '"event_type":"reasoning_completed"' in remainder
        assert "id: 2\n" in remainder
        assert terminal.trace_id is not None
        assert terminal.trace_path is not None
    finally:
        runner.release.set()
        server.close()
        service.close()


def test_http_trace_page_rejects_complete_source_corruption(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AppServerService(tmp_path / "service", runner=_trace_runner)
    server = LocalAppHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        _, created = _json_request(
            server,
            "/v1/sessions",
            method="POST",
            payload={
                "workspace_root": str(workspace),
                "task": "project terminal trace",
                "model_profile": "main",
                "verification_commands": ["python -m pytest"],
            },
        )
        terminal = _wait_terminal(service, created["session_id"])
        assert terminal.trace_path is not None
        trace_path = Path(terminal.trace_path)
        rows = trace_path.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(rows[0])
        tampered["event"]["metadata"]["tampered"] = True
        rows[0] = json.dumps(tampered, separators=(",", ":"))
        trace_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

        with pytest.raises(HTTPError) as exc_info:
            _json_request(server, f"/v1/sessions/{terminal.session_id}/trace")
        assert exc_info.value.code == 409
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "trace_corruption"
        assert "event hash mismatch" in payload["detail"]
    finally:
        server.close()
        service.close()


def test_http_rejects_non_loopback_host_header(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalAppHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        request = Request(
            server.base_url + "/v1/health",
            headers={"Host": "attacker.example"},
            method="GET",
        )
        with pytest.raises(HTTPError) as exc_info:
            urlopen(request, timeout=3.0)
        assert exc_info.value.code == 400
    finally:
        server.close()
        service.close()


def test_http_cancel_terminal_session_returns_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalAppHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        _, created = _json_request(
            server,
            "/v1/sessions",
            method="POST",
            payload={
                "workspace_root": str(workspace),
                "task": "finish quickly",
                "model_profile": "main",
                "verification_commands": ["python -m pytest"],
            },
        )
        session_id = created["session_id"]
        _wait_terminal(service, session_id)
        with pytest.raises(HTTPError) as exc_info:
            _json_request(
                server,
                f"/v1/sessions/{session_id}/cancel",
                method="POST",
                payload={},
            )
        assert exc_info.value.code == 409
    finally:
        server.close()
        service.close()
