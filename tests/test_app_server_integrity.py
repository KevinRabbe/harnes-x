from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from harness_x.app_server.cli import build_parser
from harness_x.app_server.http_server import LocalAppHTTPServer
from harness_x.app_server.protocol import CodingSessionRequest
from harness_x.app_server.service import AppServerService
from harness_x.app_server.store import AppSessionStore


def _request(tmp_path: Path) -> CodingSessionRequest:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return CodingSessionRequest(
        workspace_root=workspace,
        task="repair",
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def test_snapshot_fingerprint_tamper_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "state"
    store = AppSessionStore(root)
    snapshot = store.create_session(_request(tmp_path), output_root=tmp_path / "run")
    path = root / snapshot.session_id / "snapshot.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["request"]["task"] = "tampered"
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="fingerprint mismatch"):
        AppSessionStore(root)


def test_http_server_rejects_non_loopback_bind(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service", runner=lambda _: None)
    try:
        with pytest.raises(ValueError, match="127.0.0.1"):
            LocalAppHTTPServer(service, tmp_path / "transport", host="0.0.0.0", port=0)
    finally:
        service.close()


def test_cli_parser_is_single_user_loopback_by_default(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--root", str(tmp_path / "server"), "--port", "0"])
    assert args.host == "127.0.0.1"
    assert args.port == 0


def test_installed_app_server_command_exposes_help() -> None:
    executable = shutil.which("harness-x-app-server")
    assert executable is not None
    completed = subprocess.run(
        [executable, "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout
    assert "127.0.0.1" in completed.stdout
