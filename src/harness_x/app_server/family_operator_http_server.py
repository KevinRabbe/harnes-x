"""M51 operator transport for tab-scoped reload revocation families."""

from __future__ import annotations

import re
from http import HTTPStatus
from urllib.parse import urlsplit

from .reload_auth import (
    ReloadCapabilityFamilyCapacityError,
    ReloadCapabilityFamilyRevokedError,
)
from .revocation_operator_http_server import (
    LocalOperatorHTTPServer as M50LocalOperatorHTTPServer,
)

_FAMILY_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class LocalOperatorHTTPServer(M50LocalOperatorHTTPServer):
    """Layer family-aware reload issuance/revocation over frozen M50."""

    def _handler_type(self):
        base_handler = super()._handler_type()
        token = self.token
        reload_capabilities = self.reload_capabilities

        class Handler(base_handler):
            server_version = "HarnessXAppServer/51"

            def do_POST(self) -> None:  # noqa: N802
                if not self._valid_host():
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                    return
                parsed = urlsplit(self.path)

                if parsed.path == "/v1/operator/reload-family-ticket":
                    if parsed.query:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_request",
                            "reload-family-ticket endpoint does not accept query parameters",
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
                            "invalid_reload_family_ticket_request",
                            str(exc)[:4000],
                        )
                        return
                    if (
                        not isinstance(raw, dict)
                        or set(raw) != {"previous_ticket", "family"}
                        or (
                            raw.get("previous_ticket") is not None
                            and not isinstance(raw.get("previous_ticket"), str)
                        )
                        or not self._valid_family(raw.get("family"))
                    ):
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_reload_family_ticket_request",
                            "reload-family-ticket request must contain exactly previous_ticket as text or null and one canonical family",
                        )
                        return
                    try:
                        issued = reload_capabilities.issue_for_family(
                            previous_ticket=raw.get("previous_ticket"),
                            family=raw["family"],
                        )
                    except ReloadCapabilityFamilyRevokedError:
                        self._error(HTTPStatus.CONFLICT, "reload_family_revoked")
                        return
                    except ReloadCapabilityFamilyCapacityError:
                        self._error(HTTPStatus.SERVICE_UNAVAILABLE, "reload_family_unavailable")
                        return
                    except RuntimeError:
                        self._error(HTTPStatus.SERVICE_UNAVAILABLE, "reload_unavailable")
                        return
                    self._json(
                        HTTPStatus.OK,
                        {
                            "schema_version": "app-operator-reload-family-ticket-v1",
                            "ticket": issued,
                        },
                    )
                    return

                if parsed.path == "/v1/operator/reload-family-revoke":
                    if parsed.query:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_request",
                            "reload-family-revoke endpoint does not accept query parameters",
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
                            "invalid_reload_family_revoke_request",
                            str(exc)[:4000],
                        )
                        return
                    if (
                        not isinstance(raw, dict)
                        or set(raw) != {"family"}
                        or not self._valid_family(raw.get("family"))
                    ):
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_reload_family_revoke_request",
                            "reload-family-revoke request must contain exactly one canonical family",
                        )
                        return
                    try:
                        reload_capabilities.revoke_family(raw["family"])
                    except ReloadCapabilityFamilyCapacityError:
                        self._error(HTTPStatus.SERVICE_UNAVAILABLE, "reload_family_unavailable")
                        return
                    self._no_content()
                    return

                super().do_POST()

            @staticmethod
            def _valid_family(value: object) -> bool:
                return isinstance(value, str) and _FAMILY_PATTERN.fullmatch(value) is not None

        return Handler
