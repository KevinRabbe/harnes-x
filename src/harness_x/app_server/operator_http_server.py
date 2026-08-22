"""Local operator UI transport layered over the authenticated M34/M35 App Server."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from harness_x.core.errors import TraceCorruptionError

from .bootstrap import OneTimeBootstrapTickets
from .evidence_manifest import (
    EvidenceManifestCorruptionError,
    EvidenceManifestNotTerminalError,
    RenderedEvidenceManifest,
    build_terminal_evidence_manifest,
    render_terminal_evidence_manifest,
)
from .http_server import LocalAppHTTPServer
from .report_projection import (
    ReportCorruptionError,
    ReportUnavailableError,
    ValidatedCodingReport,
    build_coding_report_projection,
    read_validated_coding_report,
)
from .trace_export import (
    TraceExportNotTerminalError,
    TraceExportUnavailableError,
    ValidatedTraceExport,
    read_validated_trace_export,
)
from .ui_assets import load_ui_asset

_UI_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "connect-src 'self'; "
    "img-src 'none'; "
    "font-src 'none'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


class LocalOperatorHTTPServer(LocalAppHTTPServer):
    """Serve the operator UI without weakening inherited API authentication.

    Static UI assets contain no session data or credentials and remain public on the loopback
    origin. Stateful inherited APIs still use the M34 bearer-token boundary. M40 adds only a
    short-lived one-time local bootstrap exchange for explicitly launched operator browsers.
    M41 adds one authenticated raw-byte export for the canonical validated coding report.
    M42 adds one terminal-only exact-byte export for the attached verified causal trace.
    M43 adds one terminal-only generated evidence manifest correlating those identities with the
    validated App Server lifecycle head.
    """

    def __init__(self, *args, **kwargs) -> None:
        self.bootstrap_tickets = OneTimeBootstrapTickets()
        super().__init__(*args, **kwargs)
        self.ui_url = f"{self.base_url}/ui/"

    def issue_ui_bootstrap_url(self) -> str:
        """Return one disposable fragment URL without exposing the persistent bearer."""

        ticket = self.bootstrap_tickets.issue()
        return f"{self.ui_url}#bootstrap={ticket}"

    def _handler_type(self):
        base_handler = super()._handler_type()
        service = self.service
        token = self.token
        bootstrap_tickets = self.bootstrap_tickets

        class Handler(base_handler):
            server_version = "HarnessXAppServer/43"

            def do_GET(self) -> None:  # noqa: N802
                if not self._valid_host():
                    super().do_GET()
                    return
                parsed = urlsplit(self.path)
                if parsed.path in {"/", "/ui"}:
                    self._redirect_ui()
                    return
                asset = load_ui_asset(parsed.path)
                if asset is not None:
                    content_type, body = asset
                    self._ui_asset(content_type, body)
                    return

                pieces = self._session_path(parsed.path)
                if pieces is not None and pieces[1] == "/evidence/manifest":
                    if not self._authorized(token):
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                        return
                    if parsed.query:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_request",
                            "evidence manifest endpoint does not accept query parameters",
                        )
                        return
                    session_id, _ = pieces
                    try:
                        snapshot = service.session(session_id)
                        events = service.store.events(session_id)
                        manifest = build_terminal_evidence_manifest(
                            snapshot=snapshot,
                            events=events,
                        )
                        rendered_manifest = render_terminal_evidence_manifest(manifest)
                    except KeyError:
                        self._error(HTTPStatus.NOT_FOUND, "unknown_session")
                        return
                    except EvidenceManifestNotTerminalError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "evidence_manifest_not_terminal",
                            str(exc)[:4000],
                        )
                        return
                    except EvidenceManifestCorruptionError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "evidence_corruption",
                            str(exc)[:4000],
                        )
                        return
                    self._evidence_manifest(rendered_manifest)
                    return

                if pieces is not None and pieces[1] == "/trace/export":
                    if not self._authorized(token):
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                        return
                    if parsed.query:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_request",
                            "trace export endpoint does not accept query parameters",
                        )
                        return
                    session_id, _ = pieces
                    try:
                        snapshot = service.session(session_id)
                        events = service.store.events(session_id)
                        validated_trace = read_validated_trace_export(
                            snapshot=snapshot,
                            events=events,
                        )
                    except KeyError:
                        self._error(HTTPStatus.NOT_FOUND, "unknown_session")
                        return
                    except TraceExportNotTerminalError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "trace_export_not_terminal",
                            str(exc)[:4000],
                        )
                        return
                    except TraceExportUnavailableError as exc:
                        self._error(
                            HTTPStatus.NOT_FOUND,
                            "trace_export_not_available",
                            str(exc)[:4000],
                        )
                        return
                    except TraceCorruptionError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "trace_corruption",
                            str(exc)[:4000],
                        )
                        return
                    self._trace_export(validated_trace)
                    return

                if pieces is not None and pieces[1] in {"/report", "/report/export"}:
                    if not self._authorized(token):
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                        return
                    session_id, suffix = pieces
                    if parsed.query:
                        detail = (
                            "report endpoint does not accept query parameters"
                            if suffix == "/report"
                            else "report export endpoint does not accept query parameters"
                        )
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_request",
                            detail,
                        )
                        return
                    try:
                        snapshot = service.session(session_id)
                        events = service.store.events(session_id)
                        if suffix == "/report/export":
                            validated = read_validated_coding_report(
                                snapshot=snapshot,
                                events=events,
                            )
                            self._report_export(validated)
                            return
                        projection = build_coding_report_projection(
                            snapshot=snapshot,
                            events=events,
                        )
                    except KeyError:
                        self._error(HTTPStatus.NOT_FOUND, "unknown_session")
                        return
                    except ReportUnavailableError as exc:
                        self._error(
                            HTTPStatus.NOT_FOUND,
                            "report_not_available",
                            str(exc)[:4000],
                        )
                        return
                    except ReportCorruptionError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "report_corruption",
                            str(exc)[:4000],
                        )
                        return
                    self._json(HTTPStatus.OK, projection.model_dump(mode="json"))
                    return

                super().do_GET()

            def do_POST(self) -> None:  # noqa: N802
                if not self._valid_host():
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                    return
                parsed = urlsplit(self.path)
                if parsed.path == "/v1/operator/bootstrap":
                    if parsed.query:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_request",
                            "bootstrap endpoint does not accept query parameters",
                        )
                        return
                    if not self._same_origin_request():
                        self._error(HTTPStatus.FORBIDDEN, "invalid_origin")
                        return
                    try:
                        raw = self._read_json()
                    except ValueError as exc:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_bootstrap_request",
                            str(exc)[:4000],
                        )
                        return
                    if (
                        not isinstance(raw, dict)
                        or set(raw) != {"ticket"}
                        or not isinstance(raw.get("ticket"), str)
                    ):
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_bootstrap_request",
                            "bootstrap request must contain exactly one text ticket",
                        )
                        return
                    if not bootstrap_tickets.redeem(raw["ticket"]):
                        self._error(HTTPStatus.UNAUTHORIZED, "bootstrap_rejected")
                        return
                    self._json(
                        HTTPStatus.OK,
                        {
                            "schema_version": "app-operator-bootstrap-v1",
                            "access_token": token,
                        },
                    )
                    return

                super().do_POST()

            def _same_origin_request(self) -> bool:
                origin = self.headers.get("Origin", "")
                request_host = self.headers.get("Host", "")
                if not origin or not request_host:
                    return False
                parsed_origin = urlsplit(origin)
                return (
                    parsed_origin.scheme.casefold() == "http"
                    and parsed_origin.username is None
                    and parsed_origin.password is None
                    and parsed_origin.path in {"", "/"}
                    and not parsed_origin.query
                    and not parsed_origin.fragment
                    and parsed_origin.netloc.casefold() == request_host.casefold()
                )

            def _redirect_ui(self) -> None:
                self.close_connection = True
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/ui/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _ui_asset(self, content_type: str, body: bytes) -> None:
                self.close_connection = True
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", _UI_CSP)
                self.send_header("Cross-Origin-Opener-Policy", "same-origin")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def _report_export(self, validated: ValidatedCodingReport) -> None:
                body = validated.source.payload
                self.close_connection = True
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="coding-task-report.json"',
                )
                self.send_header("Content-Length", str(validated.source.source_bytes))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "X-Harness-X-Report-SHA256",
                    validated.source.source_sha256,
                )
                self.send_header(
                    "X-Harness-X-Report-Attestation",
                    validated.attestation_status,
                )
                self.send_header(
                    "X-Harness-X-Artifact-Event-Hash",
                    validated.artifact_event_hash,
                )
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def _trace_export(self, validated: ValidatedTraceExport) -> None:
                body = validated.source.payload
                self.close_connection = True
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="causal-trace.jsonl"',
                )
                self.send_header("Content-Length", str(validated.source.source_bytes))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Harness-X-Trace-ID", validated.trace_id)
                self.send_header(
                    "X-Harness-X-Trace-SHA256",
                    validated.source.source_sha256,
                )
                self.send_header(
                    "X-Harness-X-Trace-Records",
                    str(len(validated.records)),
                )
                self.send_header(
                    "X-Harness-X-Trace-Final-Event-Hash",
                    validated.final_event_hash or "none",
                )
                self.send_header(
                    "X-Harness-X-Trace-Attachment-Event-Hash",
                    validated.attachment_event_hash,
                )
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def _evidence_manifest(self, rendered: RenderedEvidenceManifest) -> None:
                self.close_connection = True
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="session-evidence-manifest.json"',
                )
                self.send_header("Content-Length", str(rendered.source_bytes))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "X-Harness-X-Evidence-Manifest-SHA256",
                    rendered.source_sha256,
                )
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(rendered.payload)

        return Handler
