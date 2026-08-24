from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit


def _readline_with_timeout(stream, *, timeout: float = 15.0) -> str:
    result: queue.Queue[str] = queue.Queue(maxsize=1)

    def read() -> None:
        result.put(stream.readline())

    threading.Thread(target=read, daemon=True).start()
    return result.get(timeout=timeout)


def test_desktop_host_emits_private_bootstrap_handshake_and_stops_on_stdin_eof(
    tmp_path: Path,
) -> None:
    root = tmp_path / "desktop-app-server"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "harness_x.app_server.cli",
            "--root",
            str(root),
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--desktop-host",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    try:
        line = _readline_with_timeout(process.stdout)
        assert line, process.stderr.read()
        payload = json.loads(line)

        assert payload["schema_version"] == "app-server-desktop-start-v1"
        assert set(payload) == {
            "schema_version",
            "base_url",
            "ui_url",
            "ui_bootstrap_url",
            "pid",
        }
        assert payload["pid"] == process.pid
        assert "token_path" not in payload
        assert "server_info_path" not in payload

        base = urlsplit(payload["base_url"])
        ui = urlsplit(payload["ui_url"])
        bootstrap = urlsplit(payload["ui_bootstrap_url"])
        assert base.scheme == "http"
        assert base.hostname == "127.0.0.1"
        assert base.port is not None and base.port > 0
        assert (ui.scheme, ui.hostname, ui.port) == (base.scheme, base.hostname, base.port)
        assert (bootstrap.scheme, bootstrap.hostname, bootstrap.port) == (
            base.scheme,
            base.hostname,
            base.port,
        )
        assert bootstrap.query
        assert bootstrap.path.startswith("/ui")

        process.stdin.close()
        process.wait(timeout=15)
        assert process.returncode == 0, process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_desktop_host_refuses_browser_open_mode() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness_x.app_server.cli",
            "--desktop-host",
            "--open-ui",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 2
    assert "--desktop-host and --open-ui are mutually exclusive" in completed.stderr
