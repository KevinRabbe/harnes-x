"""M26 browser/application feedback interfaces."""

from .application import ApplicationProcessManager
from .contracts import (
    ApplicationProcessState,
    ApplicationServerSpec,
    BrowserConsoleMessage,
    BrowserObservation,
    BrowserProviderInfo,
    BrowserScreenshot,
    BrowserSelector,
    BrowserSelectorKind,
    is_loopback_hostname,
)
from .fake import FakeBrowserProvider
from .playwright_provider import PlaywrightBrowserProvider
from .provider import BrowserProvider

__all__ = [
    "ApplicationProcessManager",
    "ApplicationProcessState",
    "ApplicationServerSpec",
    "BrowserConsoleMessage",
    "BrowserObservation",
    "BrowserProvider",
    "BrowserProviderInfo",
    "BrowserScreenshot",
    "BrowserSelector",
    "BrowserSelectorKind",
    "FakeBrowserProvider",
    "PlaywrightBrowserProvider",
    "is_loopback_hostname",
]
