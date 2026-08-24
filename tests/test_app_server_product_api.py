from __future__ import annotations

import http.client
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from harness_x.app_server.product_operator_http_server import LocalOperatorHTTPServer
from harness_x.app_server.service import AppServerService


class _ServerHarness:
    def __init__(self, root: Path) -> None:
        self.service = AppServerService(root / "data")
        self.server = LocalOperatorHTTPServer(self.service, root, port=0)
        self.server.start_in_thread()

    def close(self) -> None:
        self.server.close()
        self.service.close()


def _request(
    harness: _ServerHarness,
    method: str,
    path: str,
    *,
    body: object | str | None = None,
    token: str | None = "server",
    content_type: str = "application/json",
) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", harness.server.port, timeout=10)
    headers: dict[str, str] = {}
    if token == "server":
        headers["Authorization"] = f"Bearer {harness.server.token}"
    elif token is not None:
        headers["Authorization"] = f"Bearer {token}"
    payload: bytes | None = None
    if body is not None:
        payload = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        headers["Content-Type"] = content_type
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw.decode("utf-8")) if raw else {}


def _create_project(harness: _ServerHarness, workspace: Path, *, name: str = "Project") -> dict:
    status, payload = _request(
        harness,
        "POST",
        "/v1/projects",
        body={
            "schema_version": "create-project-request-v1",
            "name": name,
            "workspace_root": str(workspace),
        },
    )
    assert status == 201, payload
    return payload


def _create_chat(harness: _ServerHarness, project_id: str, *, title: str = "Chat") -> dict:
    status, payload = _request(
        harness,
        "POST",
        f"/v1/projects/{project_id}/chats",
        body={"schema_version": "create-chat-request-v1", "title": title},
    )
    assert status == 201, payload
    return payload


def test_product_api_requires_existing_bearer_auth_for_reads_and_writes(tmp_path: Path) -> None:
    harness = _ServerHarness(tmp_path / "server")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        status, payload = _request(harness, "GET", "/v1/projects", token=None)
        assert status == 401
        assert payload["error"] == "unauthorized"
        status, payload = _request(harness, "GET", "/v1/projects", token="wrong")
        assert status == 401
        assert payload["error"] == "unauthorized"
        status, payload = _request(
            harness,
            "POST",
            "/v1/projects",
            token=None,
            body={
                "schema_version": "create-project-request-v1",
                "name": "No auth",
                "workspace_root": str(workspace),
            },
        )
        assert status == 401
        assert payload["error"] == "unauthorized"
        assert harness.server.product_store.projects() == ()
    finally:
        harness.close()


def test_project_lifecycle_and_restoration_are_exposed_without_internal_paths(tmp_path: Path) -> None:
    harness = _ServerHarness(tmp_path / "server")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project = _create_project(harness, workspace)
        project_id = project["project_id"]
        assert project["workspace_root"] == str(workspace.resolve())

        status, listing = _request(harness, "GET", "/v1/projects")
        assert status == 200
        assert listing["schema_version"] == "project-list-v1"
        assert [item["project_id"] for item in listing["projects"]] == [project_id]

        status, renamed = _request(
            harness,
            "POST",
            f"/v1/projects/{project_id}/rename",
            body={"schema_version": "rename-project-request-v1", "name": "Renamed"},
        )
        assert status == 200
        assert renamed["name"] == "Renamed"
        assert renamed["project_id"] == project_id

        status, restoration = _request(harness, "GET", "/v1/product/restoration")
        assert status == 200
        assert restoration["last_opened_project_id"] == project_id

        status, archived = _request(
            harness, "POST", f"/v1/projects/{project_id}/archive", body={}
        )
        assert status == 200 and archived["archived"] is True
        status, listing = _request(harness, "GET", "/v1/projects")
        assert status == 200 and listing["projects"] == []
        status, listing = _request(harness, "GET", "/v1/projects?include_archived=true")
        assert status == 200 and listing["projects"][0]["project_id"] == project_id

        status, restored = _request(
            harness, "POST", f"/v1/projects/{project_id}/restore", body={}
        )
        assert status == 200 and restored["archived"] is False
        serialized = json.dumps({"project": restored, "restoration": restoration})
        assert "project-chat-state.json" not in serialized
        assert "messages.jsonl" not in serialized
        assert "access-token" not in serialized
    finally:
        harness.close()


def test_chat_lifecycle_user_message_append_and_read_are_project_scoped(tmp_path: Path) -> None:
    harness = _ServerHarness(tmp_path / "server")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project = _create_project(harness, workspace)
        project_id = project["project_id"]
        chat = _create_chat(harness, project_id, title="First")
        chat_id = chat["chat_id"]

        status, message = _request(
            harness,
            "POST",
            f"/v1/projects/{project_id}/chats/{chat_id}/messages",
            body={
                "schema_version": "append-user-message-request-v1",
                "role": "user",
                "content": {"type": "text", "text": "hello"},
            },
        )
        assert status == 201
        assert message["sequence"] == 1
        assert message["role"] == "user"
        assert message["content"] == {"type": "text", "text": "hello"}

        status, page = _request(
            harness, "GET", f"/v1/projects/{project_id}/chats/{chat_id}/messages"
        )
        assert status == 200
        assert page["schema_version"] == "chat-message-list-v1"
        assert [item["message_id"] for item in page["messages"]] == [message["message_id"]]

        status, renamed = _request(
            harness,
            "POST",
            f"/v1/projects/{project_id}/chats/{chat_id}/rename",
            body={"schema_version": "rename-chat-request-v1", "title": "Renamed chat"},
        )
        assert status == 200 and renamed["title"] == "Renamed chat"
        status, archived = _request(
            harness, "POST", f"/v1/projects/{project_id}/chats/{chat_id}/archive", body={}
        )
        assert status == 200 and archived["archived"] is True
        status, _ = _request(
            harness,
            "POST",
            f"/v1/projects/{project_id}/chats/{chat_id}/messages",
            body={
                "schema_version": "append-user-message-request-v1",
                "role": "user",
                "content": {"type": "text", "text": "blocked"},
            },
        )
        assert status == 409
        status, restored = _request(
            harness, "POST", f"/v1/projects/{project_id}/chats/{chat_id}/restore", body={}
        )
        assert status == 200 and restored["archived"] is False
    finally:
        harness.close()


