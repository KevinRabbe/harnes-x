from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest

from harness_x.app_server.project_settings_operator_http_server import LocalOperatorHTTPServer
from harness_x.app_server.service import AppServerService
from harness_x.product import ProjectSettingsStore
from harness_x.reasoning.adapters.openai_compatible import (
    OpenAICompatibleConnectionResult,
    OpenAICompatibleReasoningCore,
)


class _ServerHarness:
    def __init__(self, root: Path) -> None:
        self.service = AppServerService(root / "data")
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
            "name": "Settings project",
            "workspace_root": str(workspace),
            "default_model_profile": None,
        },
    )
    assert status == 201, project
    return project


def _replace_body(**overrides) -> dict:
    body = {
        "schema_version": "replace-project-settings-request-v1",
        "model_profile": "qwen3-8b",
        "verification_strategy": "pytest_and_diff_check",
        "project_instructions": "Preserve the public API.",
        "autonomy_profile": "cautious",
    }
    body.update(overrides)
    return body


def test_model_profile_projection_is_authenticated_curated_and_secret_free(tmp_path: Path) -> None:
    harness = _ServerHarness(tmp_path / "server")
    try:
        status, unauthorized = _http(harness, "GET", "/v1/model-profiles", token=None)
        assert status == 401 and unauthorized["error"] == "unauthorized"

        status, payload = _http(harness, "GET", "/v1/model-profiles")
        assert status == 200, payload
        assert payload["schema_version"] == "model-profile-list-v1"
        assert [item["profile_id"] for item in payload["profiles"]] == [
            "main",
            "qwen3-8b",
            "coder",
            "reasoning",
            "api",
        ]
        assert payload["profiles"][0]["provider"] == "qwen"
        assert payload["profiles"][-1]["provider"] == "openai"
        serialized = json.dumps(payload, sort_keys=True)
        for forbidden in (
            "base_url",
            "api_key_env",
            "OPENAI_API_KEY",
            "Authorization",
            "Bearer ",
            "token_path",
            "server_info_path",
            "project-chat-state.json",
            "settings.json",
            "http://",
            "https://",
        ):
            assert forbidden not in serialized
    finally:
        harness.close()


def test_project_settings_get_replace_restart_and_archived_conflict(tmp_path: Path) -> None:
    root = tmp_path / "server"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = _ServerHarness(root)
    project_id = ""
    try:
        project = _create_project(harness, workspace)
        project_id = project["project_id"]
        path = f"/v1/projects/{project_id}/settings"

        status, defaults = _http(harness, "GET", path)
        assert status == 200, defaults
        assert defaults["schema_version"] == "project-settings-v1"
        assert defaults["model_profile"] == "main"
        assert defaults["verification_strategy"] == "diff_check"
        assert defaults["autonomy_profile"] == "standard"
        assert defaults["revision"] == 1

        status, unauthorized = _http(
            harness,
            "POST",
            path,
            token=None,
            body=_replace_body(),
        )
        assert status == 401 and unauthorized["error"] == "unauthorized"
        assert ProjectSettingsStore(harness.server.product_store).persisted(project_id) is False

        status, updated = _http(harness, "POST", path, body=_replace_body())
        assert status == 200, updated
        assert updated["model_profile"] == "qwen3-8b"
        assert updated["verification_strategy"] == "pytest_and_diff_check"
        assert updated["project_instructions"] == "Preserve the public API."
        assert updated["autonomy_profile"] == "cautious"
        assert updated["revision"] == 1
        assert ProjectSettingsStore(harness.server.product_store).persisted(project_id) is True

        status, second = _http(
            harness,
            "POST",
            path,
            body=_replace_body(project_instructions="Second revision."),
        )
        assert status == 200 and second["revision"] == 2

        status, archived = _http(
            harness,
            "POST",
            f"/v1/projects/{project_id}/archive",
            body={},
        )
        assert status == 200 and archived["archived"] is True
        status, conflict = _http(harness, "POST", path, body=_replace_body())
        assert status == 409 and conflict["error"] == "project_settings_conflict"
    finally:
        harness.close()

    restarted = _ServerHarness(root)
    try:
        status, restored = _http(restarted, "GET", f"/v1/projects/{project_id}/settings")
        assert status == 200, restored
        assert restored["revision"] == 2
        assert restored["project_instructions"] == "Second revision."
    finally:
        restarted.close()


