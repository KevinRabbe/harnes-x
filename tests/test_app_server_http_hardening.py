from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import BaseModel

from harness_x.app_server.http_server import LocalAppHTTPServer
from harness_x.app_server.protocol import (
    AppEventKind,
    AppSessionStatus,
    CodingSessionRequest,
)
from harness_x.app_server.service import AppServerService


class _Report(BaseModel):
    succeeded: bool = True
    failure_reason: str | None = None


def _runner(snapshot):
    output = Path(snapshot.output_root)
    output.mkdir(parents=True, exist_ok=True)
    report = _Report()
    (output / "coding-task-report.json").write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return report


def _request(tmp_path: Path, *, task: str) -> CodingSessionRequest:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return CodingSessionRequest(
        workspace_root=workspace,
        task=task,
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def _get_json(server: LocalAppHTTPServer, path: str):
    request = Request(
        server.base_url + path,
        headers={"Authorization": f"Bearer {server.token}"},
        method="GET",
    )
    with urlopen(request, timeout=3.0) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_session_and_event_pages_are_bounded_and_cursor_explicit(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalAppHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        sessions = [
            service.store.create_session(
                _request(tmp_path, task=f"task {index}"),
                output_root=tmp_path / f"run-{index}",
            )
            for index in range(3)
        ]
        with urlopen(
            Request(
                server.base_url + "/v1/sessions?limit=2",
                headers={"Authorization": f"Bearer {server.token}"},
            ),
            timeout=3.0,
        ) as response:
            page = json.loads(response.read().decode("utf-8"))
            assert response.headers["Connection"] == "close"
        assert page["total"] == 3
        assert page["limit"] == 2
        assert page["truncated"] is True
        assert [row["session_id"] for row in page["sessions"]] == [
            sessions[1].session_id,
            sessions[2].session_id,
        ]

        target = sessions[0]
        for index in range(4):
            service.store.add_artifact(
                target.session_id,
                artifact_kind=f"artifact-{index}",
                path=tmp_path / f"artifact-{index}.json",
            )
        _, events = _get_json(
            server,
            f"/v1/sessions/{target.session_id}/events?after=0&limit=2",
        )
        assert events["limit"] == 2
        assert events["has_more"] is True
        assert len(events["events"]) == 2
        assert events["next_after"] == 2

        _, next_page = _get_json(
            server,
            f"/v1/sessions/{target.session_id}/events?after=2&limit=1000",
        )
        assert next_page["events"][0]["sequence"] == 3
        assert next_page["has_more"] is False
    finally:
        server.close()
        service.close()


def test_http_rejects_invalid_limits(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalAppHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        for path in ("/v1/sessions?limit=0", "/v1/sessions?limit=501"):
            request = Request(
                server.base_url + path,
                headers={"Authorization": f"Bearer {server.token}"},
            )
            with pytest.raises(HTTPError) as exc_info:
                urlopen(request, timeout=3.0)
            assert exc_info.value.code == 400
    finally:
        server.close()
        service.close()


def test_http_new_session_with_missing_workspace_fails_before_queueing(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalAppHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        request = Request(
            server.base_url + "/v1/sessions",
            data=json.dumps(
                {
                    "workspace_root": str(tmp_path / "missing-workspace"),
                    "task": "cannot launch",
                    "model_profile": "main",
                    "verification_commands": ["python -m pytest"],
                }
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {server.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with pytest.raises(HTTPError) as exc_info:
            urlopen(request, timeout=3.0)
        assert exc_info.value.code == 400
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "invalid_session_request"
        assert service.sessions() == ()
    finally:
        server.close()
        service.close()


def test_cancel_response_closes_connection_even_when_body_is_not_consumed(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service", runner=_runner)
    server = LocalAppHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        snapshot = service.store.create_session(
            _request(tmp_path, task="queued"),
            output_root=tmp_path / "run",
        )
        service.store.transition(
            snapshot.session_id,
            status=AppSessionStatus.FAILED,
            kind=AppEventKind.SESSION_FAILED,
            failure_reason="fixture terminal state",
        )
        request = Request(
            server.base_url + f"/v1/sessions/{snapshot.session_id}/cancel",
            data=b'{"ignored":"body"}',
            headers={
                "Authorization": f"Bearer {server.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with pytest.raises(HTTPError) as exc_info:
            urlopen(request, timeout=3.0)
        assert exc_info.value.code == 409
        assert exc_info.value.headers["Connection"] == "close"
    finally:
        server.close()
        service.close()
