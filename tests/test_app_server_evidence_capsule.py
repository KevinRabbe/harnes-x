from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from harness_x.app_server import (
    AppEventKind,
    AppServerService,
    AppSessionStatus,
    CAPSULE_FILENAME,
    CAPSULE_SCHEMA_VERSION,
    CodingSessionRequest,
    EvidenceCapsuleRenderError,
    LocalOperatorHTTPServer,
    render_signed_manifest_capsule,
)
from harness_x.app_server.evidence_manifest import RenderedEvidenceManifest
from harness_x.app_server.evidence_signature import RenderedEvidenceSignature
from harness_x.evidence_signing import (
    generate_evidence_keypair,
    verify_portable_evidence_with_signature,
)


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="export one atomic signed-manifest capsule",
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def _running_session(service: AppServerService, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    output = tmp_path / "output"
    output.mkdir(parents=True, exist_ok=True)
    snapshot = service.store.create_session(_request(workspace), output_root=output)
    return service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.RUNNING,
        kind=AppEventKind.SESSION_STARTED,
    )


def _terminal_session(service: AppServerService, tmp_path: Path):
    snapshot = _running_session(service, tmp_path)
    return service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.SUCCEEDED,
        kind=AppEventKind.SESSION_COMPLETED,
    )


def _keypair(tmp_path: Path):
    private_key = tmp_path / "evidence.private.pem"
    public_key = tmp_path / "evidence.public.pem"
    generated = generate_evidence_keypair(
        private_key_path=private_key,
        public_key_path=public_key,
    )
    return generated, private_key, public_key


def _get(server: LocalOperatorHTTPServer, path: str, *, authorized: bool) -> Request:
    headers = {"Accept": "application/json"}
    if authorized:
        headers["Authorization"] = f"Bearer {server.token}"
    return Request(server.base_url + path, headers=headers, method="GET")


def _decode_base64url(text: str) -> bytes:
    assert "=" not in text
    assert all(character.isalnum() or character in "-_" for character in text)
    padding = "=" * ((4 - len(text) % 4) % 4)
    decoded = base64.urlsafe_b64decode(text + padding)
    assert base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") == text
    return decoded


def test_capsule_renderer_preserves_exact_manifest_and_signature_bytes() -> None:
    manifest_payload = b'{"manifest":true}\n'
    manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    signature_payload = b'{"signature":"opaque"}\n'
    manifest = RenderedEvidenceManifest(
        payload=manifest_payload,
        source_bytes=len(manifest_payload),
        source_sha256=manifest_sha256,
    )
    signature = RenderedEvidenceSignature(
        payload=signature_payload,
        source_bytes=len(signature_payload),
        manifest_sha256=manifest_sha256,
        key_fingerprint="sha256:" + "a" * 64,
    )

    rendered = render_signed_manifest_capsule(manifest, signature)
    assert rendered.source_bytes == len(rendered.payload)
    assert rendered.source_sha256 == hashlib.sha256(rendered.payload).hexdigest()
    assert rendered.manifest_sha256 == manifest_sha256
    assert rendered.payload.endswith(b"\n")

    capsule = json.loads(rendered.payload.decode("utf-8"))
    assert list(capsule) == [
        "algorithm",
        "key_fingerprint",
        "manifest_payload",
        "manifest_sha256",
        "schema_version",
        "signature_payload",
    ]
    assert capsule["schema_version"] == CAPSULE_SCHEMA_VERSION
    assert capsule["algorithm"] == "ed25519"
    assert _decode_base64url(capsule["manifest_payload"]) == manifest_payload
    assert _decode_base64url(capsule["signature_payload"]) == signature_payload

    mismatched = RenderedEvidenceSignature(
        payload=signature_payload,
        source_bytes=len(signature_payload),
        manifest_sha256="f" * 64,
        key_fingerprint=signature.key_fingerprint,
    )
    with pytest.raises(EvidenceCapsuleRenderError, match="different manifest bytes"):
        render_signed_manifest_capsule(manifest, mismatched)


def test_capsule_route_requires_auth_before_disclosing_signer_configuration(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        path = "/v1/sessions/app_" + "0" * 32 + "/evidence/signed-manifest-capsule"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path, authorized=False), timeout=3.0)
        assert exc_info.value.code == 401
        assert json.loads(exc_info.value.read().decode("utf-8"))["error"] == "unauthorized"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 404
        assert json.loads(exc_info.value.read().decode("utf-8"))["error"] == (
            "evidence_signature_not_configured"
        )
    finally:
        server.close()
        service.close()


