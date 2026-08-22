from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pytest

from harness_x.app_server import AppServerService, LocalOperatorHTTPServer


def _post(
    server: LocalOperatorHTTPServer,
    path: str,
    payload: object,
    *,
    origin: str | None,
    host: str | None = None,
):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if origin is not None:
        headers["Origin"] = origin
    if host is not None:
        headers["Host"] = host
    return Request(
        server.base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )


def _ticket_from_url(url: str) -> str:
    parsed = urlsplit(url)
    assert parsed.query == ""
    assert parsed.fragment.startswith("bootstrap=")
    return parsed.fragment.removeprefix("bootstrap=")


def test_bootstrap_exchange_returns_existing_bearer_once_without_persistence(
    tmp_path: Path,
) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        bootstrap_url = server.issue_ui_bootstrap_url()
        ticket = _ticket_from_url(bootstrap_url)
        assert server.token not in bootstrap_url
        assert ticket not in server.info_path.read_text(encoding="utf-8")
        assert ticket not in server.token_path.read_text(encoding="utf-8")

        request = _post(
            server,
            "/v1/operator/bootstrap",
            {"ticket": ticket},
            origin=server.base_url,
        )
        with urlopen(request, timeout=3.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert payload == {
            "schema_version": "app-operator-bootstrap-v1",
            "access_token": server.token,
        }
        assert not server.bootstrap_tickets.has_outstanding_ticket

        with pytest.raises(HTTPError) as used:
            urlopen(
                _post(
                    server,
                    "/v1/operator/bootstrap",
                    {"ticket": ticket},
                    origin=server.base_url,
                ),
                timeout=3.0,
            )
        assert used.value.code == 401
        used_payload = json.loads(used.value.read().decode("utf-8"))
        assert used_payload["error"] == "bootstrap_rejected"

        with pytest.raises(HTTPError) as unknown:
            urlopen(
                _post(
                    server,
                    "/v1/operator/bootstrap",
                    {"ticket": "x" * 43},
                    origin=server.base_url,
                ),
                timeout=3.0,
            )
        assert unknown.value.code == 401
        unknown_payload = json.loads(unknown.value.read().decode("utf-8"))
        assert unknown_payload == used_payload

        with pytest.raises(HTTPError) as unauthenticated:
            urlopen(Request(server.base_url + "/v1/sessions", method="GET"), timeout=3.0)
        assert unauthenticated.value.code == 401
    finally:
        server.close()
        service.close()


def test_bootstrap_exchange_fails_closed_without_consuming_ticket(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        ticket = _ticket_from_url(server.issue_ui_bootstrap_url())

        cases = [
            (_post(server, "/v1/operator/bootstrap", {"ticket": ticket}, origin=None), 403),
            (
                _post(
                    server,
                    "/v1/operator/bootstrap",
                    {"ticket": ticket},
                    origin="http://attacker.invalid",
                ),
                403,
            ),
            (
                _post(
                    server,
                    "/v1/operator/bootstrap?ticket=ignored",
                    {"ticket": ticket},
                    origin=server.base_url,
                ),
                400,
            ),
            (
                _post(
                    server,
                    "/v1/operator/bootstrap",
                    {"ticket": ticket, "extra": True},
                    origin=server.base_url,
                ),
                400,
            ),
            (
                _post(
                    server,
                    "/v1/operator/bootstrap",
                    {"ticket": ticket},
                    origin=server.base_url,
                    host="attacker.invalid",
                ),
                400,
            ),
        ]
        for request, expected in cases:
            with pytest.raises(HTTPError) as exc_info:
                urlopen(request, timeout=3.0)
            assert exc_info.value.code == expected
            assert server.bootstrap_tickets.has_outstanding_ticket

        with urlopen(
            _post(
                server,
                "/v1/operator/bootstrap",
                {"ticket": ticket},
                origin=server.base_url,
            ),
            timeout=3.0,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["access_token"] == server.token
    finally:
        server.close()
        service.close()
