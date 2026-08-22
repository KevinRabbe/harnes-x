from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from harness_x.app_server import (
    AppEventKind,
    AppServerService,
    AppSessionStatus,
    CodingSessionRequest,
    LocalOperatorHTTPServer,
)
from harness_x.app_server import operator_http_server
from harness_x.app_server.trace_export import (
    TraceExportNotTerminalError,
    TraceExportSource,
    ValidatedTraceExport,
    read_validated_trace_export,
)
from harness_x.core import EventId, EventType, SystemVersion, TaskId, TraceEvent, TraceId
from harness_x.core.errors import TraceCorruptionError
from harness_x.telemetry import TraceStore
from harness_x.telemetry.trace_store import TraceRecord


def _request_model(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="export the authoritative causal trace",
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def _write_trace(output: Path) -> tuple[Path, TraceId]:
    trace_id = TraceId.new()
    task_id = TaskId.new()
    path = output / f"{trace_id.value}.jsonl"
    store = TraceStore(path)
    base = datetime(2026, 8, 22, tzinfo=timezone.utc)
    store.append(
        TraceEvent(
            event_id=EventId.new(),
            trace_id=trace_id,
            task_id=task_id,
            step=1,
            timestamp=base,
            event_type=EventType.REASONING_REQUESTED,
            component="reasoning.service",
            system_version=SystemVersion(value="test"),
            metadata={"phase": "one"},
        )
    )
    store.append(
        TraceEvent(
            event_id=EventId.new(),
            trace_id=trace_id,
            task_id=task_id,
            step=2,
            timestamp=base + timedelta(seconds=1),
            event_type=EventType.TOOL_EXECUTION_FINISHED,
            component="tools.executor",
            system_version=SystemVersion(value="test"),
            metadata={"tool_name": "workspace_read", "success": True},
        )
    )
    return path, trace_id


def _stored_session(
    service: AppServerService,
    tmp_path: Path,
    *,
    terminal: bool,
    attach: bool = True,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    snapshot = service.store.create_session(
        _request_model(workspace),
        output_root=output,
    )
    snapshot = service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.RUNNING,
        kind=AppEventKind.SESSION_STARTED,
    )
    trace_path = None
    trace_id = None
    if attach:
        trace_path, trace_id = _write_trace(output)
        snapshot = service.store.attach_trace(
            snapshot.session_id,
            trace_id=trace_id.value,
            path=trace_path,
        )
    if terminal:
        snapshot = service.store.transition(
            snapshot.session_id,
            status=AppSessionStatus.SUCCEEDED,
            kind=AppEventKind.SESSION_COMPLETED,
        )
    return snapshot, trace_path, trace_id


def _http_request(server: LocalOperatorHTTPServer, path: str, *, authorized: bool) -> Request:
    headers = {"Accept": "application/x-ndjson"}
    if authorized:
        headers["Authorization"] = f"Bearer {server.token}"
    return Request(server.base_url + path, headers=headers, method="GET")


def test_validated_trace_export_binds_exact_source_and_attachment(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, trace_path, _ = _stored_session(service, tmp_path, terminal=True)
        assert trace_path is not None
        payload = trace_path.read_bytes()
        final = TraceRecord.model_validate_json(payload.splitlines()[-1])

        validated = read_validated_trace_export(
            snapshot=snapshot,
            events=service.store.events(snapshot.session_id),
        )

        assert validated.session_id == snapshot.session_id
        assert validated.trace_id == snapshot.trace_id
        assert validated.trace_path == snapshot.trace_path
        assert validated.source.payload == payload
        assert validated.source.source_bytes == len(payload)
        assert validated.source.source_sha256 == hashlib.sha256(payload).hexdigest()
        assert len(validated.records) == 2
        assert validated.final_event_hash == final.event_hash
        attachment = [
            event
            for event in service.store.events(snapshot.session_id)
            if event.kind == AppEventKind.TRACE_ATTACHED
        ][0]
        assert validated.attachment_event_sequence == attachment.sequence
        assert validated.attachment_event_hash == attachment.event_hash
    finally:
        service.close()


def test_trace_export_is_terminal_only(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, _, _ = _stored_session(service, tmp_path, terminal=False)
        with pytest.raises(TraceExportNotTerminalError, match="only after"):
            read_validated_trace_export(
                snapshot=snapshot,
                events=service.store.events(snapshot.session_id),
            )
    finally:
        service.close()


def test_trace_export_requires_durable_attachment_evidence(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, _, _ = _stored_session(service, tmp_path, terminal=True)
        events = tuple(
            event
            for event in service.store.events(snapshot.session_id)
            if event.kind != AppEventKind.TRACE_ATTACHED
        )
        with pytest.raises(TraceCorruptionError, match="exactly one durable"):
            read_validated_trace_export(snapshot=snapshot, events=events)
    finally:
        service.close()


def test_trace_export_rejects_snapshot_path_substitution(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, _, _ = _stored_session(service, tmp_path, terminal=True)
        substituted = snapshot.model_copy(
            update={"trace_path": str(Path(snapshot.output_root) / "other.jsonl")}
        )
        with pytest.raises(TraceCorruptionError, match="canonical session trace path"):
            read_validated_trace_export(
                snapshot=substituted,
                events=service.store.events(snapshot.session_id),
            )
    finally:
        service.close()


def test_trace_export_rejects_symlink_substitution(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, trace_path, _ = _stored_session(service, tmp_path, terminal=True)
        assert trace_path is not None
        external = tmp_path / "external.jsonl"
        external.write_bytes(trace_path.read_bytes())
        trace_path.unlink()
        try:
            os.symlink(external, trace_path)
        except (OSError, NotImplementedError):
            pytest.skip("symbolic links are not available")

        with pytest.raises(TraceCorruptionError, match="symbolic link"):
            read_validated_trace_export(
                snapshot=snapshot,
                events=service.store.events(snapshot.session_id),
            )
    finally:
        service.close()


def test_trace_export_rejects_parent_directory_symlink_substitution(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, trace_path, _ = _stored_session(service, tmp_path, terminal=True)
        assert trace_path is not None
        payload = trace_path.read_bytes()
        output = Path(snapshot.output_root)
        moved = tmp_path / "moved-output"
        replacement = tmp_path / "replacement-output"
        output.rename(moved)
        replacement.mkdir()
        (replacement / trace_path.name).write_bytes(payload)
        try:
            os.symlink(replacement, output, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("directory symbolic links are not available")

        with pytest.raises(TraceCorruptionError, match="symbolic-link path substitution"):
            read_validated_trace_export(
                snapshot=snapshot,
                events=service.store.events(snapshot.session_id),
            )
    finally:
        service.close()


def test_trace_export_rejects_terminal_partial_line(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, trace_path, _ = _stored_session(service, tmp_path, terminal=True)
        assert trace_path is not None
        with trace_path.open("ab") as handle:
            handle.write(b'{"record_schema_version":"partial"')

        with pytest.raises(TraceCorruptionError, match="incomplete final record"):
            read_validated_trace_export(
                snapshot=snapshot,
                events=service.store.events(snapshot.session_id),
            )
    finally:
        service.close()


def test_trace_export_rejects_complete_hash_chain_tamper(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, trace_path, _ = _stored_session(service, tmp_path, terminal=True)
        assert trace_path is not None
        rows = trace_path.read_text(encoding="utf-8").splitlines()
        first = json.loads(rows[0])
        first["event"]["metadata"]["phase"] = "tampered"
        rows[0] = json.dumps(first, separators=(",", ":"))
        trace_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

        with pytest.raises(TraceCorruptionError, match="event hash mismatch"):
            read_validated_trace_export(
                snapshot=snapshot,
                events=service.store.events(snapshot.session_id),
            )
    finally:
        service.close()


def test_trace_export_enforces_explicit_source_size_bound(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, trace_path, _ = _stored_session(service, tmp_path, terminal=True)
        assert trace_path is not None
        payload = trace_path.read_bytes()
        with pytest.raises(TraceCorruptionError, match="export limit"):
            read_validated_trace_export(
                snapshot=snapshot,
                events=service.store.events(snapshot.session_id),
                maximum_bytes=len(payload) - 1,
            )
    finally:
        service.close()


def test_http_trace_export_is_authenticated_and_returns_exact_verified_bytes(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot, trace_path, _ = _stored_session(service, tmp_path, terminal=True)
        assert trace_path is not None
        payload = trace_path.read_bytes()
        final = TraceRecord.model_validate_json(payload.splitlines()[-1])
        attachment = [
            event
            for event in service.store.events(snapshot.session_id)
            if event.kind == AppEventKind.TRACE_ATTACHED
        ][0]
        path = f"/v1/sessions/{snapshot.session_id}/trace/export"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_http_request(server, path, authorized=False), timeout=3.0)
        assert exc_info.value.code == 401

        with urlopen(_http_request(server, path, authorized=True), timeout=3.0) as response:
            body = response.read()
            assert response.status == 200
            assert response.headers.get("Content-Type") == "application/x-ndjson; charset=utf-8"
            assert response.headers.get("Content-Disposition") == 'attachment; filename="causal-trace.jsonl"'
            assert response.headers.get("Content-Length") == str(len(payload))
            assert response.headers.get("Cache-Control") == "no-store"
            assert response.headers.get("X-Harness-X-Trace-ID") == snapshot.trace_id
            assert response.headers.get("X-Harness-X-Trace-SHA256") == hashlib.sha256(payload).hexdigest()
            assert response.headers.get("X-Harness-X-Trace-Records") == "2"
            assert response.headers.get("X-Harness-X-Trace-Final-Event-Hash") == final.event_hash
            assert response.headers.get("X-Harness-X-Trace-Attachment-Event-Hash") == attachment.event_hash
        assert body == payload
    finally:
        server.close()
        service.close()


def test_http_trace_export_rejects_running_query_extra_path_and_missing_trace(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        running, _, _ = _stored_session(service, tmp_path, terminal=False)
        base = f"/v1/sessions/{running.session_id}/trace/export"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_http_request(server, base, authorized=True), timeout=3.0)
        assert exc_info.value.code == 409
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "trace_export_not_terminal"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_http_request(server, base + "?path=/etc/passwd", authorized=True), timeout=3.0)
        assert exc_info.value.code == 400
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "invalid_request"
        assert payload["detail"] == "trace export endpoint does not accept query parameters"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_http_request(server, base + "/trace.jsonl", authorized=True), timeout=3.0)
        assert exc_info.value.code == 404
    finally:
        server.close()
        service.close()

    other_root = tmp_path / "other"
    other_root.mkdir()
    service = AppServerService(other_root / "service")
    server = LocalOperatorHTTPServer(service, other_root / "transport", port=0)
    server.start_in_thread()
    try:
        terminal, _, _ = _stored_session(service, other_root, terminal=True, attach=False)
        path = f"/v1/sessions/{terminal.session_id}/trace/export"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_http_request(server, path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 404
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "trace_export_not_available"
    finally:
        server.close()
        service.close()


def test_http_trace_export_fails_visible_after_valid_json_tamper(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot, trace_path, _ = _stored_session(service, tmp_path, terminal=True)
        assert trace_path is not None
        rows = trace_path.read_text(encoding="utf-8").splitlines()
        first = json.loads(rows[0])
        first["event"]["metadata"]["phase"] = "tampered"
        rows[0] = json.dumps(first, separators=(",", ":"))
        trace_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

        path = f"/v1/sessions/{snapshot.session_id}/trace/export"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_http_request(server, path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 409
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "trace_corruption"
        assert "event hash mismatch" in payload["detail"]
    finally:
        server.close()
        service.close()


def test_http_trace_export_writes_validated_source_without_reopening_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AppServerService(tmp_path / "service")
    snapshot, _, _ = _stored_session(service, tmp_path, terminal=True, attach=False)
    synthetic = b'{"source":"already-validated"}\n'
    validated = ValidatedTraceExport(
        session_id=snapshot.session_id,
        trace_id="trace_" + "a" * 32,
        trace_path="/path/that/must/not/be-reopened.jsonl",
        source=TraceExportSource(
            payload=synthetic,
            source_bytes=len(synthetic),
            source_sha256=hashlib.sha256(synthetic).hexdigest(),
        ),
        records=(),
        attachment_event_sequence=7,
        attachment_event_hash="b" * 64,
        final_event_hash=None,
    )
    monkeypatch.setattr(
        operator_http_server,
        "read_validated_trace_export",
        lambda **_kwargs: validated,
    )
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        path = f"/v1/sessions/{snapshot.session_id}/trace/export"
        with urlopen(_http_request(server, path, authorized=True), timeout=3.0) as response:
            assert response.read() == synthetic
            assert response.headers.get("X-Harness-X-Trace-Records") == "0"
            assert response.headers.get("X-Harness-X-Trace-Final-Event-Hash") == "none"
    finally:
        server.close()
        service.close()
