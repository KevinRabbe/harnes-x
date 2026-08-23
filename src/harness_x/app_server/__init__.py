"""Local single-user Harness X App Server foundation and operator UI."""

from .evidence_manifest import (
    EvidenceManifestCorruptionError,
    EvidenceManifestNotTerminalError,
    TerminalEvidenceManifest,
    build_terminal_evidence_manifest,
    render_terminal_evidence_manifest,
)
from .http_server import LocalAppHTTPServer
from .lifecycle_export import (
    LifecycleExportCorruptionError,
    LifecycleExportNotTerminalError,
    LifecycleExportTooLargeError,
    LifecycleLedgerExport,
    build_lifecycle_ledger_export,
    render_lifecycle_ledger_export,
)
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
from .report_projection import (
    CodingReportProjection,
    ReportCorruptionError,
    ReportUnavailableError,
    build_coding_report_projection,
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
    "CodingReportProjection",
    "CodingSessionRequest",
    "EvidenceManifestCorruptionError",
    "EvidenceManifestNotTerminalError",
    "HarnessCodingRunner",
    "LifecycleExportCorruptionError",
    "LifecycleExportNotTerminalError",
    "LifecycleExportTooLargeError",
    "LifecycleLedgerExport",
    "LocalAppHTTPServer",
    "LocalOperatorHTTPServer",
    "ReportCorruptionError",
    "ReportUnavailableError",
    "TerminalEvidenceManifest",
    "TraceProjectionEvent",
    "TraceProjectionPage",
    "build_coding_report_projection",
    "build_lifecycle_ledger_export",
    "build_terminal_evidence_manifest",
    "build_trace_projection_page",
    "load_verified_trace_records",
    "render_lifecycle_ledger_export",
    "render_terminal_evidence_manifest",
]
