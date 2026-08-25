"""M72 adapter over the frozen M70 HTTP class with exact approval decisions."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from pydantic import ValidationError

from . import conversation_operator_http_server as _m70
from .approval_contextual_conversation_execution import (
    ApprovalContextualConversationExecutionCoordinator,
)
from .sensitive_approval import (
    ApprovalDecisionRequest,
    SensitiveActionApprovalBroker,
)

_m70.ConversationExecutionCoordinator = ApprovalContextualConversationExecutionCoordinator

_Server = _m70.LocalOperatorHTTPServer

if not getattr(_Server, "_m72_sensitive_approval_installed", False):
    _previous_handler_type = _Server._handler_type
    _previous_close = _Server.close

    def _approval_broker(owner) -> SensitiveActionApprovalBroker | None:
        broker = getattr(owner.service.runner, "approval_broker", None)
        return broker if isinstance(broker, SensitiveActionApprovalBroker) else None

    def _handler_type(self):
        base_handler = _previous_handler_type(self)
        owner = self
        token = self.token
        broker = _approval_broker(owner)

        class Handler(base_handler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                approval_path = self._approval_parts(parsed.path)
                if approval_path is None:
                    super().do_GET()
                    return
                if broker is None:
                    self._error(HTTPStatus.NOT_FOUND, "not_found")
                    return
                if not self._valid_host():
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                    return
                if not self._authorized(token):
                    self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                    return
                if parsed.query:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_sensitive_approval_request",
                        "approval endpoints do not accept query parameters",
                    )
                    return
                project_id, chat_id, execution_id, approval_id = approval_path
                if approval_id is not None:
                    self._error(HTTPStatus.NOT_FOUND, "not_found")
                    return
                try:
                    self._require_project_id(project_id)
                    self._require_chat_id(chat_id)
                    self._require_execution_id(execution_id)
                    projection = owner.conversation.projection(execution_id)
                    if projection.project_id != project_id or projection.chat_id != chat_id:
                        raise ValueError("conversation execution belongs to another project/chat")
                    approvals = broker.projections_for_execution(execution_id)
                    self._json(
                        HTTPStatus.OK,
                        {
                            "schema_version": "sensitive-action-approval-list-v1",
                            "project_id": project_id,
                            "chat_id": chat_id,
                            "execution_id": execution_id,
                            "approvals": [item.model_dump(mode="json") for item in approvals],
                        },
                    )
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "unknown_sensitive_approval_resource")
                except (RuntimeError, ValueError) as exc:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "sensitive_approval_conflict",
                        str(exc)[:4000],
                    )

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                approval_path = self._approval_parts(parsed.path)
                if approval_path is None:
                    super().do_POST()
                    return
                if broker is None:
                    self._error(HTTPStatus.NOT_FOUND, "not_found")
                    return
                if not self._valid_host():
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                    return
                if not self._authorized(token):
                    self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                    return
                if parsed.query:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_sensitive_approval_request",
                        "approval mutation does not accept query parameters",
                    )
                    return
                project_id, chat_id, execution_id, approval_id = approval_path
                if approval_id is None:
                    self._error(HTTPStatus.NOT_FOUND, "not_found")
                    return
                try:
                    self._require_project_id(project_id)
                    self._require_chat_id(chat_id)
                    self._require_execution_id(execution_id)
                    self._require_approval_id(approval_id)
                    request = ApprovalDecisionRequest.model_validate(self._read_json())
                    projection = owner.conversation.projection(execution_id)
                    if projection.project_id != project_id or projection.chat_id != chat_id:
                        raise ValueError("conversation execution belongs to another project/chat")
                    approval = broker.projection(approval_id)
                    if (
                        approval.project_id != project_id
                        or approval.chat_id != chat_id
                        or approval.execution_id != execution_id
                        or approval.session_id != projection.session_id
                    ):
                        raise ValueError("sensitive approval belongs to another execution")
                    if projection.terminal:
                        raise ValueError("cannot decide approval for terminal execution")
                    decided = broker.decide(approval_id, request.decision)
                    self._json(HTTPStatus.OK, decided.model_dump(mode="json"))
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "unknown_sensitive_approval_resource")
                except ValidationError as exc:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_sensitive_approval_request",
                        str(exc)[:4000],
                    )
                except ValueError as exc:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "sensitive_approval_conflict",
                        str(exc)[:4000],
                    )
                except RuntimeError as exc:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "sensitive_approval_corruption",
                        str(exc)[:4000],
                    )

            @staticmethod
            def _approval_parts(path: str) -> tuple[str, str, str, str | None] | None:
                if path.endswith("/"):
                    return None
                parts = tuple(item for item in path.split("/") if item)
                if (
                    len(parts) not in {8, 9}
                    or parts[:2] != ("v1", "projects")
                    or parts[3] != "chats"
                    or parts[5] != "executions"
                    or parts[7] != "approvals"
                ):
                    return None
                return parts[2], parts[4], parts[6], None if len(parts) == 8 else parts[8]

            @staticmethod
            def _require_approval_id(value: str) -> None:
                if not Handler._valid_hex_id(value, prefix="approval_"):
                    raise ValueError("invalid sensitive approval ID")

        return Handler

    def _close(self) -> None:
        broker = _approval_broker(self)
        if broker is not None:
            broker.interrupt_waiters()
        _previous_close(self)

    _Server._handler_type = _handler_type
    _Server.close = _close
    _Server._m72_sensitive_approval_installed = True

LocalOperatorHTTPServer = _Server

__all__ = ["LocalOperatorHTTPServer"]
