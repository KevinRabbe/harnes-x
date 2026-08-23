from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from harness_x.app_server import LocalOperatorHTTPServer
from harness_x.app_server.reload_auth import (
    MAX_OUTSTANDING_RELOAD_CAPABILITIES,
    RELOAD_CAPABILITY_BYTES,
    RELOAD_CAPABILITY_TTL_SECONDS,
    ReloadCapabilities,
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
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
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


def _issue(server: LocalOperatorHTTPServer, *, previous_ticket: str | None = None) -> str:
    with urlopen(
        _post(
            server,
            "/v1/operator/reload-ticket",
            {"previous_ticket": previous_ticket},
            authorized=True,
            origin=server.base_url,
        ),
        timeout=3.0,
    ) as response:
        payload = json.loads(response.read())
        assert response.status == 200
        assert response.headers.get("Cache-Control") == "no-store"
        assert payload["schema_version"] == "app-operator-reload-ticket-v1"
        ticket = payload["ticket"]
        assert isinstance(ticket, str)
        assert len(ticket) == 43
        return ticket


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
        assert response.status == 200
        assert response.headers.get("Cache-Control") == "no-store"
        return json.loads(response.read())


def test_reload_capabilities_are_digest_only_single_use_expiring_and_multi_ticket() -> None:
    now = [100.0]
    tickets = ReloadCapabilities(ttl_seconds=10.0, max_outstanding=4, clock=lambda: now[0])

    first = tickets.issue()
    second = tickets.issue()
    assert RELOAD_CAPABILITY_BYTES >= 32
    assert len(first) == 43
    assert first != second
    assert tickets.outstanding_count == 2
    assert first not in repr(vars(tickets))
    assert second not in repr(vars(tickets))
    assert all(len(digest) == hashlib.sha256().digest_size for digest, _ in tickets._entries)
    assert hashlib.sha256(first.encode("ascii")).digest() in {digest for digest, _ in tickets._entries}

    assert tickets.redeem(first)
    assert not tickets.redeem(first)
    assert tickets.redeem(second)
    assert tickets.outstanding_count == 0

    expiring = tickets.issue()
    now[0] = 110.0
    assert not tickets.redeem(expiring)
    assert tickets.outstanding_count == 0


def test_reload_capability_replacement_preserves_other_tabs_and_bounds_count() -> None:
    tickets = ReloadCapabilities(ttl_seconds=10.0, max_outstanding=2)
    tab_a = tickets.issue()
    tab_b = tickets.issue()
    replacement = tickets.issue(previous_ticket=tab_a)

    assert not tickets.redeem(tab_a)
    assert tickets.redeem(tab_b)
    assert tickets.redeem(replacement)

    first = tickets.issue()
    second = tickets.issue()
    third = tickets.issue()
    assert tickets.outstanding_count == 2
    assert not tickets.redeem(first)
    assert tickets.redeem(second)
    assert tickets.redeem(third)


def test_reload_capability_invalid_previous_never_blocks_authenticated_issue() -> None:
    tickets = ReloadCapabilities()
    existing = tickets.issue()
    replacement = tickets.issue(previous_ticket="not-a-real-capability")
    assert tickets.redeem(existing)
    assert tickets.redeem(replacement)


@pytest.mark.parametrize("ttl", [0, -1, RELOAD_CAPABILITY_TTL_SECONDS + 0.001])
def test_reload_capability_rejects_invalid_lifetime(ttl: float) -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        ReloadCapabilities(ttl_seconds=ttl)


@pytest.mark.parametrize("count", [0, -1, 65])
def test_reload_capability_rejects_invalid_count(count: int) -> None:
    with pytest.raises(ValueError, match="max_outstanding"):
        ReloadCapabilities(max_outstanding=count)


def test_reload_http_requires_auth_origin_exact_shapes_and_single_use(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _post(
                    server,
                    "/v1/operator/reload-ticket",
                    {"previous_ticket": None},
                    origin=server.base_url,
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == 401

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _post(
                    server,
                    "/v1/operator/reload-ticket",
                    {"previous_ticket": None},
                    authorized=True,
                    origin="http://example.invalid",
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == 403

        ticket = _issue(server)
        payload = _redeem(server, ticket)
        assert payload == {
            "schema_version": "app-operator-reload-v1",
            "access_token": server.token,
        }

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _post(
                    server,
                    "/v1/operator/reload",
                    {"ticket": ticket},
                    origin=server.base_url,
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == 401
        assert json.loads(exc_info.value.read())["error"] == "reload_rejected"
    finally:
        server.close()
        service.close()


def test_rejected_origin_query_or_shape_does_not_consume_reload_ticket(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        for mode in ("origin", "query", "shape"):
            ticket = _issue(server)
            if mode == "origin":
                request = _post(
                    server,
                    "/v1/operator/reload",
                    {"ticket": ticket},
                    origin="http://example.invalid",
                )
                expected = 403
            elif mode == "query":
                request = _post(
                    server,
                    "/v1/operator/reload?ticket=ignored",
                    {"ticket": ticket},
                    origin=server.base_url,
                )
                expected = 400
            else:
                request = _post(
                    server,
                    "/v1/operator/reload",
                    {"ticket": ticket, "extra": True},
                    origin=server.base_url,
                )
                expected = 400
            with pytest.raises(HTTPError) as exc_info:
                urlopen(request, timeout=3.0)
            assert exc_info.value.code == expected
            assert _redeem(server, ticket)["access_token"] == server.token
    finally:
        server.close()
        service.close()


def test_reload_ticket_cannot_authorize_normal_api_and_rotation_is_tab_local(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        tab_a = _issue(server)
        tab_b = _issue(server)
        tab_a_next = _issue(server, previous_ticket=tab_a)

        request = Request(
            server.base_url + "/v1/sessions",
            headers={"Authorization": f"Bearer {tab_a_next}"},
            method="GET",
        )
        with pytest.raises(HTTPError) as exc_info:
            urlopen(request, timeout=3.0)
        assert exc_info.value.code == 401

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _post(
                    server,
                    "/v1/operator/reload",
                    {"ticket": tab_a},
                    origin=server.base_url,
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == 401
        assert _redeem(server, tab_b)["access_token"] == server.token
        assert _redeem(server, tab_a_next)["access_token"] == server.token
    finally:
        server.close()
        service.close()


def test_reload_capability_is_process_ephemeral_across_server_restart(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    transport = tmp_path / "transport"
    first = LocalOperatorHTTPServer(service, transport, port=0)
    first.start_in_thread()
    ticket = _issue(first)
    persistent_token = first.token
    first.close()

    second = LocalOperatorHTTPServer(service, transport, port=0)
    second.start_in_thread()
    try:
        assert second.token == persistent_token
        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _post(
                    second,
                    "/v1/operator/reload",
                    {"ticket": ticket},
                    origin=second.base_url,
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == 401
        assert json.loads(exc_info.value.read())["error"] == "reload_rejected"
    finally:
        second.close()
        service.close()


def test_reload_transport_defaults_are_bounded() -> None:
    assert RELOAD_CAPABILITY_BYTES == 32
    assert RELOAD_CAPABILITY_TTL_SECONDS == 300.0
    assert 1 <= MAX_OUTSTANDING_RELOAD_CAPABILITIES <= 64
