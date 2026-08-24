"""Authenticated M67 Project + Chat API layered over the frozen operator transport."""

from __future__ import annotations

import threading
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from harness_x.product import ProjectChatStore

from .capsule_operator_http_server import LocalOperatorHTTPServer as M55LocalOperatorHTTPServer
from .product_protocol import (
    AppendUserMessageRequest,
    CreateChatRequest,
    CreateProjectRequest,
    RenameChatRequest,
    RenameProjectRequest,
)


class LocalOperatorHTTPServer(M55LocalOperatorHTTPServer):
    """Add one serialized authenticated product API without acquiring execution authority."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.product_store = ProjectChatStore(self.service.root / "product")
        self._product_lock = threading.RLock()

    def _handler_type(self):
        base_handler = super()._handler_type()
        store = self.product_store
        lock = self._product_lock
        token = self.token

        class Handler(base_handler):
            server_version = "HarnessXAppServer/67"

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                if not self._is_product_path(parsed.path):
                    super().do_GET()
                    return
                if not self._valid_host():
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                    return
                if not self._authorized(token):
                    self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                    return
                try:
                    self._product_get(parsed.path, parsed.query)
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "unknown_product_resource")
                except ValueError as exc:
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_product_request", str(exc)[:4000])

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                if not self._is_product_path(parsed.path):
                    super().do_POST()
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
                        "invalid_product_request",
                        "product mutation endpoints do not accept query parameters",
                    )
                    return
                try:
                    self._product_post(parsed.path)
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "unknown_product_resource")
                except ValidationError as exc:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_product_request",
                        str(exc)[:4000],
                    )
                except ValueError as exc:
                    detail = str(exc)[:4000]
                    status = (
                        HTTPStatus.CONFLICT
                        if "already registered" in detail
                        or "archived" in detail
                        or "belongs to another project" in detail
                        else HTTPStatus.BAD_REQUEST
                    )
                    self._error(status, "product_conflict" if status == HTTPStatus.CONFLICT else "invalid_product_request", detail)

            def _product_get(self, path: str, query_string: str) -> None:
                parts = self._product_parts(path)
                if parts == ("v1", "product", "restoration"):
                    self._require_no_query(query_string)
                    with lock:
                        restoration = store.restoration_state()
                    self._json(HTTPStatus.OK, restoration.model_dump(mode="json"))
                    return

                if parts == ("v1", "projects"):
                    include_archived = self._include_archived(query_string)
                    with lock:
                        projects = store.projects(include_archived=include_archived)
                    self._json(
                        HTTPStatus.OK,
                        {
                            "schema_version": "project-list-v1",
                            "projects": [item.model_dump(mode="json") for item in projects],
                        },
                    )
                    return

                if len(parts) >= 3 and parts[:2] == ("v1", "projects"):
                    project_id = parts[2]
                    self._require_project_id(project_id)
                    if len(parts) == 3:
                        self._require_no_query(query_string)
                        with lock:
                            project = store.project(project_id)
                        self._json(HTTPStatus.OK, project.model_dump(mode="json"))
                        return
                    if len(parts) == 4 and parts[3] == "chats":
                        include_archived = self._include_archived(query_string)
                        with lock:
                            chats = store.chats(project_id, include_archived=include_archived)
                        self._json(
                            HTTPStatus.OK,
                            {
                                "schema_version": "chat-list-v1",
                                "project_id": project_id,
                                "chats": [item.model_dump(mode="json") for item in chats],
                            },
                        )
                        return
                    if len(parts) in {5, 6} and parts[3] == "chats":
                        chat_id = parts[4]
                        self._require_chat_id(chat_id)
                        self._require_no_query(query_string)
                        with lock:
                            chat = self._owned_chat(store, project_id, chat_id)
                            if len(parts) == 5:
                                payload = chat.model_dump(mode="json")
                            elif parts[5] == "messages":
                                messages = store.messages(chat_id)
                                payload = {
                                    "schema_version": "chat-message-list-v1",
                                    "project_id": project_id,
                                    "chat_id": chat_id,
                                    "messages": [
                                        item.model_dump(mode="json") for item in messages
                                    ],
                                }
                            else:
                                raise KeyError(path)
                        self._json(HTTPStatus.OK, payload)
                        return
                raise KeyError(path)

            def _product_post(self, path: str) -> None:
                parts = self._product_parts(path)
                if parts == ("v1", "projects"):
                    request = CreateProjectRequest.model_validate(self._read_json())
                    with lock:
                        project = store.create_project(
                            name=request.name,
                            workspace_root=request.workspace_root,
                            default_model_profile=request.default_model_profile,
                        )
                    self._json(HTTPStatus.CREATED, project.model_dump(mode="json"))
                    return

                if len(parts) >= 3 and parts[:2] == ("v1", "projects"):
                    project_id = parts[2]
                    self._require_project_id(project_id)
                    if len(parts) == 4 and parts[3] in {"open", "rename", "archive", "restore"}:
                        action = parts[3]
                        if action == "rename":
                            request = RenameProjectRequest.model_validate(self._read_json())
                        else:
                            self._require_empty_json_body()
                        with lock:
                            if action == "open":
                                project = store.open_project(project_id)
                            elif action == "rename":
                                project = store.rename_project(project_id, name=request.name)
                            elif action == "archive":
                                project = store.archive_project(project_id)
                            else:
                                project = store.restore_project(project_id)
                        self._json(HTTPStatus.OK, project.model_dump(mode="json"))
                        return

                    if len(parts) == 4 and parts[3] == "chats":
                        request = CreateChatRequest.model_validate(self._read_json())
                        with lock:
                            chat = store.create_chat(project_id, title=request.title)
                        self._json(HTTPStatus.CREATED, chat.model_dump(mode="json"))
                        return

                    if len(parts) == 6 and parts[3] == "chats":
                        chat_id = parts[4]
                        self._require_chat_id(chat_id)
                        action = parts[5]
                        if action == "rename":
                            request = RenameChatRequest.model_validate(self._read_json())
                        elif action == "messages":
                            request = AppendUserMessageRequest.model_validate(self._read_json())
                        elif action in {"open", "archive", "restore"}:
                            self._require_empty_json_body()
                        else:
                            raise KeyError(path)
                        with lock:
                            self._owned_chat(store, project_id, chat_id)
                            if action == "open":
                                payload = store.open_chat(chat_id).model_dump(mode="json")
                            elif action == "rename":
                                payload = store.rename_chat(chat_id, title=request.title).model_dump(mode="json")
                            elif action == "archive":
                                payload = store.archive_chat(chat_id).model_dump(mode="json")
                            elif action == "restore":
                                payload = store.restore_chat(chat_id).model_dump(mode="json")
                            else:
                                payload = store.append_text_message(
                                    chat_id,
                                    role="user",
                                    text=request.text,
                                ).model_dump(mode="json")
                        self._json(
                            HTTPStatus.CREATED if action == "messages" else HTTPStatus.OK,
                            payload,
                        )
                        return
                raise KeyError(path)

            def _require_empty_json_body(self) -> None:
                raw = self._read_json()
                if raw != {}:
                    raise ValueError("operation request body must be an empty JSON object")

            @staticmethod
            def _owned_chat(product_store: ProjectChatStore, project_id: str, chat_id: str):
                product_store.project(project_id)
                chat = product_store.chat(chat_id)
                if chat.project_id != project_id:
                    raise ValueError("chat belongs to another project")
                return chat

            @staticmethod
            def _is_product_path(path: str) -> bool:
                return path == "/v1/projects" or path.startswith("/v1/projects/") or path == "/v1/product/restoration"

            @staticmethod
            def _product_parts(path: str) -> tuple[str, ...]:
                if path.endswith("/") and path != "/":
                    raise ValueError("product API paths must not have a trailing slash")
                return tuple(item for item in path.split("/") if item)

            @staticmethod
            def _require_project_id(value: str) -> None:
                if not Handler._valid_hex_id(value, prefix="project_"):
                    raise ValueError("invalid project ID")

            @staticmethod
            def _require_chat_id(value: str) -> None:
                if not Handler._valid_hex_id(value, prefix="chat_"):
                    raise ValueError("invalid chat ID")

            @staticmethod
            def _valid_hex_id(value: str, *, prefix: str) -> bool:
                if not value.startswith(prefix) or len(value) != len(prefix) + 32:
                    return False
                suffix = value[len(prefix) :]
                return all(character in "0123456789abcdef" for character in suffix)

            @staticmethod
            def _require_no_query(query_string: str) -> None:
                if query_string:
                    raise ValueError("endpoint does not accept query parameters")

            @staticmethod
            def _include_archived(query_string: str) -> bool:
                if not query_string:
                    return False
                query = parse_qs(query_string, keep_blank_values=True)
                if set(query) != {"include_archived"} or len(query["include_archived"]) != 1:
                    raise ValueError("only include_archived may be specified once")
                value = query["include_archived"][0].casefold()
                if value == "true":
                    return True
                if value == "false":
                    return False
                raise ValueError("include_archived must be true or false")

        return Handler
