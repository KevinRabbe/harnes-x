from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel

from harness_x.app_server.protocol import AppEventKind, AppSessionStatus, CodingSessionRequest
from harness_x.app_server.service import AppServerService
from harness_x.app_server.store import AppSessionStore
from harness_x.core import EventType, SystemClock, SystemVersion, TaskId, TraceId
from harness_x.telemetry import TraceRecorder, TraceStore


class _Report(BaseModel):
    succeeded: bool = True
    failure_reason: str | None = None


def test_historical_snapshot_remains_readable_after_workspace_and_plan_disappear(
    tmp_path: Path,
) -> None:
    service_root = tmp_path / "server"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan_path = tmp_path / "verification.json"
    plan_path.write_text("{}\n", encoding="utf-8")
    store = AppSessionStore(service_root / "sessions")
    request = CodingSessionRequest(
        workspace_root=workspace,
        task="historical task",
        model_profile="main",
        verification_plan_path=plan_path,
    )
    created = store.create_session(request, output_root=service_root / "runs" / "run")

    shutil.rmtree(workspace)
    plan_path.unlink()

    reopened = AppSessionStore(service_root / "sessions")
    historical = reopened.session(created.session_id)
    assert historical.status == AppSessionStatus.CREATED
    assert historical.request.workspace_root == workspace.resolve()
    assert historical.request.verification_plan_path == plan_path.resolve()


def test_service_restart_fails_requeued_created_session_when_launch_paths_disappeared(
    tmp_path: Path,
) -> None:
    service_root = tmp_path / "server"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AppSessionStore(service_root / "sessions")
    created = store.create_session(
        CodingSessionRequest(
            workspace_root=workspace,
            task="cannot safely requeue later",
            model_profile="main",
            verification_commands=("python -m pytest",),
        ),
        output_root=service_root / "runs" / "run",
    )
    shutil.rmtree(workspace)

    service = AppServerService(service_root, runner=lambda _: _Report())
    try:
        recovered = service.session(created.session_id)
        assert recovered.status == AppSessionStatus.FAILED
        assert "restart_launch_validation_failed" in (recovered.failure_reason or "")
        assert recovered.request.workspace_root == workspace.resolve()
    finally:
        service.close()


def test_service_restart_preserves_durable_trace_attachment_for_interrupted_run(
    tmp_path: Path,
) -> None:
    service_root = tmp_path / "server"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AppSessionStore(service_root / "sessions")
    created = store.create_session(
        CodingSessionRequest(
            workspace_root=workspace,
            task="preserve causal evidence through restart",
            model_profile="main",
            verification_commands=("python -m pytest",),
        ),
        output_root=service_root / "runs" / "run",
    )
    running = store.transition(
        created.session_id,
        status=AppSessionStatus.RUNNING,
        kind=AppEventKind.SESSION_STARTED,
    )
    trace_id = TraceId.new()
    trace_path = Path(running.output_root) / f"{trace_id.value}.jsonl"
    recorder = TraceRecorder(
        TraceStore(trace_path),
        trace_id,
        TaskId.new(),
        SystemVersion(value="test-app-server-restart"),
        SystemClock(),
    )
    recorder.emit(
        EventType.REASONING_REQUESTED,
        "test.restart.trace",
        metadata={"evidence": "before-restart"},
    )
    attached = store.attach_trace(
        running.session_id,
        trace_id=trace_id.value,
        path=trace_path,
    )
    assert attached.trace_id == trace_id.value
    assert sum(
        item.kind == AppEventKind.TRACE_ATTACHED for item in store.events(running.session_id)
    ) == 1

    service = AppServerService(service_root, runner=lambda _: _Report())
    try:
        recovered = service.session(running.session_id)
        assert recovered.status == AppSessionStatus.FAILED
        assert recovered.trace_id == trace_id.value
        assert recovered.trace_path == str(trace_path.resolve())
        assert "app_server_restart_interrupted_running_session" in (
            recovered.failure_reason or ""
        )
        assert sum(
            item.kind == AppEventKind.TRACE_ATTACHED
            for item in service.store.events(running.session_id)
        ) == 1
    finally:
        service.close()
