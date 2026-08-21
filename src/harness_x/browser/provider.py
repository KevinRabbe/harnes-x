"""Replaceable browser provider boundary for M26."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import (
    BrowserObservation,
    BrowserProviderInfo,
    BrowserScreenshot,
    BrowserSelector,
)


@runtime_checkable
class BrowserProvider(Protocol):
    @property
    def info(self) -> BrowserProviderInfo: ...

    def open(self, path: str = "/") -> BrowserObservation: ...

    def snapshot(self) -> BrowserObservation: ...

    def click(self, selector: BrowserSelector) -> BrowserObservation: ...

    def fill(self, selector: BrowserSelector, value: str) -> BrowserObservation: ...

    def select(self, selector: BrowserSelector, value: str) -> BrowserObservation: ...

    def screenshot(
        self, relative_path: str, *, full_page: bool = True
    ) -> BrowserScreenshot: ...

    def close(self) -> None: ...
