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
from .session import ApplicationBrowserSession, BrowserProviderFactory

__all__ = [
    "ApplicationBrowserSession",
    "ApplicationProcessManager",
    "ApplicationProcessState",
    "ApplicationServerSpec",
    "BrowserConsoleMessage",
    "BrowserObservation",
    "BrowserProvider",
    "BrowserProviderFactory",
    "BrowserProviderInfo",
    "BrowserScreenshot",
    "BrowserSelector",
    "BrowserSelectorKind",
    "FakeBrowserProvider",
    "PlaywrightBrowserProvider",
    "is_loopback_hostname",
]
