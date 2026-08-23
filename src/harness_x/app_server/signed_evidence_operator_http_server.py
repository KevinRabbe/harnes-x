"""M53 operator transport for detached App Server evidence-manifest signatures."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit

from .evidence_manifest import (
    EvidenceManifestCorruptionError,
    EvidenceManifestNotTerminalError,
    build_terminal_evidence_manifest,
    render_terminal_evidence_manifest,
)
from .evidence_signature import EvidenceManifestSigner, RenderedEvidenceSignature
from .family_operator_http_server import (
    LocalOperatorHTTPServer as M51LocalOperatorHTTPServer,
)


class LocalOperatorHTTPServer(M51LocalOperatorHTTPServer):
    """Layer optional deterministic manifest signing over frozen M51 transport."""

    def __init__(
        self,
        *args,
        evidence_signing_private_key: str | Path | None = None,
        **kwargs,
    ) -> None:
        self.evidence_manifest_signer = (
            None
            if evidence_signing_private_key is None
            else EvidenceManifestSigner.from_private_key_path(evidence_signing_private_key)
        )
        super().__init__(*args, **kwargs)

    def _handler_type(self):
        base_handler = super()._handler_type()
        service = self.service
        token = self.token
        signer = self.evidence_manifest_signer

        class Handler(base_handler):
            server_version = "HarnessXAppServer/53"

            def do_GET(self) -> None:  # noqa: N802
                if not self._valid_host():
                    super().do_GET()
                    return
                parsed = urlsplit(self.path)
                pieces = self._session_path(parsed.path)
                if pieces is not None and pieces[1] == "/evidence/signature":
                    if not self._authorized(token):
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                        return
                    if parsed.query:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_request",
                            "evidence signature endpoint does not accept query parameters",
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
                    except EvidenceManifestNotTerminalError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "evidence_signature_not_terminal",
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
                    rendered_signature = signer.render(
                        rendered_manifest.payload,
                        manifest_sha256=rendered_manifest.source_sha256,
                    )
                    self._evidence_signature(rendered_signature)
                    return

                super().do_GET()

            def _evidence_signature(self, rendered: RenderedEvidenceSignature) -> None:
                self.close_connection = True
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="session-evidence-manifest.sig.json"',
                )
                self.send_header("Content-Length", str(rendered.source_bytes))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
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
