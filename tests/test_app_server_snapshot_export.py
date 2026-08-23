from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from harness_x.app_server import LocalOperatorHTTPServer
from harness_x.app_server.protocol import AppEventKind, AppSessionStatus, CodingSessionRequest
from harness_x.app_server.service import AppServerService
from harness_x.app_server.snapshot_export import (
    MAX_SESSION_SNAPSHOT_EXPORT_BYTES,
    SnapshotExportCorruptionError,
    SnapshotExportNotTerminalError,
    SnapshotExportTooLargeError,
    canonical_snapshot_material,
    render_terminal_session_snapshot,
)


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="export the complete portable session fingerprint preimage",
        model_profile="main",
        verification_commands=("python -m pytest",),
        project_memory_root=workspace / ".memory",
        project_memory_key="portable/session",
        max_reasoning_steps=17,
        max_tool_actions=23,
        max_output_tokens=4096,
    )


def _terminal_session(service: AppServerService, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    snapshot = service.store.create_session(_request(workspace), output_root=output)
    snapshot = service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.RUNNING,
        kind=AppEventKind.SESSION_STARTED,
    )
    return service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.SUCCEEDED,
        kind=AppEventKind.SESSION_COMPLETED,
    )


def _get(server: LocalOperatorHTTPServer, path: str, *, authorized: bool) -> Request:
    headers = {"Accept": "application/json"}
    if authorized:
        headers["Authorization"] = f"Bearer {server.token}"
    return Request(server.base_url + path, headers=headers, method="GET")


