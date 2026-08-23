from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from harness_x.app_server import LocalOperatorHTTPServer
from harness_x.app_server.service import AppServerService


def _request(
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
        _request(
            server,
            "/v1/operator/reload-ticket",
            {"previous_ticket": None},
            authorized=True,
            origin=server.base_url,
        ),
        timeout=3.0,
    ) as response:
        return json.loads(response.read())["ticket"]


def _redeem_error(server: LocalOperatorHTTPServer, ticket: str) -> tuple[int, dict[str, object]]:
    with pytest.raises(HTTPError) as exc_info:
        urlopen(
            _request(
                server,
                "/v1/operator/reload",
                {"ticket": ticket},
                origin=server.base_url,
            ),
            timeout=3.0,
        )
    return exc_info.value.code, json.loads(exc_info.value.read())


def test_unknown_malformed_used_and_expired_capabilities_share_generic_rejection(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        now = [100.0]
        server.reload_capabilities._clock = lambda: now[0]
        expired = _issue(server)
        now[0] = 401.0

        for ticket in (expired, "not-a-ticket", "Z" * 43):
            code, payload = _redeem_error(server, ticket)
            assert code == 401
            assert payload == {"error": "reload_rejected"}

        used = _issue(server)
        with urlopen(
            _request(
                server,
                "/v1/operator/reload",
                {"ticket": used},
                origin=server.base_url,
            ),
            timeout=3.0,
        ) as response:
            assert response.status == 200
        code, payload = _redeem_error(server, used)
        assert code == 401
        assert payload == {"error": "reload_rejected"}
    finally:
        server.close()
        service.close()


def test_missing_origin_and_oversized_request_do_not_consume_valid_capability(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        ticket = _issue(server)

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _request(server, "/v1/operator/reload", {"ticket": ticket}),
                timeout=3.0,
            )
        assert exc_info.value.code == 403

        oversized = Request(
            server.base_url + "/v1/operator/reload",
            data=b"{" + (b"x" * (300 * 1024)) + b"}",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": server.base_url,
            },
            method="POST",
        )
        with pytest.raises(HTTPError) as exc_info:
            urlopen(oversized, timeout=3.0)
        assert exc_info.value.code == 400
        assert json.loads(exc_info.value.read())["error"] == "invalid_reload_request"

        with urlopen(
            _request(
                server,
                "/v1/operator/reload",
                {"ticket": ticket},
                origin=server.base_url,
            ),
            timeout=3.0,
        ) as response:
            payload = json.loads(response.read())
            assert payload["access_token"] == server.token
    finally:
        server.close()
        service.close()


def test_reload_ticket_query_and_shape_are_rejected_before_issue(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        for path, payload in (
            ("/v1/operator/reload-ticket?x=1", {"previous_ticket": None}),
            ("/v1/operator/reload-ticket", {}),
            ("/v1/operator/reload-ticket", {"previous_ticket": None, "extra": True}),
        ):
            with pytest.raises(HTTPError) as exc_info:
                urlopen(
                    _request(
                        server,
                        path,
                        payload,
                        authorized=True,
                        origin=server.base_url,
                    ),
                    timeout=3.0,
                )
            assert exc_info.value.code == 400
        assert server.reload_capabilities.outstanding_count == 0
    finally:
        server.close()
        service.close()


def test_reload_capability_generation_failure_is_structured_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        def fail_issue(*, previous_ticket=None):
            del previous_ticket
            raise RuntimeError("simulated rng uniqueness failure")

        monkeypatch.setattr(server.reload_capabilities, "issue", fail_issue)
        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                _request(
                    server,
                    "/v1/operator/reload-ticket",
                    {"previous_ticket": None},
                    authorized=True,
                    origin=server.base_url,
                ),
                timeout=3.0,
            )
        assert exc_info.value.code == 503
        assert json.loads(exc_info.value.read()) == {"error": "reload_unavailable"}
    finally:
        server.close()
        service.close()
