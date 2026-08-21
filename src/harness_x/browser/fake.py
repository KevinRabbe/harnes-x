"""Dependency-free deterministic browser provider for CI and controller tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

from .contracts import (
    BrowserConsoleMessage,
    BrowserObservation,
    BrowserProviderInfo,
    BrowserScreenshot,
    BrowserSelector,
    ensure_artifact_path,
)


@dataclass
class FakeBrowserProvider:
    base_url: str
    artifact_root: Path
    pages: dict[str, str] = field(default_factory=lambda: {"/": '- heading "App" [level=1]'})
    console_messages: list[BrowserConsoleMessage] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    title: str = "Fake App"

    def __post_init__(self) -> None:
        self.artifact_root = Path(self.artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.current_path = "/"
        self.actions: list[tuple[str, object]] = []
        self.closed = False

    @property
    def info(self) -> BrowserProviderInfo:
        return BrowserProviderInfo(
            name="fake-browser",
            version="fake-browser-v1",
            engine="deterministic",
            live_browser=False,
        )

    def _ensure_open(self) -> None:
        if self.closed:
            raise RuntimeError("browser provider is closed")

    def _observation(self) -> BrowserObservation:
        self._ensure_open()
        snapshot = self.pages.get(self.current_path, self.pages.get("/", ""))
        return BrowserObservation(
            url=urljoin(self.base_url.rstrip("/") + "/", self.current_path.lstrip("/")),
            title=self.title,
            aria_snapshot=snapshot[:30000],
            aria_truncated=len(snapshot) > 30000,
            console_messages=tuple(self.console_messages[-100:]),
            page_errors=tuple(self.page_errors[-100:]),
        )

    def open(self, path: str = "/") -> BrowserObservation:
        self._ensure_open()
        if not path.startswith("/") or "://" in path or path.startswith("//"):
            raise ValueError("fake browser accepts only same-origin absolute paths")
        self.current_path = path
        self.actions.append(("open", path))
        return self._observation()

    def snapshot(self) -> BrowserObservation:
        self.actions.append(("snapshot", self.current_path))
        return self._observation()

    def click(self, selector: BrowserSelector) -> BrowserObservation:
        self._ensure_open()
        self.actions.append(("click", selector.model_dump(mode="json")))
        return self._observation()

    def fill(self, selector: BrowserSelector, value: str) -> BrowserObservation:
        self._ensure_open()
        self.actions.append(
            ("fill", {"selector": selector.model_dump(mode="json"), "value": value})
        )
        return self._observation()

    def select(self, selector: BrowserSelector, value: str) -> BrowserObservation:
        self._ensure_open()
        self.actions.append(
            ("select", {"selector": selector.model_dump(mode="json"), "value": value})
        )
        return self._observation()

    def screenshot(
        self, relative_path: str, *, full_page: bool = True
    ) -> BrowserScreenshot:
        self._ensure_open()
        target = ensure_artifact_path(self.artifact_root, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake-browser-screenshot\n")
        self.actions.append(("screenshot", relative_path))
        return BrowserScreenshot(path=str(target), full_page=full_page, width=1280, height=720)

    def close(self) -> None:
        self.closed = True
