"""Local operator UI transport layered over the authenticated M34/M35 App Server."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from .http_server import LocalAppHTTPServer
from .report_projection import (
    ReportCorruptionError,
    ReportUnavailableError,
    build_coding_report_projection,
)
from .ui_assets import load_ui_asset

_UI_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "connect-src 'self'; "
    "img-src 'none'; "
    "font-src 'none'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


class LocalOperatorHTTPServer(LocalAppHTTPServer):
    """Serve the operator UI without weakening inherited API authentication.

    Static UI assets contain no session data or credentials and remain public on the loopback
    origin. Stateful inherited APIs still use the M34 bearer-token boundary. M38 adds only one
    authenticated read-only projection for the canonical durable coding report.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.ui_url = f"{self.base_url}/ui/"

    def _handler_type(self):
        base_handler = super()._handler_type()
        service = self.service
        token = self.token

        class Handler(base_handler):
            server_version = "HarnessXAppServer/38"

            def do_GET(self) -> None:  # noqa: N802
                if not self._valid_host():
                    super().do_GET()
                    return
                parsed = urlsplit(self.path)
                if parsed.path in {"/", "/ui"}:
                    self._redirect_ui()
                    return
                asset = load_ui_asset(parsed.path)
                if asset is not None:
                    content_type, body = asset
                    self._ui_asset(content_type, body)
                    return

                pieces = self._session_path(parsed.path)
                if pieces is not None and pieces[1] == "/report":
                    if not self._authorized(token):
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                        return
                    session_id = pieces[0]
                    try:
                        snapshot = service.session(session_id)
                        events = service.store.events(session_id)
                        projection = build_coding_report_projection(
                            snapshot=snapshot,
                            events=events,
                        )
                    except KeyError:
                        self._error(HTTPStatus.NOT_FOUND, "unknown_session")
                        return
                    except ReportUnavailableError as exc:
                        self._error(
                            HTTPStatus.NOT_FOUND,
                            "report_not_available",
                            str(exc)[:4000],
                        )
                        return
                    except ReportCorruptionError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "report_corruption",
                            str(exc)[:4000],
                        )
                        return
                    self._json(HTTPStatus.OK, projection.model_dump(mode="json"))
                    return

                super().do_GET()

            def _redirect_ui(self) -> None:
                self.close_connection = True
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/ui/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _ui_asset(self, content_type: str, body: bytes) -> None:
                self.close_connection = True
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", _UI_CSP)
                self.send_header("Cross-Origin-Opener-Policy", "same-origin")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

        return Handler
