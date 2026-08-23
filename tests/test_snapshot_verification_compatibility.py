from __future__ import annotations

from pathlib import Path

import pytest

from harness_x.app_server.evidence_manifest import (
    build_terminal_evidence_manifest,
    render_terminal_evidence_manifest,
)
from harness_x.app_server.protocol import AppEventKind, AppSessionStatus, CodingSessionRequest
from harness_x.app_server.service import AppServerService
import harness_x.snapshot_verification as snapshot_verification


def test_omitted_snapshot_delegates_without_m47_manifest_reread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    service = AppServerService(tmp_path / "service")
    try:
        request = CodingSessionRequest(
            workspace_root=workspace,
            task="preserve the frozen M45 verifier path when snapshot evidence is omitted",
            model_profile="main",
            verification_commands=("python -m pytest",),
        )
        snapshot = service.store.create_session(request, output_root=output)
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
        manifest = build_terminal_evidence_manifest(
            snapshot=snapshot,
            events=service.store.events(snapshot.session_id),
        )
        manifest_path = tmp_path / "session-evidence-manifest.json"
        manifest_path.write_bytes(render_terminal_evidence_manifest(manifest).payload)
    finally:
        service.close()

    def fail_m47_read(*args, **kwargs):
        raise AssertionError("M47-specific bounded read must not run without --snapshot")

    monkeypatch.setattr(snapshot_verification, "_bounded_regular_file", fail_m47_read)
    result = snapshot_verification.verify_portable_evidence_with_snapshot(manifest_path)

    assert result.base.session_id == snapshot.session_id
    assert result.snapshot_status == "not_supplied"
    assert result.snapshot_revision is None
