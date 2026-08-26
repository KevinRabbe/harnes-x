"""M75 authenticated everyday reliability projection and explicit recovery actions."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from pydantic import ValidationError

from . import conversation_operator_http_server as _m70
from . import resource_operator_http_server as _m74
from .reliability_execution import (
    ConversationExecutionRetryRequest,
    ConversationExecutionStopRequest,
    ReliableProjectResourceConversationExecutionCoordinator,
)

# M70 constructs its coordinator at server construction time through this module global. M74
# already replaces it with the resource-aware coordinator; M75 deliberately layers one more
# coordinator that preserves all M71-M74 dynamic identity checks for retry-created plans.
_m70.ConversationExecutionCoordinator = ReliableProjectResourceConversationExecutionCoordinator

_Server = _m74.LocalOperatorHTTPServer


if not getattr(_Server, "_m75_everyday_reliability_installed", False):
    _previous_handler_type = _Server._handler_type

    def _handler_type(self):
        base_handler = _previous_handler_type(self)
        owner = self
        token = self.token

        class Handler(base_handler):
            server_version = "HarnessXAppServer/75"

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                parts = self._reliability_parts(parsed.path)
                if parts is None or parts[3] != "reliability":
                    super().do_GET()
                    return
                if not self._reliability_auth(parsed, token):
                    return
                try:
                    project_id, chat_id, execution_id, _action = parts
                    self._require_project_id(project_id)
                    self._require_chat_id(chat_id)
                    self._require_execution_id(execution_id)
                    projection = owner.conversation.reliability_projection(
                        project_id,
                        chat_id,
                        execution_id,
                    )
                    self._json(HTTPStatus.OK, projection.model_dump(mode="json"))
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "unknown_conversation_execution")
                except RuntimeError as exc:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "conversation_reliability_corruption",
                        str(exc)[:4000],
                    )
                except ValueError as exc:
                    self._reliability_value_error(exc)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                parts = self._reliability_parts(parsed.path)
                if parts is None or parts[3] not in {"stop", "retry"}:
                    super().do_POST()
                    return
                if not self._reliability_auth(parsed, token):
                    return
                try:
                    project_id, chat_id, execution_id, action = parts
                    self._require_project_id(project_id)
                    self._require_chat_id(chat_id)
                    self._require_execution_id(execution_id)
                    raw = self._read_json()
                    if action == "stop":
                        ConversationExecutionStopRequest.model_validate(raw)
                        projection = owner.conversation.stop_execution(
                            project_id,
                            chat_id,
                            execution_id,
                        )
                        self._json(HTTPStatus.OK, projection.model_dump(mode="json"))
                        return

                    request = ConversationExecutionRetryRequest.model_validate(raw)
                    projection = owner.conversation.retry_execution(
                        project_id,
                        chat_id,
                        execution_id,
                        request,
                    )
                    self._json(
                        HTTPStatus.ACCEPTED,
                        {
                            "schema_version": "conversation-execution-retry-result-v1",
                            "source_execution_id": execution_id,
                            "execution": projection.model_dump(mode="json"),
                        },
                    )
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "unknown_conversation_execution")
                except ValidationError as exc:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_conversation_reliability_request",
                        str(exc)[:4000],
                    )
                except RuntimeError as exc:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "conversation_reliability_corruption",
                        str(exc)[:4000],
                    )
                except ValueError as exc:
                    self._reliability_value_error(exc)

            def _reliability_auth(self, parsed, bearer: str) -> bool:
                if not self._valid_host():
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                    return False
                if not self._authorized(bearer):
                    self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                    return False
                if parsed.query:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_conversation_reliability_request",
                        "conversation reliability endpoints do not accept query parameters",
                    )
                    return False
                return True

            def _reliability_value_error(self, exc: ValueError) -> None:
                detail = str(exc)[:4000]
                conflict_markers = (
                    "belongs to another",
                    "active conversation execution",
                    "terminal executions",
                    "archived project/chat",
                    "already bound",
                    "workspace no longer",
                )
                conflict = any(marker in detail for marker in conflict_markers)
                self._error(
                    HTTPStatus.CONFLICT if conflict else HTTPStatus.BAD_REQUEST,
                    (
                        "conversation_reliability_conflict"
                        if conflict
                        else "invalid_conversation_reliability_request"
                    ),
                    detail,
                )

            @staticmethod
            def _reliability_parts(path: str) -> tuple[str, str, str, str] | None:
                if path.endswith("/"):
                    return None
                parts = tuple(item for item in path.split("/") if item)
                if (
                    len(parts) != 8
                    or parts[:2] != ("v1", "projects")
                    or parts[3] != "chats"
                    or parts[5] != "executions"
                    or parts[7] not in {"reliability", "stop", "retry"}
                ):
                    return None
                return parts[2], parts[4], parts[6], parts[7]

            @staticmethod
            def _require_execution_id(value: str) -> None:
                prefix = "exec_"
                suffix = value[len(prefix) :] if value.startswith(prefix) else ""
                if len(suffix) != 32 or any(
                    character not in "0123456789abcdef" for character in suffix
                ):
                    raise ValueError("invalid conversation execution ID")

        return Handler

    _Server._handler_type = _handler_type
    _Server._m75_everyday_reliability_installed = True


LocalOperatorHTTPServer = _Server

__all__ = ["LocalOperatorHTTPServer"]
