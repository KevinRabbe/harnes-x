"""M48 operator transport for bounded same-tab reload reauthentication."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from .reload_auth import ReloadCapabilities
from .snapshot_operator_http_server import LocalOperatorHTTPServer as M47LocalOperatorHTTPServer


class LocalOperatorHTTPServer(M47LocalOperatorHTTPServer):
    """Layer M48 reload capability issuance/redemption over the frozen M47 server."""

    def __init__(self, *args, **kwargs) -> None:
        self.reload_capabilities = ReloadCapabilities()
        super().__init__(*args, **kwargs)

    def _handler_type(self):
        base_handler = super()._handler_type()
        token = self.token
        reload_capabilities = self.reload_capabilities

        class Handler(base_handler):
            server_version = "HarnessXAppServer/48"

            def do_POST(self) -> None:  # noqa: N802
                if not self._valid_host():
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                    return
                parsed = urlsplit(self.path)

                if parsed.path == "/v1/operator/reload-ticket":
                    if parsed.query:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_request",
                            "reload-ticket endpoint does not accept query parameters",
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
                            "invalid_reload_ticket_request",
                            str(exc)[:4000],
                        )
                        return
                    if (
                        not isinstance(raw, dict)
                        or set(raw) != {"previous_ticket"}
                        or (
                            raw.get("previous_ticket") is not None
                            and not isinstance(raw.get("previous_ticket"), str)
                        )
                    ):
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_reload_ticket_request",
                            "reload-ticket request must contain exactly previous_ticket as text or null",
                        )
                        return
                    issued = reload_capabilities.issue(
                        previous_ticket=raw.get("previous_ticket")
                    )
                    self._json(
                        HTTPStatus.OK,
                        {
                            "schema_version": "app-operator-reload-ticket-v1",
                            "ticket": issued,
                        },
                    )
                    return

                if parsed.path == "/v1/operator/reload":
                    if parsed.query:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_request",
                            "reload endpoint does not accept query parameters",
                        )
                        return
                    if not self._same_origin_request():
                        self._error(HTTPStatus.FORBIDDEN, "invalid_origin")
                        return
                    try:
                        raw = self._read_json()
                    except ValueError as exc:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_reload_request",
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
                            "invalid_reload_request",
                            "reload request must contain exactly one text ticket",
                        )
                        return
                    if not reload_capabilities.redeem(raw["ticket"]):
                        self._error(HTTPStatus.UNAUTHORIZED, "reload_rejected")
                        return
                    self._json(
                        HTTPStatus.OK,
                        {
                            "schema_version": "app-operator-reload-v1",
                            "access_token": token,
                        },
                    )
                    return

                super().do_POST()

        return Handler
