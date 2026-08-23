from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from harness_x.app_server import LocalOperatorHTTPServer
from harness_x.app_server.reload_auth import ReloadCapabilities
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


def _issue(server: LocalOperatorHTTPServer) -> str:
    with urlopen(
        _post(
            server,
            "/v1/operator/reload-ticket",
            {"previous_ticket": None},
            authorized=True,
            origin=server.base_url,
        ),
        timeout=3.0,
    ) as response:
        return json.loads(response.read())["ticket"]


def _revoke(server: LocalOperatorHTTPServer, ticket: str) -> None:
    with urlopen(
        _post(
            server,
            "/v1/operator/reload-revoke",
            {"ticket": ticket},
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


def test_reload_capability_revoke_is_idempotent_and_tab_local() -> None:
    now = [100.0]
    capabilities = ReloadCapabilities(ttl_seconds=10.0, max_outstanding=4, clock=lambda: now[0])
    tab_a = capabilities.issue()
    tab_b = capabilities.issue()

    assert capabilities.revoke(tab_a)
    assert not capabilities.revoke(tab_a)
    assert not capabilities.redeem(tab_a)
    assert capabilities.redeem(tab_b)

    expired = capabilities.issue()
    now[0] = 110.0
    assert not capabilities.revoke(expired)
    assert capabilities.outstanding_count == 0

    assert not capabilities.revoke(None)
    assert not capabilities.revoke("\N{SNOWMAN}")


def test_reload_revoke_is_idempotent_and_prevents_future_redemption(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        tab_a = _issue(server)
        tab_b = _issue(server)

        _revoke(server, tab_a)
        _revoke(server, tab_a)
        _revoke(server, "unknown-text-capability")

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
        payload = json.loads(exc_info.value.read())
        assert payload["schema_version"] == "app-server-error-v1"
        assert payload["error"] == "reload_rejected"

        assert _redeem(server, tab_b)["access_token"] == server.token
    finally:
        server.close()
        service.close()


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    (("query", 400), ("origin", 403), ("auth", 401), ("shape", 400)),
)
def test_reload_revoke_rejections_happen_before_revocation(
    tmp_path: Path,
    mode: str,
    expected_status: int,
) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        ticket = _issue(server)
        path = "/v1/operator/reload-revoke"
        payload: object = {"ticket": ticket}
        authorized = True
        origin = server.base_url

        if mode == "query":
            path += "?ticket=ignored"
        elif mode == "origin":
            origin = "http://example.invalid"
        elif mode == "auth":
            authorized = False
        elif mode == "shape":
            payload = {"ticket": ticket, "extra": True}

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _post(
                    server,
                    path,
                    payload,
                    authorized=authorized,
                    origin=origin,
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == expected_status
        assert _redeem(server, ticket)["access_token"] == server.token
    finally:
        server.close()
        service.close()


def test_reload_revoke_oversized_body_does_not_consume_ticket(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        ticket = _issue(server)
        request = Request(
            server.base_url + "/v1/operator/reload-revoke",
            data=b"{" + (b"x" * (3 * 1024 * 1024)) + b"}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {server.token}",
                "Content-Type": "application/json",
                "Origin": server.base_url,
            },
            method="POST",
        )
        with pytest.raises(HTTPError) as exc_info:
            urlopen(request, timeout=3.0)
        assert exc_info.value.code == 400
        payload = json.loads(exc_info.value.read())
        assert payload["schema_version"] == "app-server-error-v1"
        assert payload["error"] == "invalid_reload_revoke_request"

        assert _redeem(server, ticket)["access_token"] == server.token
    finally:
        server.close()
        service.close()
