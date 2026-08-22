"""Local single-user Harness X App Server foundation and operator UI."""

from .http_server import LocalAppHTTPServer
from .operator_http_server import LocalOperatorHTTPServer
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
    "LocalOperatorHTTPServer",
    "TraceProjectionEvent",
    "TraceProjectionPage",
    "build_trace_projection_page",
    "load_verified_trace_records",
]
