"""M47 operator transport for deterministic terminal session-snapshot export."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from .operator_http_server import LocalOperatorHTTPServer as M46LocalOperatorHTTPServer
from .snapshot_export import (
    RenderedSessionSnapshot,
    SnapshotExportCorruptionError,
    SnapshotExportNotTerminalError,
    SnapshotExportTooLargeError,
    render_terminal_session_snapshot,
)


class LocalOperatorHTTPServer(M46LocalOperatorHTTPServer):
    """Layer the M47 read-only snapshot export over the frozen M46 operator server."""

    def _handler_type(self):
        base_handler = super()._handler_type()
        service = self.service
        token = self.token

        class Handler(base_handler):
            server_version = "HarnessXAppServer/47"

            def do_GET(self) -> None:  # noqa: N802
                if not self._valid_host():
                    super().do_GET()
                    return
                parsed = urlsplit(self.path)
                pieces = self._session_path(parsed.path)
                if pieces is not None and pieces[1] == "/snapshot/export":
                    if not self._authorized(token):
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                        return
                    if parsed.query:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_request",
                            "snapshot export endpoint does not accept query parameters",
                        )
                        return
                    session_id, _ = pieces
                    try:
                        snapshot = service.session(session_id)
                        rendered = render_terminal_session_snapshot(
                            snapshot=snapshot,
                            expected_session_id=session_id,
                        )
                    except KeyError:
                        self._error(HTTPStatus.NOT_FOUND, "unknown_session")
                        return
                    except SnapshotExportNotTerminalError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "snapshot_export_not_terminal",
                            str(exc)[:4000],
                        )
                        return
                    except SnapshotExportTooLargeError as exc:
                        self._error(
                            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                            "snapshot_export_too_large",
                            str(exc)[:4000],
                        )
                        return
                    except SnapshotExportCorruptionError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "snapshot_corruption",
                            str(exc)[:4000],
                        )
                        return
                    self._snapshot_export(rendered)
                    return

                super().do_GET()

            def _snapshot_export(self, rendered: RenderedSessionSnapshot) -> None:
                self.close_connection = True
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="session-snapshot.json"',
                )
                self.send_header("Content-Length", str(rendered.source_bytes))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "X-Harness-X-Snapshot-SHA256",
                    rendered.source_sha256,
                )
                self.send_header(
                    "X-Harness-X-Snapshot-Fingerprint",
                    rendered.fingerprint,
                )
                self.send_header(
                    "X-Harness-X-Snapshot-Revision",
                    str(rendered.revision),
                )
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(rendered.payload)

        return Handler
