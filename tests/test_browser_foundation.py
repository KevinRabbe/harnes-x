from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from harness_x.browser import (
    ApplicationProcessManager,
    ApplicationServerSpec,
    BrowserConsoleMessage,
    BrowserSelector,
    BrowserSelectorKind,
    FakeBrowserProvider,
    PlaywrightBrowserProvider,
)
from harness_x.tools.browser import browser_tool_definitions


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_browser_selector_contract_rejects_ambiguous_shapes() -> None:
    role = BrowserSelector(kind="role", role="button", name="Save")
    assert role.kind == BrowserSelectorKind.ROLE
    assert role.name == "Save"

    label = BrowserSelector(kind="label", value="Email")
    assert label.value == "Email"

    with pytest.raises(ValueError, match="requires role"):
        BrowserSelector(kind="role", name="Save")
    with pytest.raises(ValueError, match="requires value"):
        BrowserSelector(kind="text")
    with pytest.raises(ValueError, match="cannot set role/name"):
        BrowserSelector(kind="css", value="#save", name="Save")


def test_application_server_spec_is_loopback_only() -> None:
    spec = ApplicationServerSpec(
        argv=("python", "-m", "http.server", "8000"),
        base_url="http://127.0.0.1:8000",
    )
    assert spec.base_url == "http://127.0.0.1:8000"

    with pytest.raises(ValueError, match="loopback-only"):
        ApplicationServerSpec(
            argv=("python", "-m", "http.server", "8000"),
            base_url="https://example.com",
        )
    with pytest.raises(ValueError, match="absolute path"):
        ApplicationServerSpec(
            argv=("python", "-m", "http.server", "8000"),
            base_url="http://localhost:8000",
            health_path="health",
        )


def test_fake_browser_exposes_bounded_semantic_actions_and_artifacts(tmp_path: Path) -> None:
    provider = FakeBrowserProvider(
        "http://127.0.0.1:8000",
        tmp_path,
        pages={"/": '- heading "Dashboard" [level=1]\n- button "Save"'},
        console_messages=[BrowserConsoleMessage(level="warning", text="example")],
    )
    observation = provider.open("/")
    assert "Dashboard" in observation.aria_snapshot
    assert observation.console_messages[0].level == "warning"

    selector = BrowserSelector(kind="role", role="button", name="Save")
    provider.click(selector)
    provider.fill(BrowserSelector(kind="label", value="Name"), "Harness X")
    provider.select(BrowserSelector(kind="label", value="Mode"), "safe")
    screenshot = provider.screenshot("shots/page.png")

    assert Path(screenshot.path).read_bytes() == b"fake-browser-screenshot\n"
    assert [row[0] for row in provider.actions] == [
        "open",
        "click",
        "fill",
        "select",
        "screenshot",
    ]
    with pytest.raises(ValueError, match="escapes"):
        provider.screenshot("../escape.png")
    with pytest.raises(ValueError, match="same-origin"):
        provider.open("https://example.com")


def test_browser_tool_contract_contains_seven_bounded_capabilities(tmp_path: Path) -> None:
    provider = FakeBrowserProvider("http://localhost:3000", tmp_path)
    definitions = browser_tool_definitions(provider)
    by_name = {item.spec.name: item for item in definitions}
    assert set(by_name) == {
        "browser_open",
        "browser_snapshot",
        "browser_click",
        "browser_fill",
        "browser_select",
        "browser_screenshot",
        "browser_console",
    }
    assert by_name["browser_snapshot"].spec.permissions == ("workspace.read",)
    assert by_name["browser_click"].spec.permissions == ("workspace.execute",)
    assert by_name["browser_click"].spec.side_effect_level.value == "persistent"
    assert by_name["browser_screenshot"].spec.side_effect_level.value == "persistent"


def test_application_process_runs_inside_workspace_and_cleans_up(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("index.html").write_text("<h1>Harness X</h1>\n", encoding="utf-8")
    port = _free_loopback_port()
    spec = ApplicationServerSpec(
        argv=(
            sys.executable,
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
        ),
        base_url=f"http://127.0.0.1:{port}",
        startup_timeout_seconds=10.0,
        shutdown_timeout_seconds=3.0,
    )
    manager = ApplicationProcessManager(workspace, tmp_path / "artifacts", spec)
    state = manager.start()
    try:
        assert state.running is True
        assert state.pid is not None
        assert state.base_url == spec.base_url
    finally:
        stopped = manager.stop()
    assert stopped.running is False
    assert Path(stopped.stdout_path).exists()
    assert Path(stopped.stderr_path).exists()


def test_application_process_rejects_escape_and_disallowed_command(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spec = ApplicationServerSpec(
        argv=("git", "status"),
        base_url="http://localhost:8000",
    )
    with pytest.raises(PermissionError, match="not allowed"):
        ApplicationProcessManager(workspace, tmp_path / "artifacts", spec)

    escape = ApplicationServerSpec(
        argv=("python", "-m", "http.server", "8000"),
        cwd="..",
        base_url="http://localhost:8000",
    )
    with pytest.raises(ValueError, match="escapes"):
        ApplicationProcessManager(workspace, tmp_path / "artifacts-2", escape)


def test_playwright_provider_is_importable_without_playwright_installed(tmp_path: Path) -> None:
    provider = PlaywrightBrowserProvider("http://localhost:3000", tmp_path)
    assert provider.info.live_browser is True
    assert provider.info.engine == "chromium"
    # Do not start a live browser in standard CI. Importability itself proves the optional
    # dependency is lazy and keeps the normal Harness X install browser-free.
    provider.close()
