from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from harness_x.app_server import (
    AppEventKind,
    AppServerService,
    AppSessionStatus,
    CodingSessionRequest,
    EvidenceManifestCorruptionError,
    EvidenceManifestNotTerminalError,
    LocalOperatorHTTPServer,
    build_terminal_evidence_manifest,
    render_terminal_evidence_manifest,
)
from harness_x.core import EventId, EventType, SystemVersion, TaskId, TraceEvent, TraceId
from harness_x.telemetry import TraceStore


def _request_model(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="correlate terminal evidence",
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def _write_trace(output: Path) -> tuple[Path, TraceId]:
    trace_id = TraceId.new()
    trace_path = output / f"{trace_id.value}.jsonl"
    TraceStore(trace_path).append(
        TraceEvent(
            event_id=EventId.new(),
            trace_id=trace_id,
            task_id=TaskId.new(),
            step=1,
            timestamp=datetime(2026, 8, 23, tzinfo=timezone.utc),
            event_type=EventType.REASONING_COMPLETED,
            component="reasoning.service",
            system_version=SystemVersion(value="test"),
            metadata={"summary": "done"},
        )
    )
    return trace_path, trace_id


def _stored_terminal_session(
    service: AppServerService,
    tmp_path: Path,
    *,
    report: bool = True,
    trace: bool = True,
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
    if trace:
        trace_path, trace_id = _write_trace(output)
        snapshot = service.store.attach_trace(
            snapshot.session_id,
            trace_id=trace_id.value,
            path=trace_path,
        )

    report_path = None
    report_payload = None
    if report:
        report_path = output / "coding-task-report.json"
        report_payload = b'{"succeeded":true,"note":"terminal evidence"}\n'
        report_path.write_bytes(report_payload)
        snapshot = service.store.add_artifact(
            snapshot.session_id,
            artifact_kind="coding_task_report",
            path=report_path,
            source_bytes=len(report_payload),
            source_sha256=hashlib.sha256(report_payload).hexdigest(),
        )

    snapshot = service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.SUCCEEDED,
        kind=AppEventKind.SESSION_COMPLETED,
        coding_report_path=(str(report_path) if report_path is not None else None),
    )
    return snapshot, report_path, report_payload, trace_path, trace_id


def _http_request(server: LocalOperatorHTTPServer, path: str, *, authorized: bool) -> Request:
    headers = {"Accept": "application/json"}
    if authorized:
        headers["Authorization"] = f"Bearer {server.token}"
    return Request(server.base_url + path, headers=headers, method="GET")


def _canonical_manifest_material(manifest) -> bytes:
    return json.dumps(
        manifest.model_dump(mode="json", exclude={"fingerprint"}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def test_manifest_correlates_lifecycle_report_and_trace_without_source_paths(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, report_path, report_payload, trace_path, trace_id = _stored_terminal_session(
            service,
            tmp_path,
        )
        assert report_path is not None and report_payload is not None
        assert trace_path is not None and trace_id is not None
        events = service.store.events(snapshot.session_id)
        artifact = [event for event in events if event.kind == AppEventKind.ARTIFACT_AVAILABLE][0]
        attachment = [event for event in events if event.kind == AppEventKind.TRACE_ATTACHED][0]

        manifest = build_terminal_evidence_manifest(snapshot=snapshot, events=events)

        assert manifest.schema_version == "app-terminal-evidence-manifest-v1"
        assert manifest.session_id == snapshot.session_id
        assert manifest.lifecycle.status == "succeeded"
        assert manifest.lifecycle.snapshot_revision == snapshot.revision
        assert manifest.lifecycle.snapshot_fingerprint == snapshot.fingerprint
        assert manifest.lifecycle.event_count == snapshot.event_count == len(events)
        assert manifest.lifecycle.ledger_head_hash == snapshot.latest_event_hash == events[-1].event_hash
        assert manifest.lifecycle.ledger_head_kind == AppEventKind.SESSION_COMPLETED.value
        assert manifest.lifecycle.created_at == snapshot.created_at
        assert manifest.lifecycle.completed_at == snapshot.completed_at

        assert manifest.coding_report.availability == "available"
        assert manifest.coding_report.source_filename == "coding-task-report.json"
        assert manifest.coding_report.source_bytes == len(report_payload)
        assert manifest.coding_report.source_sha256 == hashlib.sha256(report_payload).hexdigest()
        assert manifest.coding_report.attestation_status == "verified"
        assert manifest.coding_report.attested_source_bytes == len(report_payload)
        assert manifest.coding_report.attested_source_sha256 == hashlib.sha256(report_payload).hexdigest()
        assert manifest.coding_report.artifact_event_sequence == artifact.sequence
        assert manifest.coding_report.artifact_event_hash == artifact.event_hash

        trace_payload = trace_path.read_bytes()
        assert manifest.causal_trace.availability == "available"
        assert manifest.causal_trace.source_filename == "causal-trace.jsonl"
        assert manifest.causal_trace.trace_id == trace_id.value
        assert manifest.causal_trace.source_bytes == len(trace_payload)
        assert manifest.causal_trace.source_sha256 == hashlib.sha256(trace_payload).hexdigest()
        assert manifest.causal_trace.record_count == 1
        assert manifest.causal_trace.final_event_hash is not None
        assert manifest.causal_trace.attachment_event_sequence == attachment.sequence
        assert manifest.causal_trace.attachment_event_hash == attachment.event_hash

        serialized = manifest.model_dump_json()
        assert str(report_path) not in serialized
        assert str(trace_path) not in serialized
        assert manifest.fingerprint == hashlib.sha256(_canonical_manifest_material(manifest)).hexdigest()
    finally:
        service.close()


def test_manifest_is_deterministic_and_render_digest_describes_exact_bytes(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, *_ = _stored_terminal_session(service, tmp_path)
        events = service.store.events(snapshot.session_id)

        first = build_terminal_evidence_manifest(snapshot=snapshot, events=events)
        second = build_terminal_evidence_manifest(snapshot=snapshot, events=events)
        first_rendered = render_terminal_evidence_manifest(first)
        second_rendered = render_terminal_evidence_manifest(second)

        assert first.fingerprint == second.fingerprint
        assert first_rendered.payload == second_rendered.payload
        assert first_rendered.payload.endswith(b"\n")
        assert first_rendered.source_bytes == len(first_rendered.payload)
        assert first_rendered.source_sha256 == hashlib.sha256(first_rendered.payload).hexdigest()
        parsed = json.loads(first_rendered.payload.decode("utf-8"))
        assert parsed["fingerprint"] == first.fingerprint
    finally:
        service.close()


def test_manifest_is_terminal_only(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    service = AppServerService(tmp_path / "service")
    try:
        snapshot = service.store.create_session(_request_model(workspace), output_root=output)
        snapshot = service.store.transition(
            snapshot.session_id,
            status=AppSessionStatus.RUNNING,
            kind=AppEventKind.SESSION_STARTED,
        )
        with pytest.raises(EvidenceManifestNotTerminalError, match="only after"):
            build_terminal_evidence_manifest(
                snapshot=snapshot,
                events=service.store.events(snapshot.session_id),
            )
    finally:
        service.close()


def test_manifest_explicitly_marks_absent_report_and_trace(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, *_ = _stored_terminal_session(
            service,
            tmp_path,
            report=False,
            trace=False,
        )
        manifest = build_terminal_evidence_manifest(
            snapshot=snapshot,
            events=service.store.events(snapshot.session_id),
        )
        assert manifest.coding_report.availability == "not_available"
        assert manifest.causal_trace.availability == "not_available"
    finally:
        service.close()


def test_manifest_rejects_report_event_without_snapshot_report_path(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, _, _, _, _ = _stored_terminal_session(
            service,
            tmp_path,
            report=True,
            trace=False,
        )
        contradictory = snapshot.model_copy(update={"coding_report_path": None})
        with pytest.raises(EvidenceManifestCorruptionError, match="artifact exists"):
            build_terminal_evidence_manifest(
                snapshot=contradictory,
                events=service.store.events(snapshot.session_id),
            )
    finally:
        service.close()


def test_manifest_rejects_trace_event_without_snapshot_trace_identity(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, *_ = _stored_terminal_session(
            service,
            tmp_path,
            report=False,
            trace=True,
        )
        contradictory = snapshot.model_copy(update={"trace_id": None, "trace_path": None})
        with pytest.raises(EvidenceManifestCorruptionError, match="trace attachment exists"):
            build_terminal_evidence_manifest(
                snapshot=contradictory,
                events=service.store.events(snapshot.session_id),
            )
    finally:
        service.close()


def test_manifest_rejects_lifecycle_head_disagreement(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, *_ = _stored_terminal_session(
            service,
            tmp_path,
            report=False,
            trace=False,
        )
        contradictory = snapshot.model_copy(update={"latest_event_hash": "0" * 64})
        with pytest.raises(EvidenceManifestCorruptionError, match="ledger head"):
            build_terminal_evidence_manifest(
                snapshot=contradictory,
                events=service.store.events(snapshot.session_id),
            )
    finally:
        service.close()


def test_manifest_rejects_broken_supplied_lifecycle_chain(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, *_ = _stored_terminal_session(
            service,
            tmp_path,
            report=False,
            trace=False,
        )
        events = list(service.store.events(snapshot.session_id))
        events[-1] = events[-1].model_copy(update={"previous_hash": "1" * 64})
        with pytest.raises(EvidenceManifestCorruptionError, match="previous hash"):
            build_terminal_evidence_manifest(snapshot=snapshot, events=tuple(events))
    finally:
        service.close()


def test_manifest_fails_closed_when_present_report_is_tampered(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, report_path, _, _, _ = _stored_terminal_session(
            service,
            tmp_path,
            report=True,
            trace=False,
        )
        assert report_path is not None
        original = report_path.read_bytes()
        tampered = original.replace(b"terminal evidence", b"terminal evidencf")
        assert len(tampered) == len(original)
        json.loads(tampered.decode("utf-8"))
        report_path.write_bytes(tampered)

        with pytest.raises(EvidenceManifestCorruptionError, match="coding report evidence is corrupt"):
            build_terminal_evidence_manifest(
                snapshot=snapshot,
                events=service.store.events(snapshot.session_id),
            )
    finally:
        service.close()


def test_manifest_fails_closed_when_present_trace_is_tampered(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot, _, _, trace_path, _ = _stored_terminal_session(
            service,
            tmp_path,
            report=False,
            trace=True,
        )
        assert trace_path is not None
        rows = trace_path.read_text(encoding="utf-8").splitlines()
        raw = json.loads(rows[0])
        raw["event"]["metadata"]["summary"] = "tampered"
        rows[0] = json.dumps(raw, separators=(",", ":"))
        trace_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

        with pytest.raises(EvidenceManifestCorruptionError, match="causal trace evidence is corrupt"):
            build_terminal_evidence_manifest(
                snapshot=snapshot,
                events=service.store.events(snapshot.session_id),
            )
    finally:
        service.close()


def test_http_manifest_requires_auth_and_returns_exact_hashed_body(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot, *_ = _stored_terminal_session(service, tmp_path)
        path = f"/v1/sessions/{snapshot.session_id}/evidence/manifest"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_http_request(server, path, authorized=False), timeout=3.0)
        assert exc_info.value.code == 401

        with urlopen(_http_request(server, path, authorized=True), timeout=3.0) as response:
            body = response.read()
            assert response.status == 200
            assert response.headers.get("Content-Type") == "application/json; charset=utf-8"
            assert response.headers.get("Content-Disposition") == (
                'attachment; filename="session-evidence-manifest.json"'
            )
            assert response.headers.get("Content-Length") == str(len(body))
            assert response.headers.get("Cache-Control") == "no-store"
            assert response.headers.get("X-Content-Type-Options") == "nosniff"
            assert response.headers.get("X-Harness-X-Evidence-Manifest-SHA256") == (
                hashlib.sha256(body).hexdigest()
            )
        payload = json.loads(body.decode("utf-8"))
        assert payload["schema_version"] == "app-terminal-evidence-manifest-v1"
        assert payload["session_id"] == snapshot.session_id
        assert payload["coding_report"]["availability"] == "available"
        assert payload["causal_trace"]["availability"] == "available"
    finally:
        server.close()
        service.close()


def test_http_manifest_allows_explicit_missing_components(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot, *_ = _stored_terminal_session(
            service,
            tmp_path,
            report=False,
            trace=False,
        )
        path = f"/v1/sessions/{snapshot.session_id}/evidence/manifest"
        with urlopen(_http_request(server, path, authorized=True), timeout=3.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["coding_report"] == {
            "schema_version": "app-coding-report-evidence-v1",
            "availability": "not_available",
        }
        assert payload["causal_trace"] == {
            "schema_version": "app-causal-trace-evidence-v1",
            "availability": "not_available",
        }
    finally:
        server.close()
        service.close()


def test_http_manifest_rejects_running_query_extra_path_and_corruption(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        output = tmp_path / "running-output"
        output.mkdir()
        running = service.store.create_session(_request_model(workspace), output_root=output)
        running = service.store.transition(
            running.session_id,
            status=AppSessionStatus.RUNNING,
            kind=AppEventKind.SESSION_STARTED,
        )
        running_path = f"/v1/sessions/{running.session_id}/evidence/manifest"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_http_request(server, running_path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 409
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "evidence_manifest_not_terminal"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _http_request(server, running_path + "?path=/etc/passwd", authorized=True),
                timeout=3.0,
            )
        assert exc_info.value.code == 400
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "invalid_request"
        assert payload["detail"] == "evidence manifest endpoint does not accept query parameters"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _http_request(server, running_path + "/bundle.zip", authorized=True),
                timeout=3.0,
            )
        assert exc_info.value.code == 404

        other_root = tmp_path / "terminal"
        other_root.mkdir()
        terminal, report_path, *_ = _stored_terminal_session(
            service,
            other_root,
            report=True,
            trace=False,
        )
        assert report_path is not None
        report_path.write_bytes(b'{"succeeded":false,"note":"terminal evidence"}\n')
        terminal_path = f"/v1/sessions/{terminal.session_id}/evidence/manifest"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_http_request(server, terminal_path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 409
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "evidence_corruption"
    finally:
        server.close()
        service.close()
