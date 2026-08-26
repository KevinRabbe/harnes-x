"""M73 authenticated project settings, model-profile discovery, and readiness probe API."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harness_x.product import (
    ProjectAutonomyProfile,
    ProjectSettingsStore,
    ProjectVerificationStrategy,
)
from harness_x.reasoning import (
    ModelProfileBackend,
    OpenAICompatibleReasoningCore,
    OpenAICompatibleSettings,
    ReasoningCoreError,
    builtin_model_profiles,
)

from . import conversation_operator_http_server as _m70
from . import sensitive_approval_operator_http_server as _m72
from .project_settings_execution import ProjectSettingsConversationExecutionCoordinator

_m70.ConversationExecutionCoordinator = ProjectSettingsConversationExecutionCoordinator

_Server = _m72.LocalOperatorHTTPServer


class ReplaceProjectSettingsRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^replace-project-settings-request-v1$")
    model_profile: str = Field(min_length=1, max_length=64)
    verification_strategy: ProjectVerificationStrategy
    project_instructions: str = Field(default="", max_length=6000)
    autonomy_profile: ProjectAutonomyProfile


class ModelProfileConnectionTestRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^model-profile-connection-test-request-v1$")
    profile_id: str = Field(min_length=1, max_length=64)


def _profile_projection(profile) -> dict[str, object]:
    return {
        "schema_version": "model-profile-projection-v1",
        "profile_id": profile.profile_id,
        "role": profile.role.value,
        "provider": profile.provider.value,
        "backend": profile.backend.value,
        "model": profile.model,
        "description": profile.description,
        "capabilities": [item.value for item in profile.capabilities],
        "requires_api_key": profile.requires_api_key,
        "connection_test_supported": profile.backend == ModelProfileBackend.OPENAI_COMPATIBLE,
    }


def _test_profile_connection(profile) -> dict[str, object]:
    if profile.backend != ModelProfileBackend.OPENAI_COMPATIBLE or profile.base_url is None:
        return {
            "schema_version": "model-profile-connection-test-v1",
            "ready": False,
            "provider": profile.provider.value,
            "configured_model": profile.model,
            "advertised_model_ids": [],
            "supported": False,
        }
    core = OpenAICompatibleReasoningCore(
        OpenAICompatibleSettings(
            base_url=profile.base_url,
            model=profile.model,
            provider=profile.provider.value,
            timeout_seconds=10.0,
            api_key_env=profile.api_key_env,
            allow_remote_endpoint=profile.allow_remote_endpoint,
            max_output_tokens=profile.max_output_tokens,
            temperature=profile.temperature,
            top_p=profile.top_p,
            reasoning_effort=profile.reasoning_effort,
            prompt_mode=profile.prompt_mode,
        )
    )
    try:
        result = core.test_connection()
        return {
            "schema_version": "model-profile-connection-test-v1",
            "ready": result.ready,
            "provider": result.provider,
            "configured_model": result.configured_model,
            "advertised_model_ids": list(result.advertised_model_ids),
            "supported": True,
        }
    except ReasoningCoreError:
        # Keep credential names, endpoint details, transport errors, and response bodies server-side.
        return {
            "schema_version": "model-profile-connection-test-v1",
            "ready": False,
            "provider": profile.provider.value,
            "configured_model": profile.model,
            "advertised_model_ids": [],
            "supported": True,
        }


if not getattr(_Server, "_m73_project_settings_installed", False):
    _previous_handler_type = _Server._handler_type

    def _handler_type(self):
        base_handler = _previous_handler_type(self)
        owner = self
        token = self.token

        class Handler(base_handler):
            server_version = "HarnessXAppServer/73"

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                if parsed.path == "/v1/model-profiles":
                    if not self._settings_auth(parsed, token):
                        return
                    registry = builtin_model_profiles()
                    self._json(
                        HTTPStatus.OK,
                        {
                            "schema_version": "model-profile-list-v1",
                            "profiles": [_profile_projection(item) for item in registry.profiles],
                        },
                    )
                    return
                project_id = self._settings_project_id(parsed.path)
                if project_id is None:
                    super().do_GET()
                    return
                if not self._settings_auth(parsed, token):
                    return
                try:
                    self._require_project_id(project_id)
                    with owner._product_lock:
                        record = owner.conversation.settings_store.settings(project_id)
                    self._json(HTTPStatus.OK, record.model_dump(mode="json"))
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "unknown_project")
                except (RuntimeError, ValueError) as exc:
                    self._error(HTTPStatus.CONFLICT, "project_settings_conflict", str(exc)[:4000])

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                connection_project_id = self._settings_connection_project_id(parsed.path)
                project_id = self._settings_project_id(parsed.path)
                if connection_project_id is None and project_id is None:
                    super().do_POST()
                    return
                if not self._settings_auth(parsed, token):
                    return
                try:
                    if connection_project_id is not None:
                        self._require_project_id(connection_project_id)
                        request = ModelProfileConnectionTestRequest.model_validate(self._read_json())
                        with owner._product_lock:
                            owner.product_store.project(connection_project_id)
                            profile = builtin_model_profiles().get(request.profile_id)
                        self._json(HTTPStatus.OK, _test_profile_connection(profile))
                        return

                    assert project_id is not None
                    self._require_project_id(project_id)
                    request = ReplaceProjectSettingsRequest.model_validate(self._read_json())
                    try:
                        builtin_model_profiles().get(request.model_profile)
                    except KeyError as exc:
                        raise ValueError(str(exc)) from exc
                    with owner._product_lock:
                        store = ProjectSettingsStore(owner.product_store)
                        record = store.replace(
                            project_id,
                            model_profile=request.model_profile,
                            verification_strategy=request.verification_strategy,
                            project_instructions=request.project_instructions,
                            autonomy_profile=request.autonomy_profile,
                        )
                    self._json(HTTPStatus.OK, record.model_dump(mode="json"))
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "unknown_project")
                except ValidationError as exc:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_project_settings_request",
                        str(exc)[:4000],
                    )
                except ValueError as exc:
                    self._error(HTTPStatus.CONFLICT, "project_settings_conflict", str(exc)[:4000])
                except RuntimeError as exc:
                    self._error(HTTPStatus.CONFLICT, "project_settings_corruption", str(exc)[:4000])

            def _settings_auth(self, parsed, bearer: str) -> bool:
                if not self._valid_host():
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                    return False
                if not self._authorized(bearer):
                    self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                    return False
                if parsed.query:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_project_settings_request",
                        "project settings endpoints do not accept query parameters",
                    )
                    return False
                return True

            @staticmethod
            def _settings_project_id(path: str) -> str | None:
                if path.endswith("/"):
                    return None
                parts = tuple(item for item in path.split("/") if item)
                if len(parts) != 4 or parts[:2] != ("v1", "projects") or parts[3] != "settings":
                    return None
                return parts[2]

            @staticmethod
            def _settings_connection_project_id(path: str) -> str | None:
                if path.endswith("/"):
                    return None
                parts = tuple(item for item in path.split("/") if item)
                if (
                    len(parts) != 5
                    or parts[:2] != ("v1", "projects")
                    or parts[3:] != ("settings", "test-connection")
                ):
                    return None
                return parts[2]

        return Handler

    _Server._handler_type = _handler_type
    _Server._m73_project_settings_installed = True

LocalOperatorHTTPServer = _Server

__all__ = ["LocalOperatorHTTPServer"]
