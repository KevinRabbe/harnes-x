"""Optional Playwright implementation of the M26 browser provider.

Playwright is imported lazily so the standard Harness X install and CI do not depend on
browser automation or downloaded browser binaries.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin, urlparse

from .contracts import (
    BrowserConsoleMessage,
    BrowserObservation,
    BrowserProviderInfo,
    BrowserScreenshot,
    BrowserSelector,
    BrowserSelectorKind,
    ensure_artifact_path,
    is_loopback_hostname,
)


class PlaywrightBrowserProvider:
    def __init__(
        self,
        base_url: str,
        artifact_root: str | Path,
        *,
        headless: bool = True,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        default_timeout_ms: float = 8000.0,
        max_snapshot_chars: int = 30000,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not is_loopback_hostname(
            parsed.hostname
        ):
            raise ValueError("Playwright browser base_url must be loopback http(s)")
        if parsed.username or parsed.password:
            raise ValueError("Playwright browser base_url cannot contain credentials")
        self.base_url = base_url.rstrip("/")
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.default_timeout_ms = default_timeout_ms
        self.max_snapshot_chars = max_snapshot_chars
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._console: list[BrowserConsoleMessage] = []
        self._page_errors: list[str] = []

    @property
    def info(self) -> BrowserProviderInfo:
        return BrowserProviderInfo(
            name="playwright-browser",
            version="playwright-browser-v1",
            engine="chromium",
            live_browser=True,
        )

    def _ensure_started(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "M26 live browser support requires the optional Harness X browser "
                "dependency and a Playwright Chromium installation"
            ) from exc
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(
                viewport={"width": self.viewport_width, "height": self.viewport_height},
                service_workers="block",
            )
            self._context.set_default_timeout(self.default_timeout_ms)
            self._context.route("**/*", self._route_http)
            self._context.route_web_socket("**/*", self._route_websocket)
            self._page = self._context.new_page()
            self._page.on("console", self._on_console)
            self._page.on("pageerror", self._on_page_error)
        except Exception:
            self.close()
            raise

    @staticmethod
    def _network_url_allowed(url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme in {"data", "blob", "about"}:
            return True
        if parsed.scheme in {"http", "https", "ws", "wss"}:
            return is_loopback_hostname(parsed.hostname)
        return False

    def _route_http(self, route, request) -> None:
        if self._network_url_allowed(request.url):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    def _route_websocket(self, websocket_route) -> None:
        if self._network_url_allowed(websocket_route.url):
            websocket_route.connect_to_server()
        else:
            websocket_route.close(code=1008, reason="Harness X blocks non-loopback WebSockets")

    def _on_console(self, message) -> None:
        self._console.append(
            BrowserConsoleMessage(level=str(message.type), text=str(message.text)[:4000])
        )
        del self._console[:-100]

    def _on_page_error(self, error) -> None:
        text = str(getattr(error, "message", error))[:4000]
        self._page_errors.append(text)
        del self._page_errors[:-100]

    @staticmethod
    def _validate_path(path: str) -> str:
        parsed = urlparse(path)
        if parsed.scheme or parsed.netloc or path.startswith("//"):
            raise ValueError("browser navigation must stay on the declared app origin")
        if not path.startswith("/"):
            path = "/" + path
        return path

    def _locator(self, selector: BrowserSelector):
        page = self._page
        if page is None:
            raise RuntimeError("browser provider is not started")
        if selector.kind == BrowserSelectorKind.ROLE:
            kwargs = {"exact": selector.exact}
            if selector.name is not None:
                kwargs["name"] = selector.name
            return page.get_by_role(selector.role, **kwargs)
        if selector.kind == BrowserSelectorKind.LABEL:
            return page.get_by_label(selector.value, exact=selector.exact)
        if selector.kind == BrowserSelectorKind.TEXT:
            return page.get_by_text(selector.value, exact=selector.exact)
        if selector.kind == BrowserSelectorKind.TEST_ID:
            return page.get_by_test_id(selector.value)
        assert selector.kind == BrowserSelectorKind.CSS
        return page.locator(selector.value)

    def _observation(self) -> BrowserObservation:
        page = self._page
        if page is None:
            raise RuntimeError("browser provider is not started")
        snapshot = page.aria_snapshot(
            mode="ai",
            boxes=True,
            depth=10,
            timeout=self.default_timeout_ms,
        )
        truncated = len(snapshot) > self.max_snapshot_chars
        return BrowserObservation(
            url=page.url,
            title=page.title()[:1000],
            aria_snapshot=snapshot[: self.max_snapshot_chars],
            aria_truncated=truncated,
            console_messages=tuple(self._console),
            page_errors=tuple(self._page_errors),
        )

    def open(self, path: str = "/") -> BrowserObservation:
        self._ensure_started()
        page = self._page
        assert page is not None
        normalized = self._validate_path(path)
        target = urljoin(self.base_url + "/", normalized.lstrip("/"))
        page.goto(target, wait_until="domcontentloaded", timeout=self.default_timeout_ms)
        return self._observation()

    def snapshot(self) -> BrowserObservation:
        self._ensure_started()
        return self._observation()

    def click(self, selector: BrowserSelector) -> BrowserObservation:
        self._ensure_started()
        self._locator(selector).click(timeout=self.default_timeout_ms)
        return self._observation()

    def fill(self, selector: BrowserSelector, value: str) -> BrowserObservation:
        self._ensure_started()
        self._locator(selector).fill(value, timeout=self.default_timeout_ms)
        return self._observation()

    def select(self, selector: BrowserSelector, value: str) -> BrowserObservation:
        self._ensure_started()
        self._locator(selector).select_option(value=value, timeout=self.default_timeout_ms)
        return self._observation()

    def screenshot(
        self, relative_path: str, *, full_page: bool = True
    ) -> BrowserScreenshot:
        self._ensure_started()
        page = self._page
        assert page is not None
        target = ensure_artifact_path(self.artifact_root, relative_path)
        if target.suffix.casefold() != ".png":
            raise ValueError("browser screenshots must use a .png artifact path")
        target.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target), full_page=full_page)
        viewport = page.viewport_size or {}
        return BrowserScreenshot(
            path=str(target),
            full_page=full_page,
            width=viewport.get("width"),
            height=viewport.get("height"),
        )

    def close(self) -> None:
        for name in ("_page", "_context", "_browser"):
            value = getattr(self, name)
            if value is not None:
                try:
                    value.close()
                except Exception:
                    pass
                setattr(self, name, None)
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
