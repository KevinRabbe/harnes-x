from __future__ import annotations

import json
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


def test_cli_startup_json_never_prints_bootstrap_ticket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_url = "http://127.0.0.1:12345/ui/#bootstrap=secret-ticket-material"
    opened: list[str] = []

    class FakeService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeTickets:
        def invalidate(self) -> None:
            pass

    class FakeHTTPD:
        def server_close(self) -> None:
            pass

    class FakeServer:
        def __init__(self, _service, root, **_kwargs) -> None:
            root = Path(root)
            self.base_url = "http://127.0.0.1:12345"
            self.ui_url = self.base_url + "/ui/"
            self.token_path = root / "access-token"
            self.info_path = root / "server-info.json"
            self.bootstrap_tickets = FakeTickets()
            self.httpd = FakeHTTPD()

        def issue_ui_bootstrap_url(self) -> str:
            return secret_url

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "AppServerService", FakeService)
    monkeypatch.setattr(cli_module, "LocalOperatorHTTPServer", FakeServer)
    monkeypatch.setattr(
        cli_module.webbrowser,
        "open",
        lambda url, **_kwargs: opened.append(url) or True,
    )

    assert cli_module.main(["--root", str(tmp_path), "--open-ui"]) == 0
    startup_text = capsys.readouterr().out.strip()
    startup = json.loads(startup_text)
    assert opened == [secret_url]
    assert "secret-ticket-material" not in startup_text
    assert "#bootstrap=" not in startup_text
    assert startup["ui_url"] == "http://127.0.0.1:12345/ui/"
    assert startup["ui_open_requested"] is True
    assert startup["ui_opened"] is True
