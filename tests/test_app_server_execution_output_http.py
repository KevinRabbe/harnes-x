from __future__ import annotations

import http.client
import json
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

import harness_x.app_server.execution_outputs as output_module
from harness_x.app_server.resource_operator_http_server import LocalOperatorHTTPServer
from harness_x.app_server.service import AppServerService
from harness_x.repository import RepositoryIdentity
from harness_x.tools.repository import GitDiffOutput, GitStatusEntry, GitStatusOutput


class _Report(BaseModel):
    succeeded: bool = True


def _runner(snapshot) -> _Report:
    output = Path(snapshot.output_root)
    output.mkdir(parents=True, exist_ok=True)
    report = _Report()
    (output / "coding-task-report.json").write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "not-registered.txt").write_text("not an artifact", encoding="utf-8")
    return report


class _Harness:
    def __init__(self, root: Path) -> None:
        self.service = AppServerService(root / "data", runner=_runner)
        self.server = LocalOperatorHTTPServer(self.service, root / "transport", port=0)
        self.server.start_in_thread()

    def close(self) -> None:
        self.server.close()
        self.service.close()


def _raw(
    harness: _Harness,
    method: str,
    path: str,
    *,
    body: object | None = None,
    authorized: bool = True,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", harness.server.port, timeout=10)
    headers: dict[str, str] = {}
    if authorized:
        headers["Authorization"] = f"Bearer {harness.server.token}"
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    response_headers = {key.casefold(): value for key, value in response.getheaders()}
    connection.close()
    return status, response_headers, raw


def _json(
    harness: _Harness,
    method: str,
    path: str,
    *,
    body: object | None = None,
    authorized: bool = True,
) -> tuple[int, dict]:
    status, _headers, raw = _raw(harness, method, path, body=body, authorized=authorized)
    return status, json.loads(raw.decode("utf-8")) if raw else {}


def _create_project_chat(harness: _Harness, workspace: Path) -> tuple[dict, dict]:
    status, project = _json(
        harness,
        "POST",
        "/v1/projects",
        body={
            "schema_version": "create-project-request-v1",
            "name": "Output surfaces",
            "workspace_root": str(workspace),
            "default_model_profile": None,
        },
    )
    assert status == 201, project
    status, chat = _json(
        harness,
        "POST",
        f"/v1/projects/{project['project_id']}/chats",
        body={"schema_version": "create-chat-request-v1", "title": "Outputs"},
    )
    assert status == 201, chat
    return project, chat


def _execute(harness: _Harness, project: dict, chat: dict) -> dict:
    status, projection = _json(
        harness,
        "POST",
        f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/executions",
        body={
            "schema_version": "conversation-execution-submit-v1",
            "submission_id": "submission_" + "d" * 32,
            "role": "user",
            "content": {"type": "text", "text": "Produce one report"},
        },
    )
    assert status == 202, projection
    path = (
        f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}"
        f"/executions/{projection['execution_id']}"
    )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        status, current = _json(harness, "GET", path)
        assert status == 200, current
        if current["terminal"]:
            return current
        time.sleep(0.02)
    raise AssertionError("conversation execution did not become terminal")


def test_execution_diff_endpoint_is_authenticated_read_only_bounded_and_not_authorship_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = _Harness(tmp_path / "server")
    try:
        project, chat = _create_project_chat(harness, workspace)
        execution = _execute(harness, project, chat)
        monkeypatch.setattr(
            output_module,
            "_status_output",
            lambda _root: GitStatusOutput(
                identity=RepositoryIdentity(
                    root=str(workspace),
                    is_git_repository=True,
                    head_sha="a" * 40,
                    branch="main",
                    dirty=True,
                ),
                entries=(
                    GitStatusEntry(path="src/example.py", index_status=" ", worktree_status="M"),
                ),
            ),
        )
        monkeypatch.setattr(
            output_module,
            "_diff_output",
            lambda _root, path, *, staged: GitDiffOutput(
                path=path,
                staged=staged,
                diff="--- a/src/example.py\n+++ b/src/example.py\n@@ -1 +1 @@\n-old\n+new\n",
                truncated=False,
            ),
        )
        path = (
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}"
            f"/executions/{execution['execution_id']}/diff"
        )
        status, unauthorized = _json(harness, "GET", path, authorized=False)
        assert status == 401 and unauthorized["error"] == "unauthorized"
        status, queried = _json(harness, "GET", path + "?full=true")
        assert status == 400 and queried["error"] == "invalid_project_resource_request"

        status, diff = _json(harness, "GET", path)
        assert status == 200, diff
        assert diff["schema_version"] == "conversation-execution-diff-projection-v1"
        assert diff["read_only"] is True
        assert diff["execution_authorship_proven"] is False
        assert diff["verification_authority"] is False
        assert diff["evidence_authority"] is False
        assert diff["files"][0]["path"] == "src/example.py"
        assert "old" in diff["files"][0]["patch"]
        serialized = json.dumps(diff, sort_keys=True)
        assert str(workspace) not in serialized
        assert "workspace_root" not in serialized

        wrong = path.replace(chat["chat_id"], "chat_" + "f" * 32)
        status, conflict = _json(harness, "GET", wrong)
        assert status == 409 and conflict["error"] == "project_resource_conflict"
    finally:
        harness.close()


def test_registered_artifact_list_and_download_are_owned_digest_checked_and_no_store(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = _Harness(tmp_path / "server")
    try:
        project, chat = _create_project_chat(harness, workspace)
        execution = _execute(harness, project, chat)
        prefix = (
            f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}"
            f"/executions/{execution['execution_id']}/artifacts"
        )
        status, listing = _json(harness, "GET", prefix)
        assert status == 200, listing
        assert listing["schema_version"] == "conversation-execution-artifact-list-v1"
        assert len(listing["artifacts"]) == 1
        artifact = listing["artifacts"][0]
        assert artifact["logical_name"] == "Coding task report"
        assert artifact["storage_name"] == "coding-task-report.json"
        assert artifact["verification_authority"] is False
        assert artifact["evidence_authority"] is False
        serialized = json.dumps(listing, sort_keys=True)
        assert "not-registered.txt" not in serialized
        assert str(harness.service.run_root) not in serialized

        download_path = f"{prefix}/{artifact['artifact_id']}"
        status, unauthorized_headers, unauthorized_raw = _raw(
            harness,
            "GET",
            download_path,
            authorized=False,
        )
        assert status == 401
        assert json.loads(unauthorized_raw.decode("utf-8"))["error"] == "unauthorized"
        assert "x-harness-x-artifact-sha256" not in unauthorized_headers

        status, headers, data = _raw(harness, "GET", download_path)
        assert status == 200
        assert headers["cache-control"] == "no-store"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-harness-x-artifact-sha256"] == artifact["sha256"]
        assert headers["content-disposition"] == 'attachment; filename="coding-task-report.json"'
        assert len(data) == artifact["size_bytes"]

        unknown_id = "artifact_" + "e" * 32
        status, missing = _json(harness, "GET", f"{prefix}/{unknown_id}")
        assert status == 404 and missing["error"] == "unknown_project_resource"

        output_root = Path(harness.service.session(execution["session_id"]).output_root)
        (output_root / "coding-task-report.json").write_text('{"succeeded":false}\n', encoding="utf-8")
        status, corrupt = _json(harness, "GET", download_path)
        assert status == 409 and corrupt["error"] == "project_resource_corruption"
    finally:
        harness.close()
