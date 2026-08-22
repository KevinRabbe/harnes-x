from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import BaseModel

from harness_x.app_server import (
    AppEventKind,
    AppServerService,
    AppSessionStatus,
    CodingSessionRequest,
    LocalOperatorHTTPServer,
)
from harness_x.app_server import operator_http_server as operator_http_module
from harness_x.app_server.report_attestation import ReportSource
from harness_x.app_server.report_projection import ValidatedCodingReport


class _Report(BaseModel):
    succeeded: bool = True
    failure_reason: str | None = None
    value: int = 1


def _report_bytes(value: int = 1) -> bytes:
    return (_Report(value=value).model_dump_json(indent=2) + "\n").encode("utf-8")


def _runner(snapshot):
    output = Path(snapshot.output_root)
    output.mkdir(parents=True, exist_ok=True)
    report = _Report()
    (output / "coding-task-report.json").write_bytes(_report_bytes())
    return report


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="export exact durable coding report bytes",
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def _completed_session(service: AppServerService, workspace: Path):
    created = service.create_session(_request(workspace))
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        snapshot = service.session(created.session_id)
        if snapshot.status.terminal:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("session did not become terminal")


def _manual_terminal_report(
    service: AppServerService,
    workspace: Path,
    *,
    payload: bytes,
    unavailable: bool = False,
):
    output = service.run_root / ("unavailable" if unavailable else "legacy")
    output.mkdir(parents=True, exist_ok=True)
    snapshot = service.store.create_session(_request(workspace), output_root=output)
    service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.RUNNING,
        kind=AppEventKind.SESSION_STARTED,
    )
    report_path = output / "coding-task-report.json"
    report_path.write_bytes(payload)
    kwargs: dict[str, object] = {}
    if unavailable:
        kwargs["attestation_error"] = "synthetic attestation capture failure"
    service.store.add_artifact(
        snapshot.session_id,
        artifact_kind="coding_task_report",
        path=report_path,
        **kwargs,
    )
    return service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.SUCCEEDED,
        kind=AppEventKind.SESSION_COMPLETED,
        coding_report_path=str(report_path),
    )


def _get(
    server: LocalOperatorHTTPServer,
    path: str,
    *,
    authorized: bool = True,
):
    headers = {"Accept": "application/json"}
    if authorized:
        headers["Authorization"] = f"Bearer {server.token}"
    return Request(server.base_url + path, headers=headers, method="GET")


