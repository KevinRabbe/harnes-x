"""M50 operator transport for immediate explicit-lock reload-capability revocation."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from .reload_operator_http_server import LocalOperatorHTTPServer as M48LocalOperatorHTTPServer


class LocalOperatorHTTPServer(M48LocalOperatorHTTPServer):
    """Layer one authenticated same-origin reload-capability cleanup route over M48."""

    def _handler_type(self):
        base_handler = super()._handler_type()
        token = self.token
        reload_capabilities = self.reload_capabilities

        class Handler(base_handler):
            server_version = "HarnessXAppServer/50"

            def do_POST(self) -> None:  # noqa: N802
                if not self._valid_host():
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                    return
                parsed = urlsplit(self.path)

                if parsed.path == "/v1/operator/reload-revoke":
                    if parsed.query:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_request",
                            "reload-revoke endpoint does not accept query parameters",
                        )
                        return
                    if not self._same_origin_request():
                        self._error(HTTPStatus.FORBIDDEN, "invalid_origin")
                        return
                    if not self._authorized(token):
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                        return
                    try:
                        raw = self._read_json()
                    except ValueError as exc:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_reload_revoke_request",
                            str(exc)[:4000],
                        )
                        return
                    if (
                        not isinstance(raw, dict)
                        or set(raw) != {"ticket"}
                        or not isinstance(raw.get("ticket"), str)
                    ):
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_reload_revoke_request",
                            "reload-revoke request must contain exactly one text ticket",
                        )
                        return
                    reload_capabilities.revoke(raw["ticket"])
                    self._no_content()
                    return

                super().do_POST()

            def _no_content(self) -> None:
                self.close_connection = True
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.send_header("Content-Length", "0")
                self.end_headers()

        return Handler
