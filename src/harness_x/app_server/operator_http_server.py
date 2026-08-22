"""M36 local operator UI transport layered over the authenticated M34/M35 App Server."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from .http_server import LocalAppHTTPServer
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
    """Serve the static M36 operator UI without weakening API authentication.

    The UI assets themselves contain no session data or credentials, so they are available on
    the same loopback origin without bearer authentication. Every stateful API call continues to
    flow through the inherited M34/M35 handlers and therefore still requires the bearer token.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.ui_url = f"{self.base_url}/ui/"

    def _handler_type(self):
        base_handler = super()._handler_type()

        class Handler(base_handler):
            server_version = "HarnessXAppServer/36"

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
