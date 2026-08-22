from __future__ import annotations

from pathlib import Path

import pytest

from harness_x.app_server import (
    AppEventKind,
    AppServerService,
    AppSessionStatus,
    CodingSessionRequest,
    build_terminal_evidence_manifest,
)


def _terminal_report(service: AppServerService, tmp_path: Path, *, state: str):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    snapshot = service.store.create_session(
        CodingSessionRequest(
            workspace_root=workspace,
            task="preserve report provenance",
            model_profile="main",
            verification_commands=("python -m pytest",),
        ),
        output_root=output,
    )
    snapshot = service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.RUNNING,
        kind=AppEventKind.SESSION_STARTED,
    )
    report_path = output / "coding-task-report.json"
    report_path.write_text('{"succeeded":true}\n', encoding="utf-8")
    if state == "legacy_unattested":
        snapshot = service.store.add_artifact(
            snapshot.session_id,
            artifact_kind="coding_task_report",
            path=report_path,
        )
    elif state == "unavailable":
        snapshot = service.store.add_artifact(
            snapshot.session_id,
            artifact_kind="coding_task_report",
            path=report_path,
            attestation_error="content attestation capture unavailable",
        )
    else:
        raise AssertionError(state)
    return service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.SUCCEEDED,
        kind=AppEventKind.SESSION_COMPLETED,
        coding_report_path=str(report_path),
    )


@pytest.mark.parametrize("state", ["legacy_unattested", "unavailable"])
def test_manifest_preserves_nonverified_report_provenance(tmp_path: Path, state: str) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot = _terminal_report(service, tmp_path, state=state)
        manifest = build_terminal_evidence_manifest(
            snapshot=snapshot,
            events=service.store.events(snapshot.session_id),
        )

        assert manifest.coding_report.availability == "available"
        assert manifest.coding_report.attestation_status == state
        assert manifest.coding_report.attested_source_bytes is None
        assert manifest.coding_report.attested_source_sha256 is None
        if state == "unavailable":
            assert manifest.coding_report.attestation_error == (
                "content attestation capture unavailable"
            )
        else:
            assert manifest.coding_report.attestation_error is None
        assert manifest.causal_trace.availability == "not_available"
    finally:
        service.close()