def test_settings_api_rejects_browser_authored_runtime_and_transport_fields(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = _ServerHarness(tmp_path / "server")
    try:
        project = _create_project(harness, workspace)
        path = f"/v1/projects/{project['project_id']}/settings"
        for extra in (
            {"verification_commands": ["rm -rf ."]},
            {"base_url": "https://example.invalid/v1"},
            {"api_key": "secret"},
            {"api_key_env": "ATTACKER_CONTROLLED_ENV"},
            {"max_tool_actions": 2048},
        ):
            status, payload = _http(harness, "POST", path, body=_replace_body(**extra))
            assert status == 400, payload
            assert payload["error"] == "invalid_project_settings_request"

        status, unknown = _http(
            harness,
            "POST",
            path,
            body=_replace_body(model_profile="not-a-built-in-profile"),
        )
        assert status == 409 and unknown["error"] == "project_settings_conflict"

        status, queried = _http(harness, "GET", path + "?include_internal=true")
        assert status == 400 and queried["error"] == "invalid_project_settings_request"
    finally:
        harness.close()


def test_connection_probe_uses_server_owned_profile_and_returns_bounded_safe_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = _ServerHarness(tmp_path / "server")
    try:
        project = _create_project(harness, workspace)
        path = f"/v1/projects/{project['project_id']}/settings/test-connection"
        captured = {}

        def fake_test_connection(self):
            captured["provider"] = self.settings.provider
            captured["base_url"] = self.settings.base_url
            captured["model"] = self.settings.model
            captured["api_key_env"] = self.settings.api_key_env
            return OpenAICompatibleConnectionResult(
                ready=True,
                provider=self.settings.provider,
                configured_model=self.settings.model,
                advertised_model_ids=(self.settings.model, "another-model"),
            )

        monkeypatch.setattr(OpenAICompatibleReasoningCore, "test_connection", fake_test_connection)
        status, payload = _http(
            harness,
            "POST",
            path,
            body={
                "schema_version": "model-profile-connection-test-request-v1",
                "profile_id": "main",
            },
        )
        assert status == 200, payload
        assert payload == {
            "schema_version": "model-profile-connection-test-v1",
            "ready": True,
            "provider": "qwen",
            "configured_model": "Qwen/Qwen3.8-27B",
            "advertised_model_ids": ["Qwen/Qwen3.8-27B", "another-model"],
            "supported": True,
        }
        assert captured == {
            "provider": "qwen",
            "base_url": "http://127.0.0.1:8000/v1",
            "model": "Qwen/Qwen3.8-27B",
            "api_key_env": None,
        }
        serialized = json.dumps(payload, sort_keys=True)
        assert "127.0.0.1" not in serialized and "base_url" not in serialized

        status, strict = _http(
            harness,
            "POST",
            path,
            body={
                "schema_version": "model-profile-connection-test-request-v1",
                "profile_id": "main",
                "base_url": "https://attacker.invalid/v1",
            },
        )
        assert status == 400 and strict["error"] == "invalid_project_settings_request"
    finally:
        harness.close()


def test_missing_api_key_probe_is_sanitized_and_never_attempts_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = _ServerHarness(tmp_path / "server")
    try:
        project = _create_project(harness, workspace)
        status, payload = _http(
            harness,
            "POST",
            f"/v1/projects/{project['project_id']}/settings/test-connection",
            body={
                "schema_version": "model-profile-connection-test-request-v1",
                "profile_id": "api",
            },
        )
        assert status == 200, payload
        assert payload == {
            "schema_version": "model-profile-connection-test-v1",
            "ready": False,
            "provider": "openai",
            "configured_model": "gpt-5.6-sol",
            "advertised_model_ids": [],
            "supported": True,
        }
        serialized = json.dumps(payload, sort_keys=True)
        for forbidden in ("OPENAI_API_KEY", "api.openai.com", "https://", "reasoning endpoint"):
            assert forbidden not in serialized
    finally:
        harness.close()
