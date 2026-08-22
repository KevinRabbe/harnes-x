from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from harness_x.app_server.protocol import (
    AppEvent,
    AppEventKind,
    AppSessionStatus,
    CodingSessionRequest,
)
from harness_x.app_server.service import AppServerService
from harness_x.app_server.store import AppSessionStore


def _request(tmp_path: Path, *, task: str = "repair the project") -> CodingSessionRequest:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return CodingSessionRequest(
        workspace_root=workspace,
        task=task,
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def _wait(predicate, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for app-server condition")


def test_session_store_persists_hash_chained_lifecycle(tmp_path: Path) -> None:
    store = AppSessionStore(tmp_path / "state")
    created = store.create_session(_request(tmp_path), output_root=tmp_path / "run")
    assert created.status == AppSessionStatus.CREATED
    assert created.event_count == 1
    assert created.fingerprint

    running = store.transition(
        created.session_id,
        status=AppSessionStatus.RUNNING,
        kind=AppEventKind.SESSION_STARTED,
    )
    finished = store.transition(
        created.session_id,
        status=AppSessionStatus.SUCCEEDED,
        kind=AppEventKind.SESSION_COMPLETED,
        coding_report_path=str(tmp_path / "run" / "coding-task-report.json"),
    )

    assert running.fingerprint != created.fingerprint
    assert finished.status == AppSessionStatus.SUCCEEDED
    assert finished.completed_at is not None
    events = store.events(created.session_id)
    assert tuple(item.sequence for item in events) == (1, 2, 3)
    assert events[0].previous_hash is None
    assert events[1].previous_hash == events[0].event_hash
    assert events[2].previous_hash == events[1].event_hash
    assert all(item.verify_hash() for item in events)

    reopened = AppSessionStore(tmp_path / "state")
    assert reopened.session(created.session_id) == finished


def test_store_reconciles_append_before_snapshot_crash(tmp_path: Path) -> None:
    root = tmp_path / "state"
    store = AppSessionStore(root)
    snapshot = store.create_session(_request(tmp_path), output_root=tmp_path / "run")
    event = AppEvent.create(
        session_id=snapshot.session_id,
        sequence=2,
        kind=AppEventKind.SESSION_STARTED,
        payload={"status": AppSessionStatus.RUNNING.value},
        previous_hash=snapshot.latest_event_hash,
    )
    event_path = root / snapshot.session_id / "events.jsonl"
    with event_path.open("ab") as handle:
        handle.write(event.model_dump_json().encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())

    reopened = AppSessionStore(root)
    recovered = reopened.session(snapshot.session_id)
    assert recovered.status == AppSessionStatus.RUNNING
    assert recovered.event_count == 2
    assert recovered.latest_event_hash == event.event_hash


def test_event_ledger_tamper_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "state"
    store = AppSessionStore(root)
    snapshot = store.create_session(_request(tmp_path), output_root=tmp_path / "run")
    path = root / snapshot.session_id / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    raw = json.loads(lines[0])
    raw["payload"]["status"] = "failed"
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="hash mismatch"):
        AppSessionStore(root)


class _FakeReport(BaseModel):
    succeeded: bool
    failure_reason: str | None = None


class _GateRunner:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.release = threading.Event()

    def __call__(self, snapshot):
        self.started.append(snapshot.session_id)
        self.release.wait(timeout=2.0)
        output = Path(snapshot.output_root)
        output.mkdir(parents=True, exist_ok=True)
        report = _FakeReport(succeeded=True)
        (output / "coding-task-report.json").write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return report


def test_service_serializes_runs_and_cancels_queued_session(tmp_path: Path) -> None:
    runner = _GateRunner()
    service = AppServerService(tmp_path / "server", runner=runner)
    try:
        first = service.create_session(_request(tmp_path, task="first task"))
        second = service.create_session(_request(tmp_path, task="second task"))

        _wait(lambda: service.session(first.session_id).status == AppSessionStatus.RUNNING)
        assert service.session(second.session_id).status == AppSessionStatus.CREATED
        assert runner.started == [first.session_id]

        cancelled = service.cancel(second.session_id)
        assert cancelled.status == AppSessionStatus.CANCELLED
        assert second.session_id not in runner.started

        runner.release.set()
        _wait(lambda: service.session(first.session_id).status == AppSessionStatus.SUCCEEDED)
        events = service.store.events(first.session_id)
        assert AppEventKind.ARTIFACT_AVAILABLE in {item.kind for item in events}
        assert service.session(first.session_id).coding_report_path is not None
    finally:
        runner.release.set()
        service.close()


def test_service_marks_interrupted_running_session_failed_on_restart(tmp_path: Path) -> None:
    root = tmp_path / "server"
    store = AppSessionStore(root / "sessions")
    snapshot = store.create_session(_request(tmp_path), output_root=root / "runs" / "run")
    store.transition(
        snapshot.session_id,
        status=AppSessionStatus.RUNNING,
        kind=AppEventKind.SESSION_STARTED,
    )

    service = AppServerService(root, runner=lambda _: _FakeReport(succeeded=True))
    try:
        recovered = service.session(snapshot.session_id)
        assert recovered.status == AppSessionStatus.FAILED
        assert "restart_interrupted" in (recovered.failure_reason or "")
    finally:
        service.close()
