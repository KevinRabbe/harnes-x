"""M76 authenticated GET-only Improvement Observatory endpoint.

The route resolves the selected project's workspace on the server and projects only bounded
existing evidence below that workspace's fixed ``.harness-x`` root. It never accepts an
observation path and never acquires improvement, promotion, rollback, campaign, or execution
authority.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit

from . import reliability_operator_http_server as _m75
from .improvement_observatory import ImprovementObservatoryProjection
from .improvement_observatory_guard import build_public_improvement_observatory

_Server = _m75.LocalOperatorHTTPServer


def _public_projection(
    projection: ImprovementObservatoryProjection,
    *,
    workspace_root: str,
) -> dict[str, object]:
    """Remove parser payloads and keep rejected root topology fail-visible over HTTP."""

    payload = projection.model_dump(mode="json")
    sources = payload.get("sources", [])
    for source in sources:
        status = source.get("status")
        if status == "malformed":
            source["detail"] = "record failed strict schema or bounded-file validation"
        elif status == "symlink_rejected":
            source["detail"] = "observatory does not follow symlinked evidence"
        elif status == "oversized":
            source["detail"] = "record exceeds observatory read budget"

    fixed_root = Path(workspace_root) / ".harness-x"
    if not projection.observatory_root_present and fixed_root.is_symlink():
        sources.append(
            {
                "relative_path": ".harness-x",
                "record_kind": "observatory_root",
                "status": "symlink_rejected",
                "size_bytes": None,
                "source_sha256": None,
                "detail": "observatory does not follow symlinked evidence",
            }
        )
    return payload


if not getattr(_Server, "_m76_improvement_observatory_installed", False):
    _previous_handler_type = _Server._handler_type

    def _handler_type(self):
        base_handler = _previous_handler_type(self)
        owner = self
        token = self.token

        class Handler(base_handler):
            server_version = "HarnessXAppServer/76"

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                project_id = self._observatory_project_id(parsed.path)
                if project_id is None:
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
                        "invalid_improvement_observatory_request",
                        "improvement observatory does not accept query parameters",
                    )
                    return
                try:
                    self._require_project_id(project_id)
                    with owner._product_lock:
                        project = owner.product_store.project(project_id)
                    projection = build_public_improvement_observatory(
                        project_id=project_id,
                        workspace_root=project.workspace_root,
                    )
                    self._json(
                        HTTPStatus.OK,
                        _public_projection(projection, workspace_root=project.workspace_root),
                    )
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "unknown_product_resource")
                except ValueError:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_improvement_observatory_request",
                        "project observatory identity or workspace is invalid",
                    )

            @staticmethod
            def _observatory_project_id(path: str) -> str | None:
                if path.endswith("/"):
                    return None
                parts = tuple(item for item in path.split("/") if item)
                if (
                    len(parts) != 4
                    or parts[:2] != ("v1", "projects")
                    or parts[3] != "improvement-observatory"
                ):
                    return None
                return parts[2]

        return Handler

    _Server._handler_type = _handler_type
    _Server._m76_improvement_observatory_installed = True


LocalOperatorHTTPServer = _Server

__all__ = ["LocalOperatorHTTPServer"]