def test_api_rejects_forged_assistant_extra_fields_bad_ids_wrong_project_and_duplicate_workspace(
    tmp_path: Path,
) -> None:
    harness = _ServerHarness(tmp_path / "server")
    wa = tmp_path / "a"
    wb = tmp_path / "b"
    wa.mkdir()
    wb.mkdir()
    try:
        pa = _create_project(harness, wa, name="A")
        pb = _create_project(harness, wb, name="B")
        ca = _create_chat(harness, pa["project_id"], title="A chat")

        status, payload = _request(
            harness,
            "POST",
            f"/v1/projects/{pa['project_id']}/chats/{ca['chat_id']}/messages",
            body={
                "schema_version": "append-user-message-request-v1",
                "role": "assistant",
                "content": {"type": "text", "text": "forged"},
            },
        )
        assert status == 400
        assert payload["error"] == "invalid_product_request"

        status, _ = _request(
            harness,
            "POST",
            "/v1/projects",
            body={
                "schema_version": "create-project-request-v1",
                "name": "Extra",
                "workspace_root": str(tmp_path),
                "unexpected": True,
            },
        )
        assert status == 400

        status, _ = _request(harness, "GET", "/v1/projects/not-a-project")
        assert status == 400

        status, wrong_owner = _request(
            harness,
            "GET",
            f"/v1/projects/{pb['project_id']}/chats/{ca['chat_id']}",
        )
        assert status == 400
        assert "another project" in (wrong_owner.get("detail") or "")

        status, conflict = _request(
            harness,
            "POST",
            "/v1/projects",
            body={
                "schema_version": "create-project-request-v1",
                "name": "Duplicate",
                "workspace_root": str(wa),
            },
        )
        assert status == 409
        assert conflict["error"] == "product_conflict"
    finally:
        harness.close()


def test_api_rejects_malformed_json_bad_queries_and_nonempty_action_bodies(tmp_path: Path) -> None:
    harness = _ServerHarness(tmp_path / "server")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project = _create_project(harness, workspace)
        status, _ = _request(
            harness,
            "POST",
            "/v1/projects",
            body="{bad-json",
        )
        assert status == 400
        status, _ = _request(harness, "GET", "/v1/projects?unexpected=true")
        assert status == 400
        status, _ = _request(harness, "GET", "/v1/projects?include_archived=maybe")
        assert status == 400
        status, _ = _request(
            harness,
            "POST",
            f"/v1/projects/{project['project_id']}/open",
            body={"unexpected": True},
        )
        assert status == 400
    finally:
        harness.close()


def test_concurrent_http_message_appends_are_serialized_into_contiguous_unique_sequences(
    tmp_path: Path,
) -> None:
    harness = _ServerHarness(tmp_path / "server")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project = _create_project(harness, workspace)
        chat = _create_chat(harness, project["project_id"])
        path = f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/messages"

        def append(index: int) -> tuple[int, dict]:
            return _request(
                harness,
                "POST",
                path,
                body={
                    "schema_version": "append-user-message-request-v1",
                    "role": "user",
                    "content": {"type": "text", "text": f"message {index}"},
                },
            )

        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(append, range(24)))
        assert all(status == 201 for status, _ in results)
        sequences = sorted(payload["sequence"] for _, payload in results)
        ids = {payload["message_id"] for _, payload in results}
        assert sequences == list(range(1, 25))
        assert len(ids) == 24

        status, page = _request(harness, "GET", path)
        assert status == 200
        assert [item["sequence"] for item in page["messages"]] == list(range(1, 25))
    finally:
        harness.close()


def test_api_created_product_state_survives_app_server_restart(tmp_path: Path) -> None:
    root = tmp_path / "server"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = _ServerHarness(root)
    try:
        project = _create_project(first, workspace)
        chat = _create_chat(first, project["project_id"])
        status, message = _request(
            first,
            "POST",
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/messages",
            body={
                "schema_version": "append-user-message-request-v1",
                "role": "user",
                "content": {"type": "text", "text": "persist me"},
            },
        )
        assert status == 201
    finally:
        first.close()

    second = _ServerHarness(root)
    try:
        status, project_after = _request(second, "GET", f"/v1/projects/{project['project_id']}")
        assert status == 200 and project_after["project_id"] == project["project_id"]
        status, page = _request(
            second,
            "GET",
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/messages",
        )
        assert status == 200
        assert page["messages"][0]["message_id"] == message["message_id"]
        status, restoration = _request(second, "GET", "/v1/product/restoration")
        assert status == 200
        assert restoration["last_opened_project_id"] == project["project_id"]
        assert restoration["last_opened_chat_id"] == chat["chat_id"]
    finally:
        second.close()
