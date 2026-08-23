from __future__ import annotations

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
    CodingSessionRequest,
    LocalOperatorHTTPServer,
)
from harness_x.evidence_signing import (
    generate_evidence_keypair,
    sign_evidence_manifest,
    verify_portable_evidence_with_signature,
)


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="sign exact terminal manifest bytes",
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


def test_signature_route_requires_auth_before_disclosing_not_configured(tmp_path: Path) -> None:
    service = AppServerService(tmp_path / "service")
    server = LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        path = "/v1/sessions/app_" + "0" * 32 + "/evidence/signature"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path, authorized=False), timeout=3.0)
        assert exc_info.value.code == 401
        unauthorized = json.loads(exc_info.value.read().decode("utf-8"))
        assert unauthorized["error"] == "unauthorized"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 404
        configured = json.loads(exc_info.value.read().decode("utf-8"))
        assert configured["error"] == "evidence_signature_not_configured"
    finally:
        server.close()
        service.close()


def test_signature_response_matches_offline_m52_signing_and_verification(tmp_path: Path) -> None:
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

        with urlopen(_get(server, manifest_path, authorized=True), timeout=3.0) as response:
            manifest_body = response.read()
            manifest_sha256 = response.headers["X-Harness-X-Evidence-Manifest-SHA256"]
        assert manifest_sha256 == hashlib.sha256(manifest_body).hexdigest()

        with urlopen(_get(server, signature_path, authorized=True), timeout=3.0) as response:
            signature_body = response.read()
            assert response.status == 200
            assert response.headers.get("Content-Type") == "application/json; charset=utf-8"
            assert response.headers.get("Content-Disposition") == (
                'attachment; filename="session-evidence-manifest.sig.json"'
            )
            assert response.headers.get("Content-Length") == str(len(signature_body))
            assert response.headers.get("Cache-Control") == "no-store"
            assert response.headers.get("X-Content-Type-Options") == "nosniff"
            assert response.headers.get("Referrer-Policy") == "no-referrer"
            assert response.headers.get("X-Harness-X-Evidence-Manifest-SHA256") == manifest_sha256
            assert response.headers.get("X-Harness-X-Evidence-Signature-Key") == (
                generated.key_fingerprint
            )
            assert response.headers.get("X-Harness-X-Evidence-Signature-Algorithm") == "ed25519"

        envelope = json.loads(signature_body.decode("utf-8"))
        assert envelope["schema_version"] == "app-evidence-signature-v1"
        assert envelope["algorithm"] == "ed25519"
        assert envelope["key_fingerprint"] == generated.key_fingerprint
        assert envelope["manifest_sha256"] == manifest_sha256

        with urlopen(_get(server, signature_path, authorized=True), timeout=3.0) as response:
            assert response.read() == signature_body

        downloaded_manifest = tmp_path / "session-evidence-manifest.json"
        downloaded_manifest.write_bytes(manifest_body)
        offline_signature = tmp_path / "offline.sig.json"
        sign_evidence_manifest(
            downloaded_manifest,
            private_key_path=private_key,
            output_path=offline_signature,
        )
        assert offline_signature.read_bytes() == signature_body

        downloaded_signature = tmp_path / "server.sig.json"
        downloaded_signature.write_bytes(signature_body)
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


def test_signature_route_rejects_running_query_extra_path_and_unknown_session(tmp_path: Path) -> None:
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
        path = f"/v1/sessions/{running.session_id}/evidence/signature"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 409
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "evidence_signature_not_terminal"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path + "?algorithm=ed25519", authorized=True), timeout=3.0)
        assert exc_info.value.code == 400
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "invalid_request"
        assert payload["detail"] == "evidence signature endpoint does not accept query parameters"

        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path + "/public-key", authorized=True), timeout=3.0)
        assert exc_info.value.code == 404

        unknown = "/v1/sessions/app_" + "f" * 32 + "/evidence/signature"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, unknown, authorized=True), timeout=3.0)
        assert exc_info.value.code == 404
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "unknown_session"
    finally:
        server.close()
        service.close()


def test_signature_route_maps_durable_event_corruption_to_structured_409(tmp_path: Path) -> None:
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

        path = f"/v1/sessions/{snapshot.session_id}/evidence/signature"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(_get(server, path, authorized=True), timeout=3.0)
        assert exc_info.value.code == 409
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "evidence_corruption"
        assert "hash mismatch" in payload["detail"]
    finally:
        server.close()
        service.close()
