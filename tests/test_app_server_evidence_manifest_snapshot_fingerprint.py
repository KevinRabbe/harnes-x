from pathlib import Path

import pytest

from harness_x.app_server import (
    AppEventKind,
    AppServerService,
    AppSessionStatus,
    CodingSessionRequest,
    EvidenceManifestCorruptionError,
    build_terminal_evidence_manifest,
)


def test_manifest_rejects_stale_snapshot_fingerprint(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    service = AppServerService(tmp_path / "service")
    try:
        snapshot = service.store.create_session(
            CodingSessionRequest(
                workspace_root=workspace,
                task="verify snapshot fingerprint",
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
        snapshot = service.store.transition(
            snapshot.session_id,
            status=AppSessionStatus.SUCCEEDED,
            kind=AppEventKind.SESSION_COMPLETED,
        )
        tampered = snapshot.model_copy(update={"fingerprint": "0" * 64})

        with pytest.raises(
            EvidenceManifestCorruptionError,
            match="fingerprint does not match snapshot contents",
        ):
            build_terminal_evidence_manifest(
                snapshot=tampered,
                events=service.store.events(snapshot.session_id),
            )
    finally:
        service.close()
