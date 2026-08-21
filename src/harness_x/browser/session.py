"""Application-backed browser session used by model tools and browser verification."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .application import ApplicationProcessManager
from .contracts import (
    ApplicationProcessState,
    ApplicationServerSpec,
    BrowserObservation,
    BrowserProviderInfo,
    BrowserScreenshot,
    BrowserSelector,
)
from .provider import BrowserProvider

BrowserProviderFactory = Callable[[str, Path], BrowserProvider]


class ApplicationBrowserSession:
    """Lazy/restartable local app process plus one browser provider.

    Failed startup is not terminal. The manager instance is replaced after a failed start
    so a later browser action can retry after the coding model repairs the workspace.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        artifact_root: str | Path,
        application: ApplicationServerSpec,
        provider_factory: BrowserProviderFactory,
        *,
        allowed_executables: frozenset[str] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.application = application
        self.provider_factory = provider_factory
        self.allowed_executables = allowed_executables
        self._manager = self._new_manager()
        self._provider_generation = 0
        self._provider = self._new_provider("browser")
        self.latest_observation: BrowserObservation | None = None
        self.start_failures: list[str] = []
        self.closed = False

    def _new_manager(self) -> ApplicationProcessManager:
        kwargs = {}
        if self.allowed_executables is not None:
            kwargs["allowed_executables"] = self.allowed_executables
        return ApplicationProcessManager(
            self.workspace_root,
            self.artifact_root / "application",
            self.application,
            **kwargs,
        )

    def _new_provider(self, purpose: str) -> BrowserProvider:
        self._provider_generation += 1
        return self.provider_factory(
            self.application.base_url,
            self.artifact_root / f"{purpose}-{self._provider_generation:03d}",
        )

    @property
    def info(self) -> BrowserProviderInfo:
        return self._provider.info

    @property
    def application_state(self) -> ApplicationProcessState:
        return self._manager.state()

    def ensure_application(self) -> ApplicationProcessState:
        if self.closed:
            raise RuntimeError("application browser session is closed")
        state = self._manager.state()
        if state.running:
            return state
        if state.pid is not None:
            # A previously running process exited. Discard its completed Popen owner so
            # this attempt can immediately launch a fresh server rather than spending one
            # browser turn on an artificial "already started" error.
            try:
                self._manager.close()
            finally:
                self._manager = self._new_manager()
        try:
            return self._manager.start()
        except Exception as exc:
            self.start_failures.append(f"{type(exc).__name__}: {exc}"[:4000])
            try:
                self._manager.close()
            finally:
                self._manager = self._new_manager()
            raise

    def reset_browser_client(self, *, purpose: str = "verification") -> None:
        """Replace browser client state while preserving the software-owned app process."""

        if self.closed:
            raise RuntimeError("application browser session is closed")
        try:
            self._provider.close()
        finally:
            self._provider = self._new_provider(purpose)
            self.latest_observation = None

    def _observe(self, operation) -> BrowserObservation:
        self.ensure_application()
        observation = operation()
        self.latest_observation = observation
        return observation

    def open(self, path: str = "/") -> BrowserObservation:
        return self._observe(lambda: self._provider.open(path))

    def snapshot(self) -> BrowserObservation:
        return self._observe(self._provider.snapshot)

    def click(self, selector: BrowserSelector) -> BrowserObservation:
        return self._observe(lambda: self._provider.click(selector))

    def fill(self, selector: BrowserSelector, value: str) -> BrowserObservation:
        return self._observe(lambda: self._provider.fill(selector, value))

    def select(self, selector: BrowserSelector, value: str) -> BrowserObservation:
        return self._observe(lambda: self._provider.select(selector, value))

    def screenshot(
        self, relative_path: str, *, full_page: bool = True
    ) -> BrowserScreenshot:
        self.ensure_application()
        return self._provider.screenshot(relative_path, full_page=full_page)

    def context_projection(self) -> dict[str, object]:
        observation = self.latest_observation
        state = self._manager.state()
        return {
            "provider": self.info.model_dump(mode="json"),
            "application": {
                "base_url": self.application.base_url,
                "running": state.running,
                "pid": state.pid,
                "returncode": state.returncode,
                "stdout_path": state.stdout_path,
                "stderr_path": state.stderr_path,
                "startup_failures": list(self.start_failures[-3:]),
            },
            "latest_observation": (
                None
                if observation is None
                else {
                    "url": observation.url,
                    "title": observation.title,
                    "aria_snapshot": observation.aria_snapshot[:12000],
                    "aria_truncated": observation.aria_truncated,
                    "console_messages": [
                        item.model_dump(mode="json")
                        for item in observation.console_messages[-30:]
                    ],
                    "page_errors": list(observation.page_errors[-30:]),
                }
            ),
        }

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._provider.close()
        finally:
            self._manager.close()
