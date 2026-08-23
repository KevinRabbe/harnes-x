from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import harness_x.app_server.cli as app_server_cli


class _FakeService:
    instances: list["_FakeService"] = []

    def __init__(self, root: Path, *, server_version: str) -> None:
        self.root = root
        self.server_version = server_version
        self.closed = False
        self.instances.append(self)

    def close(self) -> None:
        self.closed = True


class _FakeServer:
    instances: list["_FakeServer"] = []

    def __init__(
        self,
        service: _FakeService,
        root: Path,
        *,
        host: str,
        port: int,
        evidence_signing_private_key: Path | None,
    ) -> None:
        self.service = service
        self.root = root
        self.host = host
        self.port = port
        self.evidence_signing_private_key = evidence_signing_private_key
        self.base_url = "http://127.0.0.1:8765"
        self.ui_url = self.base_url + "/ui/"
        self.token_path = root / "token"
        self.info_path = root / "server.json"
        self.httpd = SimpleNamespace(server_close=self._server_close)
        self.server_closed = False
        self.served = False
        self.instances.append(self)

    def serve_forever(self) -> None:
        self.served = True

    def _server_close(self) -> None:
        self.server_closed = True


def _install_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeService.instances.clear()
    _FakeServer.instances.clear()
    monkeypatch.setattr(app_server_cli, "AppServerService", _FakeService)
    monkeypatch.setattr(app_server_cli, "LocalOperatorHTTPServer", _FakeServer)


def test_app_server_parser_exposes_optional_evidence_signing_key() -> None:
    parser = app_server_cli.build_parser()
    unsigned = parser.parse_args([])
    assert unsigned.evidence_signing_private_key is None

    signed = parser.parse_args(["--evidence-signing-private-key", "keys/operator.pem"])
    assert signed.evidence_signing_private_key == Path("keys/operator.pem")


def test_unsigned_startup_passes_no_signer_and_preserves_start_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fakes(monkeypatch)
    root = tmp_path / "app-server"

    assert app_server_cli.main(["--root", str(root), "--port", "0"]) == 0

    server = _FakeServer.instances[-1]
    service = _FakeService.instances[-1]
    assert server.evidence_signing_private_key is None
    assert server.served is True
    assert server.server_closed is True
    assert service.closed is True

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "base_url": "http://127.0.0.1:8765",
        "pid": payload["pid"],
        "schema_version": "app-server-start-v1",
        "server_info_path": str(root.resolve() / "server.json"),
        "token_path": str(root.resolve() / "token"),
        "ui_open_requested": False,
        "ui_opened": False,
        "ui_url": "http://127.0.0.1:8765/ui/",
    }
    assert "sign" not in " ".join(payload).casefold()
    assert "private" not in " ".join(payload).casefold()


def test_signed_startup_passes_operator_key_without_disclosing_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fakes(monkeypatch)
    root = tmp_path / "app-server"
    private_key = tmp_path / "operator-secret-evidence-key.pem"

    assert (
        app_server_cli.main(
            [
                "--root",
                str(root),
                "--port",
                "0",
                "--evidence-signing-private-key",
                str(private_key),
            ]
        )
        == 0
    )

    server = _FakeServer.instances[-1]
    assert server.evidence_signing_private_key == private_key
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert str(private_key) not in output
    assert set(payload) == {
        "schema_version",
        "base_url",
        "ui_url",
        "token_path",
        "server_info_path",
        "ui_open_requested",
        "ui_opened",
        "pid",
    }


def test_requested_missing_signing_key_fails_startup_before_server_serves(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "app-server"
    missing = tmp_path / "missing-private-key.pem"

    with pytest.raises(SystemExit) as exc_info:
        app_server_cli.main(
            [
                "--root",
                str(root),
                "--port",
                "0",
                "--evidence-signing-private-key",
                str(missing),
            ]
        )
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "evidence source is unavailable" in captured.err
    assert str(missing.resolve()) in captured.err