def test_verified_report_export_returns_exact_attested_source_bytes_and_headers(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    expected = _report_bytes()
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot = _completed_session(service, workspace)
        events = service.store.events(snapshot.session_id)
        artifact = next(event for event in events if event.kind == AppEventKind.ARTIFACT_AVAILABLE)
        path = f"/v1/sessions/{snapshot.session_id}/report/export"

        with urlopen(_get(server, path), timeout=3.0) as response:
            body = response.read()

        digest = hashlib.sha256(expected).hexdigest()
        assert response.status == 200
        assert body == expected
        assert response.headers["Content-Type"] == "application/json; charset=utf-8"
        assert response.headers["Content-Disposition"] == 'attachment; filename="coding-task-report.json"'
        assert response.headers["Content-Length"] == str(len(expected))
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Harness-X-Report-SHA256"] == digest
        assert response.headers["X-Harness-X-Report-Attestation"] == "verified"
        assert response.headers["X-Harness-X-Artifact-Event-Hash"] == artifact.event_hash

        projection_path = f"/v1/sessions/{snapshot.session_id}/report"
        with urlopen(_get(server, projection_path), timeout=3.0) as response:
            projection = json.loads(response.read().decode("utf-8"))
        assert projection["schema_version"] == "app-coding-report-projection-v2"
        assert projection["source_sha256"] == digest
        assert projection["attestation_status"] == "verified"
    finally:
        server.close()
        service.close()


@pytest.mark.parametrize(
    ("unavailable", "expected_status"),
    [
        (False, "legacy_unattested"),
        (True, "unavailable"),
    ],
)
def test_export_preserves_nonverified_provenance_without_promoting_it(
    tmp_path: Path,
    unavailable: bool,
    expected_status: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = b'{"succeeded":true,"legacy":true}\n'
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot = _manual_terminal_report(
            service,
            workspace,
            payload=payload,
            unavailable=unavailable,
        )
        path = f"/v1/sessions/{snapshot.session_id}/report/export"
        with urlopen(_get(server, path), timeout=3.0) as response:
            body = response.read()
        assert body == payload
        assert response.headers["X-Harness-X-Report-Attestation"] == expected_status
        assert response.headers["X-Harness-X-Report-SHA256"] == hashlib.sha256(payload).hexdigest()
    finally:
        server.close()
        service.close()


def test_export_rejects_unauthenticated_queries_extra_paths_and_unavailable_report(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        created = service.store.create_session(
            _request(workspace),
            output_root=service.run_root / "pending",
        )
        base = f"/v1/sessions/{created.session_id}/report/export"

        with pytest.raises(HTTPError) as unauthorized:
            urlopen(_get(server, base, authorized=False), timeout=3.0)
        assert unauthorized.value.code == 401

        with pytest.raises(HTTPError) as query:
            urlopen(_get(server, base + "?path=/etc/passwd"), timeout=3.0)
        assert query.value.code == 400
        query_payload = json.loads(query.value.read().decode("utf-8"))
        assert query_payload["error"] == "invalid_request"

        with pytest.raises(HTTPError) as extra_path:
            urlopen(_get(server, base + "/anything"), timeout=3.0)
        assert extra_path.value.code == 404

        with pytest.raises(HTTPError) as not_ready:
            urlopen(_get(server, base), timeout=3.0)
        assert not_ready.value.code == 404
        not_ready_payload = json.loads(not_ready.value.read().decode("utf-8"))
        assert not_ready_payload["error"] == "report_not_available"
    finally:
        server.close()
        service.close()


def test_export_fails_before_success_response_after_same_length_valid_json_tamper(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot = _completed_session(service, workspace)
        report_path = Path(snapshot.coding_report_path)
        original = report_path.read_bytes()
        tampered = original.replace(b'"value": 1', b'"value": 2')
        assert tampered != original
        assert len(tampered) == len(original)
        assert isinstance(json.loads(tampered.decode("utf-8")), dict)
        report_path.write_bytes(tampered)

        path = f"/v1/sessions/{snapshot.session_id}/report/export"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path), timeout=3.0)
        assert exc_info.value.code == 409
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "report_corruption"
        assert "durable attestation" in payload["detail"]
    finally:
        server.close()
        service.close()


def test_export_handler_writes_the_exact_validated_source_without_reopening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        created = service.store.create_session(
            _request(workspace),
            output_root=service.run_root / "synthetic",
        )
        exact = b'{"same_validated_read":true}\n'
        source = ReportSource(
            payload=exact,
            source_bytes=len(exact),
            source_sha256=hashlib.sha256(exact).hexdigest(),
        )
        synthetic = ValidatedCodingReport(
            session_id=created.session_id,
            artifact_event_sequence=1,
            artifact_event_hash="a" * 64,
            source_path=str(service.run_root / "synthetic" / "coding-task-report.json"),
            source=source,
            attestation_status="legacy_unattested",
            attested_source_bytes=None,
            attested_source_sha256=None,
            attestation_error=None,
            report={"same_validated_read": True},
        )
        calls = 0

        def fake_read_validated_coding_report(*, snapshot, events):
            nonlocal calls
            calls += 1
            assert snapshot.session_id == created.session_id
            assert events == service.store.events(created.session_id)
            return synthetic

        monkeypatch.setattr(
            operator_http_module,
            "read_validated_coding_report",
            fake_read_validated_coding_report,
        )
        path = f"/v1/sessions/{created.session_id}/report/export"
        with urlopen(_get(server, path), timeout=3.0) as response:
            body = response.read()
        assert calls == 1
        assert body == exact
        assert response.headers["Content-Length"] == str(len(exact))
        assert response.headers["X-Harness-X-Report-SHA256"] == source.source_sha256
    finally:
        server.close()
        service.close()
