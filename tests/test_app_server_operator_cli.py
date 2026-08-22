from __future__ import annotations

import subprocess


def test_installed_app_server_help_exposes_operator_ui_surface() -> None:
    completed = subprocess.run(
        ["harness-x-app-server", "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    normalized = " ".join(completed.stdout.split())
    assert "authenticated operator UI" in normalized
    assert "--host {127.0.0.1}" in normalized
    assert "--open-ui" in normalized
    assert "persistent bearer is never placed in the URL" in normalized
