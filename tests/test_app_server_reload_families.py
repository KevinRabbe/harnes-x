from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from harness_x.app_server import LocalOperatorHTTPServer
from harness_x.app_server.reload_auth import (
    MAX_RELOAD_CAPABILITY_FAMILIES,
    ReloadCapabilities,
    ReloadCapabilityFamilyCapacityError,
    ReloadCapabilityFamilyRevokedError,
)
from harness_x.app_server.service import AppServerService


def _post(
    server: LocalOperatorHTTPServer,
    path: str,
    payload: object,
    *,
    authorized: bool = False,
    origin: str | None = None,
) -> Request:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if authorized:
        headers["Authorization"] = f"Bearer {server.token}"
    if origin is not None:
        headers["Origin"] = origin
    return Request(
        server.base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )


def _issue_family(
    server: LocalOperatorHTTPServer,
    family: str,
    *,
    previous_ticket: str | None = None,
) -> str:
    with urlopen(
        _post(
            server,
            "/v1/operator/reload-family-ticket",
            {"previous_ticket": previous_ticket, "family": family},
            authorized=True,
            origin=server.base_url,
        ),
        timeout=3.0,
    ) as response:
        payload = json.loads(response.read())
        assert response.status == 200
        assert response.headers.get("Cache-Control") == "no-store"
        assert payload["schema_version"] == "app-operator-reload-family-ticket-v1"
        ticket = payload["ticket"]
        assert isinstance(ticket, str)
        assert len(ticket) == 43
        return ticket


def _revoke_family(server: LocalOperatorHTTPServer, family: str) -> None:
    with urlopen(
        _post(
            server,
            "/v1/operator/reload-family-revoke",
            {"family": family},
            authorized=True,
            origin=server.base_url,
        ),
        timeout=3.0,
    ) as response:
        assert response.status == 204
        assert response.read() == b""
        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Content-Length") == "0"
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("Referrer-Policy") == "no-referrer"


def _redeem(server: LocalOperatorHTTPServer, ticket: str) -> dict[str, object]:
    with urlopen(
        _post(
            server,
            "/v1/operator/reload",
            {"ticket": ticket},
            origin=server.base_url,
        ),
        timeout=3.0,
    ) as response:
        return json.loads(response.read())


def test_family_issuance_keeps_exactly_one_current_ticket_per_family() -> None:
    capabilities = ReloadCapabilities(max_outstanding=8, max_families=8)
    family_a = "A" * 43
    family_b = "B" * 43

    a1 = capabilities.issue_for_family(family=family_a)
    b1 = capabilities.issue_for_family(family=family_b)
    a2 = capabilities.issue_for_family(family=family_a, previous_ticket=a1)

    assert capabilities.outstanding_count == 2
    assert not capabilities.redeem(a1)
    assert capabilities.redeem(b1)
    assert capabilities.redeem(a2)
    assert capabilities.family_count == 2
    assert capabilities.revoked_family_count == 0


def test_family_retry_replaces_unknown_prior_ticket_without_accumulation() -> None:
    capabilities = ReloadCapabilities(max_outstanding=8, max_families=8)
    family = "C" * 43

    lost_response_ticket = capabilities.issue_for_family(family=family)
    retry_ticket = capabilities.issue_for_family(family=family, previous_ticket=None)

    assert capabilities.outstanding_count == 1
    assert not capabilities.redeem(lost_response_ticket)
    assert capabilities.redeem(retry_ticket)


def test_family_revocation_tombstones_before_or_after_issuance() -> None:
    capabilities = ReloadCapabilities(max_outstanding=8, max_families=8)
    family_after = "D" * 43
    family_before = "E" * 43

    issued = capabilities.issue_for_family(family=family_after)
    assert capabilities.revoke_family(family_after) == 1
    assert capabilities.revoke_family(family_after) == 0
    assert not capabilities.redeem(issued)
    with pytest.raises(ReloadCapabilityFamilyRevokedError):
        capabilities.issue_for_family(family=family_after)

    assert capabilities.revoke_family(family_before) == 0
    with pytest.raises(ReloadCapabilityFamilyRevokedError):
        capabilities.issue_for_family(family=family_before)

    assert capabilities.family_count == 2
    assert capabilities.revoked_family_count == 2


def test_family_registry_capacity_fails_without_evicting_tombstones() -> None:
    capabilities = ReloadCapabilities(max_outstanding=4, max_families=1)
    first_family = "F" * 43
    second_family = "G" * 43

    ticket = capabilities.issue_for_family(family=first_family)
    assert capabilities.revoke_family(first_family) == 1
    assert not capabilities.redeem(ticket)

    with pytest.raises(ReloadCapabilityFamilyCapacityError):
        capabilities.issue_for_family(family=second_family)
    with pytest.raises(ReloadCapabilityFamilyCapacityError):
        capabilities.revoke_family(second_family)
    with pytest.raises(ReloadCapabilityFamilyRevokedError):
        capabilities.issue_for_family(family=first_family)

    assert capabilities.family_count == 1
    assert capabilities.revoked_family_count == 1


@pytest.mark.parametrize("count", [0, -1, MAX_RELOAD_CAPABILITY_FAMILIES + 1])
def test_reload_capability_rejects_invalid_family_bound(count: int) -> None:
    with pytest.raises(ValueError, match="max_families"):
        ReloadCapabilities(max_families=count)


def test_family_revocation_does_not_affect_legacy_or_other_family_ticket() -> None:
    capabilities = ReloadCapabilities(max_outstanding=8, max_families=8)
    legacy = capabilities.issue()
    family_a = "H" * 43
    family_b = "I" * 43
    ticket_a = capabilities.issue_for_family(family=family_a)
    ticket_b = capabilities.issue_for_family(family=family_b)

    assert capabilities.revoke_family(family_a) == 1
    assert not capabilities.redeem(ticket_a)
    assert capabilities.redeem(legacy)
    assert capabilities.redeem(ticket_b)


