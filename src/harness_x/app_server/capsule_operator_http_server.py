"""M55 operator transport for one server-atomic signed-manifest capsule."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from harness_x.evidence_signing import EvidenceSigningError

from .evidence_capsule import (
    CAPSULE_FILENAME,
    EvidenceCapsuleRenderError,
    RenderedEvidenceCapsule,
    render_signed_manifest_capsule,
)
from .evidence_manifest import (
    EvidenceManifestCorruptionError,
    EvidenceManifestNotTerminalError,
    build_terminal_evidence_manifest,
    render_terminal_evidence_manifest,
)
from .signed_evidence_operator_http_server import (
    LocalOperatorHTTPServer as M53LocalOperatorHTTPServer,
)


class LocalOperatorHTTPServer(M53LocalOperatorHTTPServer):
    """Layer one byte-correlated capsule response over frozen M53 signing transport."""

    def _handler_type(self):
        base_handler = super()._handler_type()
        service = self.service
        token = self.token
        signer = self.evidence_manifest_signer

        class Handler(base_handler):
            server_version = "HarnessXAppServer/55"

            def do_GET(self) -> None:  # noqa: N802
                if not self._valid_host():
                    super().do_GET()
                    return
                parsed = urlsplit(self.path)
                pieces = self._session_path(parsed.path)
                if pieces is not None and pieces[1] == "/evidence/signed-manifest-capsule":
                    if not self._authorized(token):
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                        return
                    if parsed.query:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_request",
                            "signed manifest capsule endpoint does not accept query parameters",
                        )
                        return
                    if signer is None:
                        self._error(
                            HTTPStatus.NOT_FOUND,
                            "evidence_signature_not_configured",
                        )
                        return
                    session_id, _ = pieces
                    try:
                        snapshot = service.session(session_id)
                        events = service.store.events(session_id)
                    except KeyError:
                        self._error(HTTPStatus.NOT_FOUND, "unknown_session")
                        return
                    except RuntimeError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "evidence_corruption",
                            str(exc)[:4000],
                        )
                        return
                    try:
                        manifest = build_terminal_evidence_manifest(
                            snapshot=snapshot,
                            events=events,
                        )
                        rendered_manifest = render_terminal_evidence_manifest(manifest)
                        rendered_signature = signer.render(
                            rendered_manifest.payload,
                            manifest_sha256=rendered_manifest.source_sha256,
                        )
                        rendered_capsule = render_signed_manifest_capsule(
                            rendered_manifest,
                            rendered_signature,
                        )
                    except EvidenceManifestNotTerminalError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "evidence_capsule_not_terminal",
                            str(exc)[:4000],
                        )
                        return
                    except (
                        EvidenceManifestCorruptionError,
                        EvidenceCapsuleRenderError,
                        EvidenceSigningError,
                    ) as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "evidence_corruption",
                            str(exc)[:4000],
                        )
                        return
                    self._evidence_capsule(rendered_capsule)
                    return

                super().do_GET()

            def _evidence_capsule(self, rendered: RenderedEvidenceCapsule) -> None:
                self.close_connection = True
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{CAPSULE_FILENAME}"',
                )
                self.send_header("Content-Length", str(rendered.source_bytes))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "X-Harness-X-Evidence-Capsule-SHA256",
                    rendered.source_sha256,
                )
                self.send_header(
                    "X-Harness-X-Evidence-Manifest-SHA256",
                    rendered.manifest_sha256,
                )
                self.send_header(
                    "X-Harness-X-Evidence-Signature-Key",
                    rendered.key_fingerprint,
                )
                self.send_header(
                    "X-Harness-X-Evidence-Signature-Algorithm",
                    rendered.algorithm,
                )
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(rendered.payload)

        return Handler
