"""Local single-user Harness X App Server foundation."""

from .http_server import LocalAppHTTPServer
from .protocol import (
    AppEvent,
    AppEventKind,
    AppServerError,
    AppServerHealth,
    AppSessionSnapshot,
    AppSessionStatus,
    CodingSessionRequest,
)
from .service import AppServerService, HarnessCodingRunner
from .store import AppSessionStore
from .trace_projection import (
    TraceProjectionEvent,
    TraceProjectionPage,
    build_trace_projection_page,
    load_verified_trace_records,
)

__all__ = [
    "AppEvent",
    "AppEventKind",
    "AppServerError",
    "AppServerHealth",
    "AppServerService",
    "AppSessionSnapshot",
    "AppSessionStatus",
    "AppSessionStore",
    "CodingSessionRequest",
    "HarnessCodingRunner",
    "LocalAppHTTPServer",
    "TraceProjectionEvent",
    "TraceProjectionPage",
    "build_trace_projection_page",
    "load_verified_trace_records",
]
