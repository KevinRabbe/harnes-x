from __future__ import annotations

import http.client
import json
from pathlib import Path

from harness_x.app_server.improvement_observatory_operator_http_server import LocalOperatorHTTPServer
from harness_x.app_server.service import AppServerService
from harness_x.core.ids import SystemVersion
from harness_x.improvement.promotion import ActiveConfigPointer


class _Harness:
    def __init__(self, root: Path) -> None:
        self.service = AppServerService(root / "data")
        self.server = LocalOperatorHTTPServer(self.service, root / "transport", port=0)
        self.server.start_in_thread()

    def close(self) -> None:
        self.server.close()
        self.service.close()


def _json(
    harness: _Harness,
    method: str,
    path: str,
    *,
    body: object | None = None,
    authorized: bool = True,
) -> tuple[int, dict]:
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
    connection.close()
    return status, json.loads(raw.decode("utf-8")) if raw else {}


def _project(harness: _Harness, workspace: Path) -> dict:
    status, project = _json(
        harness,
        "POST",
        "/v1/projects",
        body={
            "schema_version": "create-project-request-v1",
            "name": "Observatory fixture",
            "workspace_root": str(workspace),
            "default_model_profile": None,
        },
    )
    assert status == 201, project
    return project


def _path(project: dict) -> str:
    return f"/v1/projects/{project['project_id']}/improvement-observatory"


def test_get_is_authenticated_queryless_and_does_not_create_observatory_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = _Harness(tmp_path / "server")
    try:
        project = _project(harness, workspace)
        assert not (workspace / ".harness-x").exists()

        status, payload = _json(harness, "GET", _path(project), authorized=False)
        assert status == 401
        status, payload = _json(harness, "GET", _path(project) + "?root=elsewhere")
        assert status == 400
        assert payload["error"] == "invalid_improvement_observatory_request"

        before = tuple(workspace.rglob("*"))
        status, payload = _json(harness, "GET", _path(project))
        after = tuple(workspace.rglob("*"))
        assert status == 200, payload
        assert payload["schema_version"] == "improvement-observatory-v1"
        assert payload["project_id"] == project["project_id"]
        assert payload["read_only"] is True
        assert payload["promotion_authority"] is False
        assert payload["observatory_root_present"] is False
        assert before == after == ()
        assert not (workspace / ".harness-x").exists()
    finally:
        harness.close()


def test_projection_reads_only_allowlisted_pointer_and_exposes_relative_provenance(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".harness-x" / "observed" / "active-system"
    root.mkdir(parents=True)
    pointer = ActiveConfigPointer(
        system_version=SystemVersion(value="0.1.0-alpha.0+observed"),
        config_sha256="a" * 64,
        artifact_path="versions/secret-config.json",
    )
    (root / "active-config.json").write_text(pointer.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (root / "secret-config.json").write_text('{"OPENAI_API_KEY":"do-not-project"}\n', encoding="utf-8")

    harness = _Harness(tmp_path / "server")
    try:
        project = _project(harness, workspace)
        status, payload = _json(harness, "GET", _path(project))
        assert status == 200, payload
        assert payload["versions"] == [
            {
                "system_version": "0.1.0-alpha.0+observed",
                "source": ".harness-x/observed/active-system/active-config.json",
                "source_kind": "active_config_pointer",
                "config_sha256": "a" * 64,
            }
        ]
        encoded = json.dumps(payload, sort_keys=True)
        assert str(workspace) not in encoded
        assert "OPENAI_API_KEY" not in encoded
        assert "do-not-project" not in encoded
        assert "secret-config.json" not in encoded
    finally:
        harness.close()


def test_malformed_record_is_fail_visible_without_reflecting_rejected_values(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".harness-x"
    root.mkdir(parents=True)
    secret = "credential-value-must-not-cross-http"
    (root / "promotion-record.json").write_text(
        json.dumps({"promotion_id": "promotion_bad", "unexpected_secret": secret}),
        encoding="utf-8",
    )

    harness = _Harness(tmp_path / "server")
    try:
        project = _project(harness, workspace)
        status, payload = _json(harness, "GET", _path(project))
        assert status == 200, payload
        source = next(
            item
            for item in payload["sources"]
            if item["relative_path"].endswith("promotion-record.json")
        )
        assert source["status"] == "malformed"
        assert source["detail"] == "record failed strict schema or bounded-file validation"
        assert secret not in json.dumps(payload, sort_keys=True)
    finally:
        harness.close()


def test_symlinked_allowlisted_record_is_rejected_without_following_target(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".harness-x"
    root.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"unexpected_secret":"outside-secret"}\n', encoding="utf-8")
    link = root / "promotion-record.json"
    try:
        link.symlink_to(outside)
    except OSError:
        return

    harness = _Harness(tmp_path / "server")
    try:
        project = _project(harness, workspace)
        status, payload = _json(harness, "GET", _path(project))
        assert status == 200, payload
        source = next(
            item
            for item in payload["sources"]
            if item["relative_path"].endswith("promotion-record.json")
        )
        assert source["status"] == "symlink_rejected"
        assert "outside-secret" not in json.dumps(payload, sort_keys=True)
    finally:
        harness.close()


def test_symlinked_observatory_root_is_explicitly_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-root"
    outside.mkdir()
    (outside / "promotion-record.json").write_text('{"secret":"outside-root-secret"}\n', encoding="utf-8")
    try:
        (workspace / ".harness-x").symlink_to(outside, target_is_directory=True)
    except OSError:
        return

    harness = _Harness(tmp_path / "server")
    try:
        project = _project(harness, workspace)
        status, payload = _json(harness, "GET", _path(project))
        assert status == 200, payload
        assert payload["observatory_root_present"] is False
        assert payload["sources"] == [
            {
                "relative_path": ".harness-x",
                "record_kind": "observatory_root",
                "status": "symlink_rejected",
                "size_bytes": None,
                "source_sha256": None,
                "detail": "observatory does not follow symlinked evidence",
            }
        ]
        assert "outside-root-secret" not in json.dumps(payload, sort_keys=True)
    finally:
        harness.close()


def test_unknown_project_and_mutation_shaped_path_do_not_create_observatory_authority(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = _Harness(tmp_path / "server")
    try:
        project = _project(harness, workspace)
        status, _payload = _json(
            harness,
            "GET",
            "/v1/projects/project_" + "f" * 32 + "/improvement-observatory",
        )
        assert status == 404
        status, _payload = _json(harness, "POST", _path(project) + "/promote", body={})
        assert status in {404, 405}
        assert not (workspace / ".harness-x").exists()
    finally:
        harness.close()
