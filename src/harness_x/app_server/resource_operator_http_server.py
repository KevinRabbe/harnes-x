"""M74 authenticated attachment, resource, diff, and registered-output API."""

from __future__ import annotations

import base64
import binascii
from http import HTTPStatus
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import conversation_operator_http_server as _m70
from . import project_settings_operator_http_server as _m73
from .execution_outputs import ExecutionArtifactRecord, ExecutionArtifactRegistry, build_execution_diff_projection
from .resource_execution import (
    ProjectResourceConversationExecutionCoordinator,
    ResourceConversationExecutionSubmitRequest,
)

_m70.ConversationExecutionCoordinator = ProjectResourceConversationExecutionCoordinator
_m70.ConversationExecutionSubmitRequest = ResourceConversationExecutionSubmitRequest

_Server = _m73.LocalOperatorHTTPServer
_MAX_HTTP_ATTACHMENT_BYTES = 1024 * 1024
_MAX_BASE64_CHARS = 4 * ((_MAX_HTTP_ATTACHMENT_BYTES + 2) // 3)
_STRICT = ConfigDict(frozen=True, extra="forbid")


class ProjectAttachmentUploadRequest(BaseModel):
    """JSON upload envelope small enough to remain inside the inherited 2 MiB body limit."""

    model_config = _STRICT

    schema_version: str = Field(pattern=r"^project-attachment-upload-request-v1$")
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default="application/octet-stream", min_length=1, max_length=128)
    data_base64: str = Field(min_length=1, max_length=_MAX_BASE64_CHARS)

    def decoded_bytes(self) -> bytes:
        try:
            data = base64.b64decode(self.data_base64.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise ValueError("attachment data_base64 must be canonical ASCII base64") from exc
        if len(data) > _MAX_HTTP_ATTACHMENT_BYTES:
            raise ValueError("attachment exceeds the M74 HTTP upload byte limit")
        if base64.b64encode(data).decode("ascii") != self.data_base64:
            raise ValueError("attachment data_base64 must use canonical padding")
        return data


if not getattr(_Server, "_m74_project_resources_installed", False):
    _previous_handler_type = _Server._handler_type

    def _handler_type(self):
        base_handler = _previous_handler_type(self)
        owner = self
        token = self.token

        class Handler(base_handler):
            server_version = "HarnessXAppServer/74"

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                attachment = self._attachment_parts(parsed.path)
                execution_resources = self._execution_resource_parts(parsed.path)
                execution_diff = self._execution_diff_parts(parsed.path)
                execution_artifact = self._execution_artifact_parts(parsed.path)
                if (
                    attachment is None
                    and execution_resources is None
                    and execution_diff is None
                    and execution_artifact is None
                ):
                    super().do_GET()
                    return
                if not self._resource_auth(parsed, token):
                    return
                try:
                    if attachment is not None:
                        project_id, attachment_id = attachment
                        if attachment_id is None:
                            self._error(HTTPStatus.NOT_FOUND, "not_found")
                            return
                        self._require_project_id(project_id)
                        with owner._product_lock:
                            record = owner.conversation.resource_store.attachment(
                                project_id,
                                attachment_id,
                            )
                        self._json(HTTPStatus.OK, record.model_dump(mode="json"))
                        return

                    if execution_resources is not None:
                        project_id, chat_id, execution_id = execution_resources
                        self._require_project_id(project_id)
                        self._require_chat_id(chat_id)
                        self._require_execution_id(execution_id)
                        with owner._product_lock:
                            projection = owner.conversation.projection(execution_id)
                            if projection.project_id != project_id or projection.chat_id != chat_id:
                                raise ValueError("conversation execution belongs to another project/chat")
                            snapshot = owner.conversation.resource_execution_store.snapshot(execution_id)
                            if snapshot is None:
                                raise KeyError("execution has no M74 resource snapshot")
                        self._json(HTTPStatus.OK, snapshot.model_dump(mode="json"))
                        return

                    if execution_diff is not None:
                        project_id, chat_id, execution_id = execution_diff
                        projection, session, workspace_root = self._owned_execution(
                            project_id,
                            chat_id,
                            execution_id,
                        )
                        result = build_execution_diff_projection(
                            project_id=project_id,
                            chat_id=chat_id,
                            execution_id=execution_id,
                            snapshot=session,
                            workspace_root=workspace_root,
                        )
                        if projection.session_id != result.session_id:
                            raise RuntimeError("execution diff session identity changed during projection")
                        self._json(HTTPStatus.OK, result.model_dump(mode="json"))
                        return

                    assert execution_artifact is not None
                    project_id, chat_id, execution_id, artifact_id = execution_artifact
                    projection, session, _workspace_root = self._owned_execution(
                        project_id,
                        chat_id,
                        execution_id,
                    )
                    assert projection.session_id is not None
                    with owner._product_lock:
                        artifact_registry = ExecutionArtifactRegistry(owner.conversation.store.root)
                        records = artifact_registry.sync_known_artifacts(
                            project_id=project_id,
                            chat_id=chat_id,
                            execution_id=execution_id,
                            snapshot=session,
                            events=owner.service.store.events(projection.session_id),
                            run_root=owner.service.run_root,
                        )
                        if artifact_id is None:
                            self._json(
                                HTTPStatus.OK,
                                {
                                    "schema_version": "conversation-execution-artifact-list-v1",
                                    "project_id": project_id,
                                    "chat_id": chat_id,
                                    "execution_id": execution_id,
                                    "artifacts": [item.model_dump(mode="json") for item in records],
                                },
                            )
                            return
                        self._require_artifact_id(artifact_id)
                        record = artifact_registry.artifact(artifact_id)
                        if (
                            record.project_id != project_id
                            or record.chat_id != chat_id
                            or record.execution_id != execution_id
                            or record.session_id != projection.session_id
                        ):
                            raise ValueError("execution artifact belongs to another project/chat/execution")
                        data = artifact_registry.bytes_for(
                            record,
                            snapshot=session,
                            run_root=owner.service.run_root,
                        )
                    self._artifact_bytes(record, data)
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "unknown_project_resource")
                except RuntimeError as exc:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "project_resource_corruption",
                        str(exc)[:4000],
                    )
                except ValueError as exc:
                    detail = str(exc)[:4000]
                    conflict = "belongs to another" in detail
                    self._error(
                        HTTPStatus.CONFLICT if conflict else HTTPStatus.BAD_REQUEST,
                        "project_resource_conflict" if conflict else "invalid_project_resource_request",
                        detail,
                    )

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                attachment = self._attachment_parts(parsed.path)
                if attachment is None:
                    super().do_POST()
                    return
                project_id, attachment_id = attachment
                if attachment_id is not None:
                    self._error(HTTPStatus.NOT_FOUND, "not_found")
                    return
                if not self._resource_auth(parsed, token):
                    return
                try:
                    self._require_project_id(project_id)
                    request = ProjectAttachmentUploadRequest.model_validate(self._read_json())
                    data = request.decoded_bytes()
                    with owner._product_lock:
                        record = owner.conversation.resource_store.create_attachment(
                            project_id,
                            filename=request.filename,
                            media_type=request.media_type,
                            data=data,
                        )
                    self._json(HTTPStatus.CREATED, record.model_dump(mode="json"))
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "unknown_project_resource")
                except ValidationError as exc:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_project_resource_request",
                        str(exc)[:4000],
                    )
                except ValueError as exc:
                    detail = str(exc)[:4000]
                    conflict = "archived project" in detail
                    self._error(
                        HTTPStatus.CONFLICT if conflict else HTTPStatus.BAD_REQUEST,
                        "project_resource_conflict" if conflict else "invalid_project_resource_request",
                        detail,
                    )
                except RuntimeError as exc:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "project_resource_corruption",
                        str(exc)[:4000],
                    )

            def _owned_execution(self, project_id: str, chat_id: str, execution_id: str):
                self._require_project_id(project_id)
                self._require_chat_id(chat_id)
                self._require_execution_id(execution_id)
                with owner._product_lock:
                    projection = owner.conversation.projection(execution_id)
                    if projection.project_id != project_id or projection.chat_id != chat_id:
                        raise ValueError("conversation execution belongs to another project/chat")
                    if projection.session_id is None:
                        raise RuntimeError("conversation execution does not have an App Session binding")
                    project = owner.product_store.project(project_id)
                    session = owner.service.session(projection.session_id)
                return projection, session, project.workspace_root

            def _resource_auth(self, parsed, bearer: str) -> bool:
                if not self._valid_host():
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                    return False
                if not self._authorized(bearer):
                    self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                    return False
                if parsed.query:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_project_resource_request",
                        "project resource endpoints do not accept query parameters",
                    )
                    return False
                return True

            def _artifact_bytes(self, record: ExecutionArtifactRecord, data: bytes) -> None:
                self.close_connection = True
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", record.media_type)
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{record.storage_name}"',
                )
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Harness-X-Artifact-SHA256", record.sha256)
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

            @staticmethod
            def _attachment_parts(path: str) -> tuple[str, str | None] | None:
                if path.endswith("/"):
                    return None
                parts = tuple(item for item in path.split("/") if item)
                if (
                    len(parts) not in {4, 5}
                    or parts[:2] != ("v1", "projects")
                    or parts[3] != "attachments"
                ):
                    return None
                return parts[2], None if len(parts) == 4 else parts[4]

            @staticmethod
            def _execution_resource_parts(path: str) -> tuple[str, str, str] | None:
                if path.endswith("/"):
                    return None
                parts = tuple(item for item in path.split("/") if item)
                if (
                    len(parts) != 8
                    or parts[:2] != ("v1", "projects")
                    or parts[3] != "chats"
                    or parts[5] != "executions"
                    or parts[7] != "resources"
                ):
                    return None
                return parts[2], parts[4], parts[6]

            @staticmethod
            def _execution_diff_parts(path: str) -> tuple[str, str, str] | None:
                if path.endswith("/"):
                    return None
                parts = tuple(item for item in path.split("/") if item)
                if (
                    len(parts) != 8
                    or parts[:2] != ("v1", "projects")
                    or parts[3] != "chats"
                    or parts[5] != "executions"
                    or parts[7] != "diff"
                ):
                    return None
                return parts[2], parts[4], parts[6]

            @staticmethod
            def _execution_artifact_parts(path: str) -> tuple[str, str, str, str | None] | None:
                if path.endswith("/"):
                    return None
                parts = tuple(item for item in path.split("/") if item)
                if (
                    len(parts) not in {8, 9}
                    or parts[:2] != ("v1", "projects")
                    or parts[3] != "chats"
                    or parts[5] != "executions"
                    or parts[7] != "artifacts"
                ):
                    return None
                return parts[2], parts[4], parts[6], None if len(parts) == 8 else parts[8]

            @staticmethod
            def _require_artifact_id(value: str) -> None:
                prefix = "artifact_"
                suffix = value[len(prefix) :] if value.startswith(prefix) else ""
                if len(suffix) != 32 or any(character not in "0123456789abcdef" for character in suffix):
                    raise ValueError("invalid execution artifact ID")

        return Handler

    _Server._handler_type = _handler_type
    _Server._m74_project_resources_installed = True

LocalOperatorHTTPServer = _Server

__all__ = ["LocalOperatorHTTPServer", "ProjectAttachmentUploadRequest"]