def test_capsule_response_contains_exact_m43_manifest_and_m52_signature_bytes(tmp_path: Path) -> None:
    generated, private_key, public_key = _keypair(tmp_path)
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(
        service,
        tmp_path / "transport",
        port=0,
        evidence_signing_private_key=private_key,
    )
    server.start_in_thread()
    try:
        snapshot = _terminal_session(service, tmp_path / "session")
        manifest_path = f"/v1/sessions/{snapshot.session_id}/evidence/manifest"
        signature_path = f"/v1/sessions/{snapshot.session_id}/evidence/signature"
        capsule_path = (
            f"/v1/sessions/{snapshot.session_id}/evidence/signed-manifest-capsule"
        )

        with urlopen(_get(server, manifest_path, authorized=True), timeout=3.0) as response:
            manifest_body = response.read()
            manifest_sha256 = response.headers["X-Harness-X-Evidence-Manifest-SHA256"]
        with urlopen(_get(server, signature_path, authorized=True), timeout=3.0) as response:
            signature_body = response.read()

        with urlopen(_get(server, capsule_path, authorized=True), timeout=3.0) as response:
            capsule_body = response.read()
            assert response.status == 200
            assert response.headers.get("Content-Type") == "application/json; charset=utf-8"
            assert response.headers.get("Content-Disposition") == (
                f'attachment; filename="{CAPSULE_FILENAME}"'
            )
            assert response.headers.get("Content-Length") == str(len(capsule_body))
            assert response.headers.get("Cache-Control") == "no-store"
            assert response.headers.get("X-Content-Type-Options") == "nosniff"
            assert response.headers.get("Referrer-Policy") == "no-referrer"
            assert response.headers.get("X-Harness-X-Evidence-Capsule-SHA256") == (
                hashlib.sha256(capsule_body).hexdigest()
            )
            assert response.headers.get("X-Harness-X-Evidence-Manifest-SHA256") == (
                manifest_sha256
            )
            assert response.headers.get("X-Harness-X-Evidence-Signature-Key") == (
                generated.key_fingerprint
            )
            assert response.headers.get("X-Harness-X-Evidence-Signature-Algorithm") == (
                "ed25519"
            )

        capsule = json.loads(capsule_body.decode("utf-8"))
        assert capsule["schema_version"] == CAPSULE_SCHEMA_VERSION
        assert capsule["algorithm"] == "ed25519"
        assert capsule["key_fingerprint"] == generated.key_fingerprint
        assert capsule["manifest_sha256"] == manifest_sha256
        assert _decode_base64url(capsule["manifest_payload"]) == manifest_body
        assert _decode_base64url(capsule["signature_payload"]) == signature_body

        with urlopen(_get(server, capsule_path, authorized=True), timeout=3.0) as response:
            assert response.read() == capsule_body

        downloaded_manifest = tmp_path / "manifest.json"
        downloaded_signature = tmp_path / "signature.json"
        downloaded_manifest.write_bytes(_decode_base64url(capsule["manifest_payload"]))
        downloaded_signature.write_bytes(_decode_base64url(capsule["signature_payload"]))
        verified = verify_portable_evidence_with_signature(
            downloaded_manifest,
            signature_path=downloaded_signature,
            public_key_path=public_key,
        )
        assert verified.signature_status == "verified"
        assert verified.key_fingerprint == generated.key_fingerprint
    finally:
        server.close()
        service.close()


def test_capsule_route_rejects_running_query_extra_path_and_unknown_session(tmp_path: Path) -> None:
    _generated, private_key, _public_key = _keypair(tmp_path)
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(
        service,
        tmp_path / "transport",
        port=0,
        evidence_signing_private_key=private_key,
    )
    server.start_in_thread()
    try:
        running = _running_session(service, tmp_path / "running")
        path = f"/v1/sessions/{running.session_id}/evidence/signed-manifest-capsule"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 409
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "evidence_capsule_not_terminal"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path + "?format=zip", authorized=True), timeout=3.0)
        assert exc_info.value.code == 400
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "invalid_request"
        assert payload["detail"] == (
            "signed manifest capsule endpoint does not accept query parameters"
        )

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path + "/public-key", authorized=True), timeout=3.0)
        assert exc_info.value.code == 404

        unknown = (
            "/v1/sessions/app_" + "f" * 32 + "/evidence/signed-manifest-capsule"
        )
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, unknown, authorized=True), timeout=3.0)
        assert exc_info.value.code == 404
        assert json.loads(exc_info.value.read().decode("utf-8"))["error"] == "unknown_session"
    finally:
        server.close()
        service.close()


def test_capsule_route_maps_durable_event_corruption_to_structured_409(tmp_path: Path) -> None:
    _generated, private_key, _public_key = _keypair(tmp_path)
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(
        service,
        tmp_path / "transport",
        port=0,
        evidence_signing_private_key=private_key,
    )
    server.start_in_thread()
    try:
        snapshot = _terminal_session(service, tmp_path / "session")
        events_path = service.store.root / snapshot.session_id / "events.jsonl"
        rows = events_path.read_text(encoding="utf-8").splitlines()
        raw = json.loads(rows[-1])
        raw["event_hash"] = "0" * 64
        rows[-1] = json.dumps(raw, separators=(",", ":"))
        events_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

        path = f"/v1/sessions/{snapshot.session_id}/evidence/signed-manifest-capsule"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 409
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "evidence_corruption"
        assert "hash mismatch" in payload["detail"]
    finally:
        server.close()
        service.close()