def test_snapshot_export_contains_complete_fingerprint_preimage_and_exact_identity(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot = _terminal_session(service, tmp_path)
        rendered = render_terminal_session_snapshot(
            snapshot=snapshot,
            expected_session_id=snapshot.session_id,
        )

        assert rendered.payload.endswith(b"\n")
        assert rendered.source_bytes == len(rendered.payload)
        assert rendered.source_sha256 == hashlib.sha256(rendered.payload).hexdigest()
        assert rendered.fingerprint == snapshot.fingerprint
        assert rendered.revision == snapshot.revision

        raw = json.loads(rendered.payload.decode("utf-8"))
        assert raw["schema_version"] == "app-session-snapshot-v1"
        assert raw["request"]["schema_version"] == "app-coding-session-request-v1"
        assert raw["session_id"] == snapshot.session_id
        assert raw["status"] == "succeeded"
        assert raw["request"]["task"] == snapshot.request.task
        assert raw["request"]["workspace_root"] == snapshot.request.workspace_root
        assert raw["request"]["verification_commands"] == ["python -m pytest"]
        assert raw["request"]["project_memory_root"] == snapshot.request.project_memory_root
        assert raw["request"]["project_memory_key"] == "portable/session"
        assert raw["output_root"] == snapshot.output_root
        assert raw["event_count"] == snapshot.event_count
        assert raw["latest_event_hash"] == snapshot.latest_event_hash
        assert raw["fingerprint"] == snapshot.fingerprint

        material = dict(raw)
        supplied = material.pop("fingerprint")
        assert supplied == hashlib.sha256(canonical_snapshot_material(material)).hexdigest()

        second = render_terminal_session_snapshot(snapshot=snapshot)
        assert second.payload == rendered.payload
        assert second.source_sha256 == rendered.source_sha256
    finally:
        service.close()


def test_snapshot_export_is_terminal_only_and_rejects_stale_fingerprint(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    service = AppServerService(tmp_path / "service")
    try:
        running = service.store.create_session(_request(workspace), output_root=output)
        running = service.store.transition(
            running.session_id,
            status=AppSessionStatus.RUNNING,
            kind=AppEventKind.SESSION_STARTED,
        )
        with pytest.raises(SnapshotExportNotTerminalError, match="only after"):
            render_terminal_session_snapshot(snapshot=running)

        terminal = service.store.transition(
            running.session_id,
            status=AppSessionStatus.SUCCEEDED,
            kind=AppEventKind.SESSION_COMPLETED,
        )
        stale = terminal.model_copy(update={"fingerprint": "0" * 64})
        with pytest.raises(SnapshotExportCorruptionError, match="fingerprint"):
            render_terminal_session_snapshot(snapshot=stale)

        with pytest.raises(SnapshotExportCorruptionError, match="identity"):
            render_terminal_session_snapshot(
                snapshot=terminal,
                expected_session_id="app_" + "f" * 32,
            )
    finally:
        service.close()


def test_snapshot_export_fingerprint_check_does_not_reresolve_persisted_request_paths(
    tmp_path: Path,
) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot = _terminal_session(service, tmp_path)
        original_workspace = Path(snapshot.request.workspace_root)
        moved_workspace = tmp_path / "moved-workspace"
        original_workspace.rename(moved_workspace)
        replacement = tmp_path / "replacement-workspace"
        replacement.mkdir()
        try:
            original_workspace.symlink_to(replacement, target_is_directory=True)
        except OSError:
            pytest.skip("symbolic links are unavailable on this platform")

        # The snapshot fingerprint commits the persisted string. Export must not reinterpret that
        # string by resolving the now-changed filesystem path behind it.
        rendered = render_terminal_session_snapshot(snapshot=snapshot)
        raw = json.loads(rendered.payload.decode("utf-8"))
        assert raw["request"]["workspace_root"] == str(original_workspace)
        assert Path(raw["request"]["workspace_root"]).resolve() == replacement.resolve()
        assert rendered.fingerprint == snapshot.fingerprint
    finally:
        service.close()


def test_snapshot_export_enforces_two_mib_hard_ceiling(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    try:
        snapshot = _terminal_session(service, tmp_path)
        with pytest.raises(SnapshotExportTooLargeError, match="byte limit"):
            render_terminal_session_snapshot(
                snapshot=snapshot,
                maximum_bytes=1,
            )
        with pytest.raises(ValueError, match="between 1"):
            render_terminal_session_snapshot(
                snapshot=snapshot,
                maximum_bytes=MAX_SESSION_SNAPSHOT_EXPORT_BYTES + 1,
            )
    finally:
        service.close()


def test_http_snapshot_export_requires_auth_and_returns_exact_deterministic_body(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot = _terminal_session(service, tmp_path)
        path = f"/v1/sessions/{snapshot.session_id}/snapshot/export"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path, authorized=False), timeout=3.0)
        assert exc_info.value.code == 401

        bodies: list[bytes] = []
        for _ in range(2):
            with urlopen(_get(server, path, authorized=True), timeout=3.0) as response:
                body = response.read()
                bodies.append(body)
                assert response.status == 200
                assert response.headers.get("Content-Type") == "application/json; charset=utf-8"
                assert response.headers.get("Content-Disposition") == (
                    'attachment; filename="session-snapshot.json"'
                )
                assert response.headers.get("Content-Length") == str(len(body))
                assert response.headers.get("Cache-Control") == "no-store"
                assert response.headers.get("X-Content-Type-Options") == "nosniff"
                assert response.headers.get("Referrer-Policy") == "no-referrer"
                assert response.headers.get("X-Harness-X-Snapshot-SHA256") == (
                    hashlib.sha256(body).hexdigest()
                )
                assert response.headers.get("X-Harness-X-Snapshot-Fingerprint") == snapshot.fingerprint
                assert response.headers.get("X-Harness-X-Snapshot-Revision") == str(snapshot.revision)
        assert bodies[0] == bodies[1]
        raw = json.loads(bodies[0].decode("utf-8"))
        assert raw["session_id"] == snapshot.session_id
        assert raw["fingerprint"] == snapshot.fingerprint
        assert raw["request"]["task"] == snapshot.request.task
    finally:
        server.close()
        service.close()


def test_http_snapshot_export_rejects_running_query_and_extra_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        running = service.store.create_session(_request(workspace), output_root=output)
        running = service.store.transition(
            running.session_id,
            status=AppSessionStatus.RUNNING,
            kind=AppEventKind.SESSION_STARTED,
        )
        path = f"/v1/sessions/{running.session_id}/snapshot/export"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 409
        assert json.loads(exc_info.value.read())["error"] == "snapshot_export_not_terminal"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path + "?path=/etc/passwd", authorized=True), timeout=3.0)
        assert exc_info.value.code == 400
        payload = json.loads(exc_info.value.read())
        assert payload["error"] == "invalid_request"
        assert payload["detail"] == "snapshot export endpoint does not accept query parameters"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path + "/snapshot.json", authorized=True), timeout=3.0)
        assert exc_info.value.code == 404
    finally:
        server.close()
        service.close()
