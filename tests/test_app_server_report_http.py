from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import BaseModel

from harness_x.app_server import AppServerService, CodingSessionRequest, LocalOperatorHTTPServer


class _Report(BaseModel):
    succeeded: bool = True
    failure_reason: str | None = None
    note: str = "<img src=x onerror=alert(1)>"


def _runner(snapshot):
    output = Path(snapshot.output_root)
    output.mkdir(parents=True, exist_ok=True)
    report = _Report()
    (output / "coding-task-report.json").write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _completed_session(service: AppServerService, workspace: Path):
    created = service.create_session(
        CodingSessionRequest(
            workspace_root=workspace,
            task="serve the durable coding report",
            model_profile="main",
            verification_commands=("python -m pytest",),
        )
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        snapshot = service.session(created.session_id)
        if snapshot.status.terminal:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("session did not become terminal")


def _request(server: LocalOperatorHTTPServer, path: str, *, authorized: bool):
    headers = {"Accept": "application/json"}
    if authorized:
        headers["Authorization"] = f"Bearer {server.token}"
    return Request(server.base_url + path, headers=headers, method="GET")


def test_http_report_projection_is_authenticated_and_read_only(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot = _completed_session(service, workspace)
        path = f"/v1/sessions/{snapshot.session_id}/report"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_request(server, path, authorized=False), timeout=3.0)
        assert exc_info.value.code == 401

        with urlopen(_request(server, path, authorized=True), timeout=3.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["schema_version"] == "app-coding-report-projection-v1"
        assert payload["session_id"] == snapshot.session_id
        assert payload["report"]["succeeded"] is True
        assert payload["report"]["note"] == "<img src=x onerror=alert(1)>"
        assert payload["source_path"] == snapshot.coding_report_path
        assert len(payload["source_sha256"]) == 64
    finally:
        server.close()
        service.close()


def test_http_report_projection_returns_404_before_report_exists(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        created = service.store.create_session(
            CodingSessionRequest(
                workspace_root=workspace,
                task="report not ready",
                model_profile="main",
                verification_commands=("python -m pytest",),
            ),
            output_root=service.run_root / "manual",
        )
        path = f"/v1/sessions/{created.session_id}/report"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_request(server, path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 404
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "report_not_available"
    finally:
        server.close()
        service.close()


def test_http_report_projection_fails_visible_after_source_tamper(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot = _completed_session(service, workspace)
        Path(snapshot.coding_report_path).write_text('{"broken":', encoding="utf-8")
        path = f"/v1/sessions/{snapshot.session_id}/report"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_request(server, path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 409
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "report_corruption"
        assert "valid JSON" in payload["detail"]
    finally:
        server.close()
        service.close()


def test_operator_transport_does_not_accept_caller_selected_report_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot = _completed_session(service, workspace)
        base = f"/v1/sessions/{snapshot.session_id}/report"
        for path in (
            base + "/coding-task-report.json",
            base + "?path=/etc/passwd",
        ):
            if "?" in path:
                # Query parameters do not become a filesystem selector; canonical report still wins.
                with urlopen(_request(server, path, authorized=True), timeout=3.0) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                assert payload["source_path"] == snapshot.coding_report_path
            else:
                with pytest.raises(HTTPError) as exc_info:
                    urlopen(_request(server, path, authorized=True), timeout=3.0)
                assert exc_info.value.code == 404
    finally:
        server.close()
        service.close()
