from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from harness_x.app_server.lifecycle_export import build_lifecycle_ledger_export
from harness_x.app_server.operator_http_server import LocalOperatorHTTPServer
from harness_x.app_server.protocol import AppEventKind, AppSessionStatus, CodingSessionRequest
from harness_x.app_server.service import AppServerService


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="preserve lifecycle evidence independently of artifact sources",
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def _authorized_get(server: LocalOperatorHTTPServer, path: str) -> Request:
    return Request(
        server.base_url + path,
        headers={"Authorization": f"Bearer {server.token}", "Accept": "application/json"},
        method="GET",
    )


def test_http_lifecycle_export_maps_durable_event_file_corruption_to_409(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot = service.store.create_session(_request(workspace), output_root=output)
        snapshot = service.store.transition(
            snapshot.session_id,
            status=AppSessionStatus.RUNNING,
            kind=AppEventKind.SESSION_STARTED,
        )
        snapshot = service.store.transition(
            snapshot.session_id,
            status=AppSessionStatus.SUCCEEDED,
            kind=AppEventKind.SESSION_COMPLETED,
        )
        events_path = service.store.root / snapshot.session_id / "events.jsonl"
        rows = events_path.read_text(encoding="utf-8").splitlines()
        raw = json.loads(rows[-1])
        raw["event_hash"] = "0" * 64
        rows[-1] = json.dumps(raw, separators=(",", ":"))
        events_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

        path = f"/v1/sessions/{snapshot.session_id}/lifecycle/export"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_authorized_get(server, path), timeout=3.0)
        assert exc_info.value.code == 409
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "lifecycle_corruption"
        assert "hash mismatch" in payload["detail"]
    finally:
        server.close()
        service.close()


def test_lifecycle_validation_does_not_reopen_report_or_trace_sources(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    service = AppServerService(tmp_path / "service")
    try:
        snapshot = service.store.create_session(_request(workspace), output_root=output)
        snapshot = service.store.transition(
            snapshot.session_id,
            status=AppSessionStatus.RUNNING,
            kind=AppEventKind.SESSION_STARTED,
        )

        trace_id = "trace_" + "a" * 32
        missing_trace = output / f"{trace_id}.jsonl"
        snapshot = service.store.attach_trace(
            snapshot.session_id,
            trace_id=trace_id,
            path=missing_trace,
        )

        report = output / "coding-task-report.json"
        report.write_text('{"ok":true}\n', encoding="utf-8")
        snapshot = service.store.add_artifact(
            snapshot.session_id,
            artifact_kind="coding_task_report",
            path=report,
        )
        report.unlink()

        snapshot = service.store.transition(
            snapshot.session_id,
            status=AppSessionStatus.SUCCEEDED,
            kind=AppEventKind.SESSION_COMPLETED,
            coding_report_path=str(report),
        )
        events = service.store.events(snapshot.session_id)
        export = build_lifecycle_ledger_export(snapshot=snapshot, events=events)

        assert export.event_count == len(events)
        assert export.ledger_head_hash == events[-1].event_hash
        assert any(event.payload.get("path") == str(report.resolve()) for event in export.events)
        assert any(event.payload.get("trace_path") == str(missing_trace.resolve()) for event in export.events)
    finally:
        service.close()
