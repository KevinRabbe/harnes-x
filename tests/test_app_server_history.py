from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel

from harness_x.app_server.protocol import AppSessionStatus, CodingSessionRequest
from harness_x.app_server.service import AppServerService
from harness_x.app_server.store import AppSessionStore


class _Report(BaseModel):
    succeeded: bool = True
    failure_reason: str | None = None


def test_historical_snapshot_remains_readable_after_workspace_and_plan_disappear(
    tmp_path: Path,
) -> None:
    service_root = tmp_path / "server"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan_path = tmp_path / "verification.json"
    plan_path.write_text("{}\n", encoding="utf-8")
    store = AppSessionStore(service_root / "sessions")
    request = CodingSessionRequest(
        workspace_root=workspace,
        task="historical task",
        model_profile="main",
        verification_plan_path=plan_path,
    )
    created = store.create_session(request, output_root=service_root / "runs" / "run")

    shutil.rmtree(workspace)
    plan_path.unlink()

    reopened = AppSessionStore(service_root / "sessions")
    historical = reopened.session(created.session_id)
    assert historical.status == AppSessionStatus.CREATED
    assert historical.request.workspace_root == workspace.resolve()
    assert historical.request.verification_plan_path == plan_path.resolve()


def test_service_restart_fails_requeued_created_session_when_launch_paths_disappeared(
    tmp_path: Path,
) -> None:
    service_root = tmp_path / "server"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AppSessionStore(service_root / "sessions")
    created = store.create_session(
        CodingSessionRequest(
            workspace_root=workspace,
            task="cannot safely requeue later",
            model_profile="main",
            verification_commands=("python -m pytest",),
        ),
        output_root=service_root / "runs" / "run",
    )
    shutil.rmtree(workspace)

    service = AppServerService(service_root, runner=lambda _: _Report())
    try:
        recovered = service.session(created.session_id)
        assert recovered.status == AppSessionStatus.FAILED
        assert "restart_launch_validation_failed" in (recovered.failure_reason or "")
        assert recovered.request.workspace_root == workspace.resolve()
    finally:
        service.close()
