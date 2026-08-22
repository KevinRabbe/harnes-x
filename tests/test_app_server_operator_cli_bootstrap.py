from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from harness_x.app_server import AppServerService, LocalOperatorHTTPServer
from harness_x.app_server import cli as cli_module


def _ticket(url: str) -> str:
    parsed = urlsplit(url)
    assert parsed.query == ""
    assert parsed.fragment.startswith("bootstrap=")
    return parsed.fragment.removeprefix("bootstrap=")


def test_open_ui_uses_disposable_fragment_without_persisting_ticket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    opened: list[tuple[str, int]] = []

    def fake_open(url: str, *, new: int = 0) -> bool:
        opened.append((url, new))
        return True

    monkeypatch.setattr(cli_module.webbrowser, "open", fake_open)
    try:
        assert cli_module._open_operator_ui(server)
        assert len(opened) == 1
        url, new = opened[0]
        ticket = _ticket(url)
        assert new == 2
        assert url.startswith(server.ui_url + "#bootstrap=")
        assert server.token not in url
        assert ticket not in server.info_path.read_text(encoding="utf-8")
        assert ticket not in server.token_path.read_text(encoding="utf-8")
        assert server.bootstrap_tickets.has_outstanding_ticket
    finally:
        server.close()
        service.close()


def test_failed_browser_open_invalidates_disposable_ticket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    monkeypatch.setattr(cli_module.webbrowser, "open", lambda *_args, **_kwargs: False)
    try:
        assert not cli_module._open_operator_ui(server)
        assert not server.bootstrap_tickets.has_outstanding_ticket
    finally:
        server.close()
        service.close()
