from __future__ import annotations

import base64
import http.client
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from harness_x.app_server.resource_operator_http_server import (
    LocalOperatorHTTPServer,
    ProjectAttachmentUploadRequest,
)
from harness_x.app_server.sensitive_approval import SensitiveActionApprovalBroker
from harness_x.app_server.service import AppServerService
from harness_x.product import ProjectResourceStore


class _Report(BaseModel):
    succeeded: bool = True


class _ApprovalRunner:
    def __init__(self, broker: SensitiveActionApprovalBroker) -> None:
        self.approval_broker = broker
        self.requests = []

    def __call__(self, snapshot) -> _Report:
        self.requests.append(snapshot.request)
        output = Path(snapshot.output_root)
        output.mkdir(parents=True, exist_ok=True)
        report = _Report()
        (output / "coding-task-report.json").write_text(
            report.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        return report


class _ServerHarness:
    def __init__(self, root: Path, *, approval_aware: bool = False) -> None:
        data = root / "data"
        if approval_aware:
            broker = SensitiveActionApprovalBroker(data / "sensitive-approvals")
            self.runner = _ApprovalRunner(broker)
        else:
            self.runner = None
        self.service = AppServerService(data, runner=self.runner)
        self.server = LocalOperatorHTTPServer(self.service, root / "transport", port=0)
        self.server.start_in_thread()

    def close(self) -> None:
        self.server.close()
        self.service.close()


def _http(
    harness: _ServerHarness,
    method: str,
    path: str,
    *,
    body: object | None = None,
    token: str | None = "server",
) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", harness.server.port, timeout=10)
    headers: dict[str, str] = {}
    if token == "server":
        headers["Authorization"] = f"Bearer {harness.server.token}"
    elif token is not None:
        headers["Authorization"] = f"Bearer {token}"
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw.decode("utf-8")) if raw else {}


def _create_project(harness: _ServerHarness, workspace: Path) -> dict:
    status, project = _http(
        harness,
        "POST",
        "/v1/projects",
        body={
            "schema_version": "create-project-request-v1",
            "name": "Resources project",
            "workspace_root": str(workspace),
            "default_model_profile": None,
        },
    )
    assert status == 201, project
    return project


def _create_chat(harness: _ServerHarness, project_id: str) -> dict:
    status, chat = _http(
        harness,
        "POST",
        f"/v1/projects/{project_id}/chats",
        body={"schema_version": "create-chat-request-v1", "title": "Resources"},
    )
    assert status == 201, chat
    return chat


def _upload_body(filename: str, data: bytes, *, media_type: str = "text/plain", **extra) -> dict:
    body = {
        "schema_version": "project-attachment-upload-request-v1",
        "filename": filename,
        "media_type": media_type,
        "data_base64": base64.b64encode(data).decode("ascii"),
    }
    body.update(extra)
    return body