def test_family_http_issue_revoke_is_tab_local_and_blocks_future_issue(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        family_a = "J" * 43
        family_b = "K" * 43
        ticket_a = _issue_family(server, family_a)
        ticket_b = _issue_family(server, family_b)

        _revoke_family(server, family_a)
        _revoke_family(server, family_a)

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _post(
                    server,
                    "/v1/operator/reload",
                    {"ticket": ticket_a},
                    origin=server.base_url,
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == 401
        assert json.loads(exc_info.value.read())["error"] == "reload_rejected"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _post(
                    server,
                    "/v1/operator/reload-family-ticket",
                    {"previous_ticket": None, "family": family_a},
                    authorized=True,
                    origin=server.base_url,
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == 409
        assert json.loads(exc_info.value.read())["error"] == "reload_family_revoked"

        assert _redeem(server, ticket_b)["access_token"] == server.token
    finally:
        server.close()
        service.close()


def test_family_revoke_before_issue_blocks_delayed_issue_over_http(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        family = "L" * 43
        _revoke_family(server, family)
        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _post(
                    server,
                    "/v1/operator/reload-family-ticket",
                    {"previous_ticket": None, "family": family},
                    authorized=True,
                    origin=server.base_url,
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == 409
        assert json.loads(exc_info.value.read())["error"] == "reload_family_revoked"
    finally:
        server.close()
        service.close()


def test_family_identifier_alone_authorizes_nothing(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        family = "M" * 43
        for path, payload in (
            ("/v1/operator/reload-family-ticket", {"previous_ticket": None, "family": family}),
            ("/v1/operator/reload-family-revoke", {"family": family}),
        ):
            with pytest.raises(HTTPError) as exc_info:
                urlopen(
                    _post(server, path, payload, origin=server.base_url),
                    timeout=3.0,
                )
            assert exc_info.value.code == 401

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                Request(
                    server.base_url + "/v1/sessions",
                    headers={"Authorization": f"Bearer {family}"},
                    method="GET",
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == 401

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _post(server, "/v1/operator/reload", {"ticket": family}, origin=server.base_url),
                timeout=3.0,
            )
        assert exc_info.value.code == 401
    finally:
        server.close()
        service.close()


@pytest.mark.parametrize(
    ("path", "mode", "expected_status"),
    (
        ("/v1/operator/reload-family-ticket", "query", 400),
        ("/v1/operator/reload-family-ticket", "origin", 403),
        ("/v1/operator/reload-family-ticket", "auth", 401),
        ("/v1/operator/reload-family-ticket", "shape", 400),
        ("/v1/operator/reload-family-ticket", "family", 400),
        ("/v1/operator/reload-family-revoke", "query", 400),
        ("/v1/operator/reload-family-revoke", "origin", 403),
        ("/v1/operator/reload-family-revoke", "auth", 401),
        ("/v1/operator/reload-family-revoke", "shape", 400),
        ("/v1/operator/reload-family-revoke", "family", 400),
    ),
)
def test_family_http_rejections_are_fail_closed(
    tmp_path: Path,
    path: str,
    mode: str,
    expected_status: int,
) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        family = "N" * 43
        request_path = path
        authorized = True
        origin = server.base_url
        if path.endswith("ticket"):
            payload: object = {"previous_ticket": None, "family": family}
        else:
            payload = {"family": family}

        if mode == "query":
            request_path += "?family=ignored"
        elif mode == "origin":
            origin = "http://example.invalid"
        elif mode == "auth":
            authorized = False
        elif mode == "shape":
            payload = {**payload, "extra": True}  # type: ignore[arg-type]
        elif mode == "family":
            payload = {**payload, "family": "not-canonical"}  # type: ignore[arg-type]

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _post(
                    server,
                    request_path,
                    payload,
                    authorized=authorized,
                    origin=origin,
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == expected_status
        assert server.reload_capabilities.family_count == 0
    finally:
        server.close()
        service.close()


def test_family_registry_exhaustion_is_visible_over_http(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.reload_capabilities._max_families = 1
    server.start_in_thread()
    try:
        first = "O" * 43
        second = "P" * 43
        _revoke_family(server, first)
        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _post(
                    server,
                    "/v1/operator/reload-family-revoke",
                    {"family": second},
                    authorized=True,
                    origin=server.base_url,
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == 503
        assert json.loads(exc_info.value.read())["error"] == "reload_family_unavailable"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _post(
                    server,
                    "/v1/operator/reload-family-ticket",
                    {"previous_ticket": None, "family": second},
                    authorized=True,
                    origin=server.base_url,
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == 503
        assert json.loads(exc_info.value.read())["error"] == "reload_family_unavailable"
        assert server.reload_capabilities.family_count == 1
        assert server.reload_capabilities.revoked_family_count == 1
    finally:
        server.close()
        service.close()


def test_family_revoke_oversized_body_does_not_register_family(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        connection = HTTPConnection(server.host, server.port, timeout=3.0)
        connection.putrequest("POST", "/v1/operator/reload-family-revoke")
        connection.putheader("Accept", "application/json")
        connection.putheader("Authorization", f"Bearer {server.token}")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Origin", server.base_url)
        connection.putheader("Content-Length", str(3 * 1024 * 1024))
        connection.endheaders()
        response = connection.getresponse()
        try:
            assert response.status == 400
            payload = json.loads(response.read())
        finally:
            connection.close()
        assert payload["error"] == "invalid_reload_family_revoke_request"
        assert server.reload_capabilities.family_count == 0
    finally:
        server.close()
        service.close()
