from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from harness_x.app_server import (
    AppEvent,
    AppEventKind,
    AppSessionStatus,
    AppSessionStore,
    CodingSessionRequest,
    ReportCorruptionError,
    ReportUnavailableError,
    build_coding_report_projection,
)


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="project durable coding report",
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def _terminal_report(
    tmp_path: Path,
    payload: bytes = b'{"succeeded":true}\n',
    *,
    attested: bool = False,
    unavailable: bool = False,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "run"
    output.mkdir()
    store = AppSessionStore(tmp_path / "sessions")
    snapshot = store.create_session(_request(workspace), output_root=output)
    store.transition(
        snapshot.session_id,
        status=AppSessionStatus.RUNNING,
        kind=AppEventKind.SESSION_STARTED,
    )
    report_path = output / "coding-task-report.json"
    report_path.write_bytes(payload)
    artifact_kwargs: dict[str, object] = {}
    if attested:
        artifact_kwargs.update(
            source_bytes=len(payload),
            source_sha256=hashlib.sha256(payload).hexdigest(),
        )
    elif unavailable:
        artifact_kwargs["attestation_error"] = "synthetic capture failure"
    store.add_artifact(
        snapshot.session_id,
        artifact_kind="coding_task_report",
        path=report_path,
        **artifact_kwargs,
    )
    snapshot = store.transition(
        snapshot.session_id,
        status=AppSessionStatus.SUCCEEDED,
        kind=AppEventKind.SESSION_COMPLETED,
        coding_report_path=str(report_path),
    )
    return store, snapshot, report_path


def test_legacy_coding_report_projection_requires_exact_durable_source(tmp_path: Path) -> None:
    payload = b'{"succeeded":true,"verification":{"passed":true}}\n'
    store, snapshot, report_path = _terminal_report(tmp_path, payload)

    projection = build_coding_report_projection(
        snapshot=snapshot,
        events=store.events(snapshot.session_id),
    )

    assert projection.schema_version == "app-coding-report-projection-v2"
    assert projection.session_id == snapshot.session_id
    assert projection.source_path == str(report_path.resolve())
    assert projection.source_bytes == len(payload)
    assert projection.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert projection.attestation_status == "legacy_unattested"
    assert projection.attested_source_bytes is None
    assert projection.attested_source_sha256 is None
    assert projection.report["succeeded"] is True
    artifact = [
        event
        for event in store.events(snapshot.session_id)
        if event.kind == AppEventKind.ARTIFACT_AVAILABLE
    ][0]
    assert projection.artifact_event_sequence == artifact.sequence
    assert projection.artifact_event_hash == artifact.event_hash


def test_attested_coding_report_projection_verifies_durable_content_identity(tmp_path: Path) -> None:
    payload = b'{"succeeded":true,"value":1}\n'
    store, snapshot, _ = _terminal_report(tmp_path, payload, attested=True)

    projection = build_coding_report_projection(
        snapshot=snapshot,
        events=store.events(snapshot.session_id),
    )

    digest = hashlib.sha256(payload).hexdigest()
    assert projection.attestation_status == "verified"
    assert projection.attested_source_bytes == len(payload)
    assert projection.attested_source_sha256 == digest
    assert projection.source_sha256 == digest
    assert projection.attestation_error is None


def test_attested_projection_rejects_same_length_valid_json_tamper(tmp_path: Path) -> None:
    payload = b'{"succeeded":true,"value":1}\n'
    store, snapshot, report_path = _terminal_report(tmp_path, payload, attested=True)
    tampered = b'{"succeeded":true,"value":2}\n'
    assert len(tampered) == len(payload)
    report_path.write_bytes(tampered)

    with pytest.raises(ReportCorruptionError, match="SHA-256.*durable attestation"):
        build_coding_report_projection(
            snapshot=snapshot,
            events=store.events(snapshot.session_id),
        )


def test_unavailable_attestation_remains_explicit_without_claiming_verification(tmp_path: Path) -> None:
    store, snapshot, _ = _terminal_report(tmp_path, unavailable=True)

    projection = build_coding_report_projection(
        snapshot=snapshot,
        events=store.events(snapshot.session_id),
    )

    assert projection.attestation_status == "unavailable"
    assert projection.attestation_error == "synthetic capture failure"
    assert projection.attested_source_bytes is None
    assert projection.attested_source_sha256 is None


def test_projection_rejects_partial_attestation_metadata(tmp_path: Path) -> None:
    store, snapshot, _ = _terminal_report(tmp_path)
    events = list(store.events(snapshot.session_id))
    index = next(
        i for i, event in enumerate(events) if event.kind == AppEventKind.ARTIFACT_AVAILABLE
    )
    original = events[index]
    payload = dict(original.payload)
    payload.update(
        {
            "attestation_schema_version": "app-artifact-content-attestation-v1",
            "attestation_status": "captured",
            "source_digest_algorithm": "sha256",
            "source_bytes": 10,
        }
    )
    events[index] = AppEvent.create(
        session_id=original.session_id,
        sequence=original.sequence,
        kind=original.kind,
        payload=payload,
        previous_hash=original.previous_hash,
        created_at=original.created_at,
    )

    with pytest.raises(ReportCorruptionError, match="captured attestation is incomplete"):
        build_coding_report_projection(snapshot=snapshot, events=tuple(events))


def test_coding_report_projection_is_unavailable_before_terminal_report(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AppSessionStore(tmp_path / "sessions")
    snapshot = store.create_session(_request(workspace), output_root=tmp_path / "run")

    with pytest.raises(ReportUnavailableError, match="not available"):
        build_coding_report_projection(
            snapshot=snapshot,
            events=store.events(snapshot.session_id),
        )


def test_coding_report_projection_requires_artifact_ledger_evidence(tmp_path: Path) -> None:
    store, snapshot, _ = _terminal_report(tmp_path)
    events = tuple(
        event
        for event in store.events(snapshot.session_id)
        if event.kind != AppEventKind.ARTIFACT_AVAILABLE
    )

    with pytest.raises(ReportCorruptionError, match="exactly one durable"):
        build_coding_report_projection(snapshot=snapshot, events=events)


def test_coding_report_projection_rejects_snapshot_path_substitution(tmp_path: Path) -> None:
    store, snapshot, _ = _terminal_report(tmp_path)
    substituted = snapshot.model_copy(
        update={"coding_report_path": str(tmp_path / "outside.json")}
    )

    with pytest.raises(ReportCorruptionError, match="canonical session report path"):
        build_coding_report_projection(
            snapshot=substituted,
            events=store.events(snapshot.session_id),
        )


def test_coding_report_projection_rejects_symlink_substitution(tmp_path: Path) -> None:
    store, snapshot, report_path = _terminal_report(tmp_path)
    external = tmp_path / "external.json"
    external.write_text('{"succeeded":false}\n', encoding="utf-8")
    report_path.unlink()
    try:
        os.symlink(external, report_path)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available")

    with pytest.raises(ReportCorruptionError, match="symbolic link"):
        build_coding_report_projection(
            snapshot=snapshot,
            events=store.events(snapshot.session_id),
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"\xff\xfe", "valid UTF-8"),
        (b'{"broken":', "valid JSON"),
        (b"[1,2,3]\n", "root must be an object"),
    ],
)
def test_coding_report_projection_rejects_invalid_source_bytes(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    store, snapshot, _ = _terminal_report(tmp_path, payload)

    with pytest.raises(ReportCorruptionError, match=message):
        build_coding_report_projection(
            snapshot=snapshot,
            events=store.events(snapshot.session_id),
        )


def test_coding_report_projection_enforces_explicit_size_bound(tmp_path: Path) -> None:
    store, snapshot, _ = _terminal_report(tmp_path, b'{"value":"1234567890"}\n')

    with pytest.raises(ReportCorruptionError, match="limit"):
        build_coding_report_projection(
            snapshot=snapshot,
            events=store.events(snapshot.session_id),
            maximum_bytes=8,
        )


def test_coding_report_projection_rejects_invalid_requested_bound(tmp_path: Path) -> None:
    store, snapshot, _ = _terminal_report(tmp_path)

    with pytest.raises(ValueError, match="maximum_bytes"):
        build_coding_report_projection(
            snapshot=snapshot,
            events=store.events(snapshot.session_id),
            maximum_bytes=0,
        )