def test_attachment_upload_is_authenticated_bounded_project_scoped_and_secret_free(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = _ServerHarness(tmp_path / "server")
    try:
        project = _create_project(harness, workspace)
        project_id = project["project_id"]
        path = f"/v1/projects/{project_id}/attachments"

        status, unauthorized = _http(
            harness,
            "POST",
            path,
            token=None,
            body=_upload_body("notes.txt", b"private attachment\n"),
        )
        assert status == 401 and unauthorized["error"] == "unauthorized"

        status, record = _http(
            harness,
            "POST",
            path,
            body=_upload_body("notes.txt", b"private attachment\n"),
        )
        assert status == 201, record
        assert record["schema_version"] == "project-attachment-v1"
        assert record["project_id"] == project_id
        assert record["filename"] == "notes.txt"
        assert record["media_type"] == "text/plain"
        assert record["text_encoding"] == "utf-8"
        assert "data_base64" not in record
        assert "private attachment" not in json.dumps(record, sort_keys=True)

        stored = ProjectResourceStore(harness.server.product_store)
        assert stored.attachment_bytes(project_id, record["attachment_id"]) == b"private attachment\n"

        status, metadata = _http(
            harness,
            "GET",
            f"{path}/{record['attachment_id']}",
        )
        assert status == 200 and metadata == record

        status, queried = _http(
            harness,
            "GET",
            f"{path}/{record['attachment_id']}?raw=true",
        )
        assert status == 400 and queried["error"] == "invalid_project_resource_request"
    finally:
        harness.close()


def test_attachment_api_rejects_path_authority_invalid_base64_and_oversize_before_storage(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = _ServerHarness(tmp_path / "server")
    try:
        project_id = _create_project(harness, workspace)["project_id"]
        path = f"/v1/projects/{project_id}/attachments"
        cases = [
            _upload_body("../escape.txt", b"x"),
            _upload_body("safe.txt", b"x", absolute_path="C:/Users/operator/secret.txt"),
            {
                "schema_version": "project-attachment-upload-request-v1",
                "filename": "bad.txt",
                "media_type": "text/plain",
                "data_base64": "not base64!",
            },
        ]
        for body in cases:
            status, payload = _http(harness, "POST", path, body=body)
            assert status == 400, payload
            assert payload["error"] == "invalid_project_resource_request"

        too_large = ProjectAttachmentUploadRequest(
            schema_version="project-attachment-upload-request-v1",
            filename="large.bin",
            media_type="application/octet-stream",
            data_base64=base64.b64encode(b"x" * (1024 * 1024 + 1)).decode("ascii"),
        )
        with pytest.raises(ValueError, match="HTTP upload byte limit"):
            too_large.decoded_bytes()

        resource_root = harness.server.product_store.projects_root / project_id / "resources"
        assert not resource_root.exists()
    finally:
        harness.close()


def test_attachment_api_rejects_archived_and_wrong_project_reads(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    other_workspace = tmp_path / "other"
    workspace.mkdir()
    other_workspace.mkdir()
    harness = _ServerHarness(tmp_path / "server")
    try:
        project = _create_project(harness, workspace)
        other = _create_project(harness, other_workspace)
        path = f"/v1/projects/{project['project_id']}/attachments"
        status, record = _http(
            harness,
            "POST",
            path,
            body=_upload_body("owned.txt", b"owned"),
        )
        assert status == 201, record

        status, wrong = _http(
            harness,
            "GET",
            f"/v1/projects/{other['project_id']}/attachments/{record['attachment_id']}",
        )
        assert status == 404 and wrong["error"] == "unknown_project_resource"

        status, archived = _http(
            harness,
            "POST",
            f"/v1/projects/{project['project_id']}/archive",
            body={},
        )
        assert status == 200 and archived["archived"] is True
        status, conflict = _http(
            harness,
            "POST",
            path,
            body=_upload_body("later.txt", b"blocked"),
        )
        assert status == 409 and conflict["error"] == "project_resource_conflict"

        status, existing = _http(
            harness,
            "GET",
            f"{path}/{record['attachment_id']}",
        )
        assert status == 200 and existing["attachment_id"] == record["attachment_id"]
    finally:
        harness.close()


def test_http_v2_submission_freezes_attachment_and_workspace_reference_and_projects_snapshot(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "input.txt").write_bytes(b"workspace input\n")
    harness = _ServerHarness(tmp_path / "server", approval_aware=True)
    try:
        project = _create_project(harness, workspace)
        chat = _create_chat(harness, project["project_id"])
        status, attachment = _http(
            harness,
            "POST",
            f"/v1/projects/{project['project_id']}/attachments",
            body=_upload_body("note.txt", b"attachment input\n"),
        )
        assert status == 201, attachment

        submission_id = "submission_" + "c" * 32
        status, projection = _http(
            harness,
            "POST",
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/executions",
            body={
                "schema_version": "conversation-execution-submit-v2",
                "submission_id": submission_id,
                "role": "user",
                "content": {"type": "text", "text": "Use the resources"},
                "resources": [
                    {"kind": "attachment", "attachment_id": attachment["attachment_id"]},
                    {"kind": "workspace_file", "source_path": "input.txt"},
                ],
            },
        )
        assert status == 202, projection
        execution_id = projection["execution_id"]

        status, frozen = _http(
            harness,
            "GET",
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/executions/{execution_id}/resources",
        )
        assert status == 200, frozen
        assert frozen["schema_version"] == "conversation-execution-resource-snapshot-v1"
        assert frozen["submission_id"] == submission_id
        assert [item["kind"] for item in frozen["items"]] == ["attachment", "workspace_file"]
        assert "attachment input" in frozen["rendered_context"]
        assert "workspace input" in frozen["rendered_context"]
        assert "untrusted-context-only" in frozen["rendered_context"]
        serialized = json.dumps(frozen, sort_keys=True)
        for forbidden in ("C:\\", "/home/", "Bearer ", "Authorization", "api_key"):
            assert forbidden not in serialized

        status, wrong_chat = _http(
            harness,
            "GET",
            f"/v1/projects/{project['project_id']}/chats/chat_{'f' * 32}/executions/{execution_id}/resources",
        )
        assert status in {404, 409}
    finally:
        harness.close()
