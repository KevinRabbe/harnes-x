from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from harness_x.app_server.lifecycle_export import (
    MAX_LIFECYCLE_EXPORT_BYTES,
    LifecycleExportCorruptionError,
    LifecycleExportNotTerminalError,
    LifecycleExportTooLargeError,
    LifecycleLedgerExport,
    build_lifecycle_ledger_export,
    render_lifecycle_ledger_export,
)
from harness_x.app_server.operator_http_server import LocalOperatorHTTPServer
from harness_x.app_server.protocol import AppEvent, AppEventKind, AppSessionStatus, CodingSessionRequest
from harness_x.app_server.service import AppServerService


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="export the terminal lifecycle ledger",
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def _terminal_session(service: AppServerService, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
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
    return snapshot, service.store.events(snapshot.session_id), output


def _get(server: LocalOperatorHTTPServer, path: str, *, authorized: bool) -> Request:
    headers = {"Accept": "application/json"}
    if authorized:
        headers["Authorization"] = f"Bearer {server.token}"
    return Request(server.base_url + path, headers=headers, method="GET")


def test_lifecycle_export_revalidates_snapshot_and_complete_event_chain(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, events, output = _terminal_session(service, tmp_path)
        export = build_lifecycle_ledger_export(snapshot=snapshot, events=events)
        rendered = render_lifecycle_ledger_export(export)

        assert export.schema_version == "app-lifecycle-ledger-export-v1"
        assert export.session_id == snapshot.session_id
        assert export.status == "succeeded"
        assert export.snapshot_revision == snapshot.revision
        assert export.snapshot_fingerprint == snapshot.fingerprint
        assert export.event_count == snapshot.event_count == len(events)
        assert export.ledger_head_hash == snapshot.latest_event_hash == events[-1].event_hash
        assert export.ledger_head_kind == AppEventKind.SESSION_COMPLETED.value
        assert export.created_at == snapshot.created_at
        assert export.completed_at == snapshot.completed_at
        assert export.events == events
        assert all(event.verify_hash() for event in export.events)

        raw = json.loads(rendered.payload.decode("utf-8"))
        assert raw["events"][0]["payload"]["output_root"] == str(output.resolve())
        assert "request" not in raw
        assert "workspace_root" not in raw
        assert "task" not in raw
        assert rendered.payload.endswith(b"\n")
        assert rendered.source_bytes == len(rendered.payload)
        assert rendered.source_sha256 == hashlib.sha256(rendered.payload).hexdigest()
        assert rendered.event_count == len(events)
        assert rendered.ledger_head_hash == events[-1].event_hash

        second = render_lifecycle_ledger_export(
            build_lifecycle_ledger_export(snapshot=snapshot, events=events)
        )
        assert second.payload == rendered.payload
        assert second.source_sha256 == rendered.source_sha256
    finally:
        service.close()


def test_lifecycle_export_is_terminal_only_and_rejects_stale_snapshot_fingerprint(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    service = AppServerService(tmp_path / "service")
    try:
        running = service.store.create_session(_request(workspace), output_root=output)
        running = service.store.transition(
            running.session_id,
            status=AppSessionStatus.RUNNING,
            kind=AppEventKind.SESSION_STARTED,
        )
        with pytest.raises(LifecycleExportNotTerminalError, match="only after"):
            build_lifecycle_ledger_export(
                snapshot=running,
                events=service.store.events(running.session_id),
            )

        terminal = service.store.transition(
            running.session_id,
            status=AppSessionStatus.SUCCEEDED,
            kind=AppEventKind.SESSION_COMPLETED,
        )
        stale = terminal.model_copy(update={"fingerprint": "0" * 64})
        with pytest.raises(LifecycleExportCorruptionError, match="fingerprint"):
            build_lifecycle_ledger_export(
                snapshot=stale,
                events=service.store.events(terminal.session_id),
            )
    finally:
        service.close()


@pytest.mark.parametrize(
    ("mutation", "pattern"),
    [
        (lambda event: event.model_copy(update={"session_id": "app_" + "f" * 32}), "cross-session"),
        (lambda event: event.model_copy(update={"sequence": event.sequence + 2}), "non-contiguous"),
        (lambda event: event.model_copy(update={"previous_hash": "1" * 64}), "previous hash"),
        (lambda event: event.model_copy(update={"event_hash": "2" * 64}), "hash mismatch"),
    ],
)
def test_lifecycle_export_rejects_corrupt_supplied_events(tmp_path: Path, mutation, pattern: str) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, events, _ = _terminal_session(service, tmp_path)
        broken = list(events)
        broken[-1] = mutation(broken[-1])
        with pytest.raises(LifecycleExportCorruptionError, match=pattern):
            build_lifecycle_ledger_export(snapshot=snapshot, events=tuple(broken))
    finally:
        service.close()


def test_lifecycle_export_rejects_snapshot_count_and_head_disagreement(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, events, _ = _terminal_session(service, tmp_path)

        wrong_count_raw = snapshot.model_dump(mode="json")
        wrong_count_raw["event_count"] = snapshot.event_count + 1
        wrong_count = type(snapshot).model_validate(wrong_count_raw)
        assert wrong_count.fingerprint != snapshot.fingerprint
        with pytest.raises(LifecycleExportCorruptionError, match="event_count"):
            build_lifecycle_ledger_export(snapshot=wrong_count, events=events)

        wrong_head_raw = snapshot.model_dump(mode="json")
        wrong_head_raw["latest_event_hash"] = "0" * 64
        wrong_head = type(snapshot).model_validate(wrong_head_raw)
        assert wrong_head.fingerprint != snapshot.fingerprint
        with pytest.raises(LifecycleExportCorruptionError, match="ledger head"):
            build_lifecycle_ledger_export(snapshot=wrong_head, events=events)
    finally:
        service.close()


def test_lifecycle_render_enforces_four_mib_ceiling() -> None:
    event = AppEvent.create(
        session_id="app_" + "a" * 32,
        sequence=1,
        kind=AppEventKind.SESSION_COMPLETED,
        payload={"large": "x" * MAX_LIFECYCLE_EXPORT_BYTES},
    )
    export = LifecycleLedgerExport(
        session_id=event.session_id,
        status="succeeded",
        snapshot_revision=1,
        snapshot_fingerprint="1" * 64,
        event_count=1,
        ledger_head_hash=event.event_hash,
        ledger_head_kind=event.kind.value,
        created_at=event.created_at,
        completed_at=event.created_at,
        events=(event,),
    )
    with pytest.raises(LifecycleExportTooLargeError, match="byte limit"):
        render_lifecycle_ledger_export(export)


def test_http_lifecycle_export_requires_auth_and_returns_exact_deterministic_body(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot, events, _ = _terminal_session(service, tmp_path)
        path = f"/v1/sessions/{snapshot.session_id}/lifecycle/export"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path, authorized=False), timeout=3.0)
        assert exc_info.value.code == 401

        bodies = []
        for _ in range(2):
            with urlopen(_get(server, path, authorized=True), timeout=3.0) as response:
                body = response.read()
                bodies.append(body)
                assert response.status == 200
                assert response.headers.get("Content-Type") == "application/json; charset=utf-8"
                assert response.headers.get("Content-Disposition") == (
                    'attachment; filename="session-lifecycle-ledger.json"'
                )
                assert response.headers.get("Content-Length") == str(len(body))
                assert response.headers.get("Cache-Control") == "no-store"
                assert response.headers.get("X-Content-Type-Options") == "nosniff"
                assert response.headers.get("Referrer-Policy") == "no-referrer"
                assert response.headers.get("X-Harness-X-Lifecycle-SHA256") == (
                    hashlib.sha256(body).hexdigest()
                )
                assert response.headers.get("X-Harness-X-Lifecycle-Events") == str(len(events))
                assert response.headers.get("X-Harness-X-Lifecycle-Head-Hash") == events[-1].event_hash
        assert bodies[0] == bodies[1]
        payload = json.loads(bodies[0].decode("utf-8"))
        assert payload["schema_version"] == "app-lifecycle-ledger-export-v1"
        assert payload["session_id"] == snapshot.session_id
        assert payload["event_count"] == len(events)
        assert payload["events"][-1]["event_hash"] == snapshot.latest_event_hash
    finally:
        server.close()
        service.close()


def test_http_lifecycle_export_rejects_running_query_and_extra_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        running = service.store.create_session(_request(workspace), output_root=output)
        running = service.store.transition(
            running.session_id,
            status=AppSessionStatus.RUNNING,
            kind=AppEventKind.SESSION_STARTED,
        )
        path = f"/v1/sessions/{running.session_id}/lifecycle/export"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 409
        assert json.loads(exc_info.value.read())["error"] == "lifecycle_export_not_terminal"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path + "?after=1", authorized=True), timeout=3.0)
        assert exc_info.value.code == 400
        payload = json.loads(exc_info.value.read())
        assert payload["error"] == "invalid_request"
        assert payload["detail"] == "lifecycle export endpoint does not accept query parameters"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path + "/events.json", authorized=True), timeout=3.0)
        assert exc_info.value.code == 404
    finally:
        server.close()
        service.close()
