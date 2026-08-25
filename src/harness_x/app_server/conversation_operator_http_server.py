"""Authenticated conversation execution plus grounded M70 work-activity API."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from harness_x.core.errors import TraceCorruptionError

from .conversation_execution import (
    ConversationExecutionCoordinator,
    ConversationExecutionSubmitRequest,
)
from .product_operator_http_server import LocalOperatorHTTPServer as M67LocalOperatorHTTPServer
from .work_activity import build_work_activity_page


class LocalOperatorHTTPServer(M67LocalOperatorHTTPServer):
    """Connect Project/Chat turns to App Sessions and project grounded activity read-only.

    The inherited M67 handler remains authoritative for Project/Chat lifecycle and history.
    M69 adds execution-link routes and a reconciliation barrier before product mutations. M70
    adds only a deterministic read projection over existing App Server and causal-trace records.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.conversation = ConversationExecutionCoordinator(
            self.service,
            self.product_store,
            self._product_lock,
            self.service.root / "conversation-executions",
        )
        # Close every durable pre-existing plan before accepting new HTTP work. AppServerService
        # already performed its own session restart recovery during construction.
        self.conversation.reconcile_all()
        self.conversation.start()

    def close(self) -> None:
        self.conversation.stop()
        super().close()

    def work_activity_page(
        self,
        *,
        project_id: str,
        chat_id: str,
        execution_id: str,
        cursor: str | None,
        limit: int,
    ):
        """Build one M70 page after revalidating M69 ownership and durable source coherence."""

        projection = self.conversation.projection(execution_id)
        if projection.project_id != project_id or projection.chat_id != chat_id:
            raise ValueError("conversation execution belongs to another project/chat")
        if projection.session_id is None:
            raise RuntimeError("conversation execution does not have an App Session binding")

        # M69 discovers traces after the runner returns. M70 may attach the same existing trace
        # pointer earlier for observability; this method does not parse, rewrite, or authorize
        # the coding runtime and is already idempotent in AppServerService.
        self.service.discover_trace(projection.session_id)

        # The worker may append a lifecycle event while the HTTP thread is reading. Re-read a
        # small bounded number of times until snapshot.event_count and the verified ledger agree.
        # Trace JSONL itself is independently verified by build_work_activity_page.
        snapshot = self.service.session(projection.session_id)
        app_events = self.service.store.events(projection.session_id)
        for _ in range(4):
            snapshot = self.service.session(projection.session_id)
            app_events = self.service.store.events(projection.session_id)
            if snapshot.event_count == len(app_events):
                break
        else:
            raise RuntimeError("App Session lifecycle changed continuously during activity read")

        return build_work_activity_page(
            project_id=project_id,
            chat_id=chat_id,
            execution_id=execution_id,
            snapshot=snapshot,
            app_events=app_events,
            cursor=cursor,
            limit=limit,
        )

    def _handler_type(self):
        base_handler = super()._handler_type()
        owner = self
        token = self.token

        class Handler(base_handler):
            server_version = "HarnessXAppServer/70"

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                activity_path = self._activity_parts(parsed.path)
                if activity_path is not None:
                    if not self._valid_host():
                        self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                        return
                    if not self._authorized(token):
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                        return
                    try:
                        project_id, chat_id, execution_id = activity_path
                        self._require_project_id(project_id)
                        self._require_chat_id(chat_id)
                        self._require_execution_id(execution_id)
                        cursor, limit = self._activity_query(parsed.query)
                        page = owner.work_activity_page(
                            project_id=project_id,
                            chat_id=chat_id,
                            execution_id=execution_id,
                            cursor=cursor,
                            limit=limit,
                        )
                        self._json(HTTPStatus.OK, page.model_dump(mode="json"))
                    except KeyError:
                        self._error(HTTPStatus.NOT_FOUND, "unknown_conversation_execution")
                    except TraceCorruptionError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "work_activity_corruption",
                            str(exc)[:4000],
                        )
                    except RuntimeError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "work_activity_corruption",
                            str(exc)[:4000],
                        )
                    except ValueError as exc:
                        detail = str(exc)[:4000]
                        status = (
                            HTTPStatus.CONFLICT
                            if "belongs to another" in detail or "archived" in detail
                            else HTTPStatus.BAD_REQUEST
                        )
                        self._error(
                            status,
                            "work_activity_conflict"
                            if status == HTTPStatus.CONFLICT
                            else "invalid_work_activity_request",
                            detail,
                        )
                    return

                execution_path = self._execution_parts(parsed.path)
                if execution_path is None:
                    super().do_GET()
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
                        "invalid_conversation_execution_request",
                        "conversation execution endpoints do not accept query parameters",
                    )
                    return
                try:
                    project_id, chat_id, execution_id = execution_path
                    self._require_project_id(project_id)
                    self._require_chat_id(chat_id)
                    if execution_id is None:
                        projections = owner.conversation.projections_for_chat(project_id, chat_id)
                        self._json(
                            HTTPStatus.OK,
                            {
                                "schema_version": "conversation-execution-list-v1",
                                "project_id": project_id,
                                "chat_id": chat_id,
                                "executions": [
                                    item.model_dump(mode="json") for item in projections
                                ],
                            },
                        )
                        return
                    self._require_execution_id(execution_id)
                    projection = owner.conversation.projection(execution_id)
                    if (
                        projection.project_id != project_id
                        or projection.chat_id != chat_id
                    ):
                        raise ValueError("conversation execution belongs to another project/chat")
                    self._json(HTTPStatus.OK, projection.model_dump(mode="json"))
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "unknown_conversation_execution")
                except RuntimeError as exc:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "conversation_execution_corruption",
                        str(exc)[:4000],
                    )
                except ValueError as exc:
                    detail = str(exc)[:4000]
                    status = (
                        HTTPStatus.CONFLICT
                        if "belongs to another" in detail or "archived" in detail
                        else HTTPStatus.BAD_REQUEST
                    )
                    self._error(
                        status,
                        "conversation_execution_conflict"
                        if status == HTTPStatus.CONFLICT
                        else "invalid_conversation_execution_request",
                        detail,
                    )

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                execution_path = self._execution_parts(parsed.path)
                if execution_path is not None:
                    if not self._valid_host():
                        self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                        return
                    if not self._authorized(token):
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                        return
                    if parsed.query:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_conversation_execution_request",
                            "conversation execution mutation does not accept query parameters",
                        )
                        return
                    project_id, chat_id, execution_id = execution_path
                    if execution_id is not None:
                        self._error(HTTPStatus.NOT_FOUND, "not_found")
                        return
                    try:
                        self._require_project_id(project_id)
                        self._require_chat_id(chat_id)
                        request = ConversationExecutionSubmitRequest.model_validate(
                            self._read_json()
                        )
                        # One chat turn is serialized through its terminal Harness X result.
                        # The same submission ID remains retry-safe while active; a distinct
                        # submission cannot overtake it and break user/result ordering.
                        with owner._product_lock:
                            active = tuple(
                                item
                                for item in owner.conversation.projections_for_chat(
                                    project_id, chat_id
                                )
                                if not item.terminal
                            )
                            if len(active) > 1:
                                raise RuntimeError(
                                    "chat has multiple active conversation executions"
                                )
                            if active and active[0].submission_id != request.submission_id:
                                raise ValueError(
                                    "chat already has an active conversation execution"
                                )
                            projection = owner.conversation.submit(
                                project_id=project_id,
                                chat_id=chat_id,
                                request=request,
                            )
                        self._json(
                            HTTPStatus.ACCEPTED,
                            projection.model_dump(mode="json"),
                        )
                    except KeyError:
                        self._error(HTTPStatus.NOT_FOUND, "unknown_product_resource")
                    except ValidationError as exc:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_conversation_execution_request",
                            str(exc)[:4000],
                        )
                    except RuntimeError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "conversation_execution_corruption",
                            str(exc)[:4000],
                        )
                    except ValueError as exc:
                        detail = str(exc)[:4000]
                        status = (
                            HTTPStatus.CONFLICT
                            if "archived" in detail
                            or "belongs to another" in detail
                            or "already bound" in detail
                            or "active conversation execution" in detail
                            else HTTPStatus.BAD_REQUEST
                        )
                        self._error(
                            status,
                            "conversation_execution_conflict"
                            if status == HTTPStatus.CONFLICT
                            else "invalid_conversation_execution_request",
                            detail,
                        )
                    return

                # All M67 product mutations cross a reconciliation barrier. This closes a plan's
                # reserved message/session/result append windows before another mutation can
                # change the same Project/Chat state.
                if self._is_product_path(parsed.path):
                    if not self._valid_host():
                        self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                        return
                    if not self._authorized(token):
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                        return
                    try:
                        owner.conversation.reconcile_all()
                        archive_target = self._archive_target(parsed.path)
                        if archive_target is not None:
                            project_id, chat_id = archive_target
                            if chat_id is None:
                                owner.conversation.assert_project_archivable(project_id)
                            else:
                                owner.conversation.assert_chat_archivable(project_id, chat_id)
                    except KeyError:
                        self._error(HTTPStatus.NOT_FOUND, "unknown_product_resource")
                        return
                    except (RuntimeError, ValueError) as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "conversation_execution_conflict",
                            str(exc)[:4000],
                        )
                        return
                super().do_POST()

            @staticmethod
            def _activity_parts(path: str) -> tuple[str, str, str] | None:
                if path.endswith("/"):
                    return None
                parts = tuple(item for item in path.split("/") if item)
                if (
                    len(parts) != 8
                    or parts[:2] != ("v1", "projects")
                    or parts[3] != "chats"
                    or parts[5] != "executions"
                    or parts[7] != "activity"
                ):
                    return None
                return parts[2], parts[4], parts[6]

            @staticmethod
            def _activity_query(query: str) -> tuple[str | None, int]:
                if not query:
                    return None, 100
                try:
                    values = parse_qs(query, keep_blank_values=True, strict_parsing=True)
                except ValueError as exc:
                    raise ValueError("invalid work activity query") from exc
                if set(values) - {"cursor", "limit"}:
                    raise ValueError("work activity query accepts only cursor and limit")
                if any(len(items) != 1 for items in values.values()):
                    raise ValueError("work activity query parameters cannot repeat")
                cursor = values.get("cursor", [None])[0]
                if cursor == "":
                    raise ValueError("work activity cursor cannot be blank")
                raw_limit = values.get("limit", ["100"])[0]
                try:
                    limit = int(raw_limit)
                except (TypeError, ValueError) as exc:
                    raise ValueError("work activity limit must be an integer") from exc
                if limit < 1 or limit > 200:
                    raise ValueError("work activity limit must be between 1 and 200")
                return cursor, limit

            @staticmethod
            def _execution_parts(path: str) -> tuple[str, str, str | None] | None:
                if path.endswith("/"):
                    return None
                parts = tuple(item for item in path.split("/") if item)
                if (
                    len(parts) not in {6, 7}
                    or parts[:2] != ("v1", "projects")
                    or parts[3] != "chats"
                    or parts[5] != "executions"
                ):
                    return None
                return parts[2], parts[4], None if len(parts) == 6 else parts[6]

            @staticmethod
            def _archive_target(path: str) -> tuple[str, str | None] | None:
                parts = tuple(item for item in path.split("/") if item)
                if (
                    len(parts) == 4
                    and parts[:2] == ("v1", "projects")
                    and parts[3] == "archive"
                ):
                    return parts[2], None
                if (
                    len(parts) == 6
                    and parts[:2] == ("v1", "projects")
                    and parts[3] == "chats"
                    and parts[5] == "archive"
                ):
                    return parts[2], parts[4]
                return None

            @staticmethod
            def _require_execution_id(value: str) -> None:
                if not Handler._valid_hex_id(value, prefix="exec_"):
                    raise ValueError("invalid conversation execution ID")

        return Handler
