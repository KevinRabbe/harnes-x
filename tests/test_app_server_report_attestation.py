from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

import harness_x.app_server.service as service_module
from harness_x.app_server import (
    AppEventKind,
    AppServerService,
    AppSessionStatus,
    AppSessionStore,
    CodingSessionRequest,
    build_coding_report_projection,
)
from harness_x.app_server.report_attestation import ReportAttestationCaptureError


class _Report(BaseModel):
    succeeded: bool = True
    failure_reason: str | None = None
    marker: str = "m39-attested"


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="attest the durable coding report",
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def _runner(snapshot):
    output = Path(snapshot.output_root)
    output.mkdir(parents=True, exist_ok=True)
    report = _Report()
    (output / "coding-task-report.json").write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _terminal(service: AppServerService, session_id: str):
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        snapshot = service.session(session_id)
        if snapshot.status.terminal:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("session did not become terminal")


def test_service_commits_report_attestation_before_terminal_transition(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AppServerService(tmp_path / "service", runner=_runner)
    try:
        created = service.create_session(_request(workspace))
        snapshot = _terminal(service, created.session_id)
        assert snapshot.status == AppSessionStatus.SUCCEEDED

        events = service.store.events(snapshot.session_id)
        artifact = next(event for event in events if event.kind == AppEventKind.ARTIFACT_AVAILABLE)
        terminal = next(event for event in events if event.kind == AppEventKind.SESSION_COMPLETED)
        source = Path(snapshot.coding_report_path).read_bytes()

        assert artifact.sequence < terminal.sequence
        assert terminal.previous_hash == artifact.event_hash
        assert artifact.payload["attestation_schema_version"] == (
            "app-artifact-content-attestation-v1"
        )
        assert artifact.payload["attestation_status"] == "captured"
        assert artifact.payload["source_digest_algorithm"] == "sha256"
        assert artifact.payload["source_bytes"] == len(source)
        assert artifact.payload["source_sha256"] == hashlib.sha256(source).hexdigest()
        assert artifact.verify_hash()

        projection = build_coding_report_projection(snapshot=snapshot, events=events)
        assert projection.attestation_status == "verified"
        assert projection.artifact_event_hash == artifact.event_hash
    finally:
        service.close()


def test_attestation_capture_failure_does_not_rewrite_coding_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def fail_capture(_path):
        raise ReportAttestationCaptureError("synthetic attestation capture failure")

    monkeypatch.setattr(service_module, "capture_report_attestation", fail_capture)
    service = AppServerService(tmp_path / "service", runner=_runner)
    try:
        created = service.create_session(_request(workspace))
        snapshot = _terminal(service, created.session_id)
        assert snapshot.status == AppSessionStatus.SUCCEEDED

        events = service.store.events(snapshot.session_id)
        artifact = next(event for event in events if event.kind == AppEventKind.ARTIFACT_AVAILABLE)
        terminal = next(event for event in events if event.kind == AppEventKind.SESSION_COMPLETED)
        assert artifact.sequence < terminal.sequence
        assert artifact.payload["attestation_status"] == "unavailable"
        assert "synthetic attestation capture failure" in artifact.payload["attestation_error"]

        projection = build_coding_report_projection(snapshot=snapshot, events=events)
        assert projection.attestation_status == "unavailable"
        assert "synthetic attestation capture failure" in projection.attestation_error
        assert projection.report["succeeded"] is True
    finally:
        service.close()


def test_store_rejects_partial_attestation_before_appending_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "run"
    output.mkdir()
    report_path = output / "coding-task-report.json"
    report_path.write_text('{"succeeded":true}\n', encoding="utf-8")

    store = AppSessionStore(tmp_path / "sessions")
    snapshot = store.create_session(_request(workspace), output_root=output)
    before = store.events(snapshot.session_id)

    with pytest.raises(ValueError, match="requires source_bytes and source_sha256 together"):
        store.add_artifact(
            snapshot.session_id,
            artifact_kind="coding_task_report",
            path=report_path,
            source_bytes=report_path.stat().st_size,
        )

    assert store.events(snapshot.session_id) == before


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"source_bytes": True, "source_sha256": "0" * 64}, "non-negative integer"),
        ({"source_bytes": 1, "source_sha256": "A" * 64}, "lowercase SHA-256"),
        (
            {
                "source_bytes": 1,
                "source_sha256": "0" * 64,
                "attestation_error": "also failed",
            },
            "both captured and unavailable",
        ),
        ({"attestation_error": "   "}, "cannot be blank"),
    ],
)
def test_store_rejects_malformed_attestation_before_event_append(
    tmp_path: Path,
    kwargs: dict[str, object],
    message: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "run"
    output.mkdir()
    report_path = output / "coding-task-report.json"
    report_path.write_text('{"succeeded":true}\n', encoding="utf-8")

    store = AppSessionStore(tmp_path / "sessions")
    snapshot = store.create_session(_request(workspace), output_root=output)
    before = store.events(snapshot.session_id)

    with pytest.raises(ValueError, match=message):
        store.add_artifact(
            snapshot.session_id,
            artifact_kind="coding_task_report",
            path=report_path,
            **kwargs,
        )

    assert store.events(snapshot.session_id) == before
