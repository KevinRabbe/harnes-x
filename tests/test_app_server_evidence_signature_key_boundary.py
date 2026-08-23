from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import harness_x.app_server.signed_evidence_operator_http_server as signed_server
from harness_x.app_server.service import AppServerService
from harness_x.evidence_signing import EvidenceSigningError, MAX_EVIDENCE_KEY_BYTES
from harness_x.evidence_verification import PortableEvidenceVerificationError


def _construct_with_key(service: AppServerService, root: Path, key: Path):
    return signed_server.LocalOperatorHTTPServer(
        service,
        root,
        port=0,
        evidence_signing_private_key=key,
    )


def test_unsigned_server_construction_does_not_invoke_signer_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(cls, path):
        pytest.fail(f"unsigned server unexpectedly loaded signing key: {path}")

    monkeypatch.setattr(
        signed_server.EvidenceManifestSigner,
        "from_private_key_path",
        classmethod(fail_if_called),
    )
    service = AppServerService(tmp_path / "service")
    server = signed_server.LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    try:
        assert server.evidence_manifest_signer is None
    finally:
        server.httpd.server_close()
        service.close()


def test_malformed_private_key_fails_before_transport_construction(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.pem"
    malformed.write_text("not a private key\n", encoding="utf-8")
    service = AppServerService(tmp_path / "service")
    try:
        with pytest.raises(EvidenceSigningError, match="valid unencrypted PEM"):
            _construct_with_key(service, tmp_path / "transport", malformed)
        assert not (tmp_path / "transport").exists()
    finally:
        service.close()


def test_non_ed25519_private_key_fails_before_transport_construction(tmp_path: Path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    non_ed = tmp_path / "rsa-private.pem"
    non_ed.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    service = AppServerService(tmp_path / "service")
    try:
        with pytest.raises(EvidenceSigningError, match="not an Ed25519 private key"):
            _construct_with_key(service, tmp_path / "transport", non_ed)
        assert not (tmp_path / "transport").exists()
    finally:
        service.close()


def test_oversized_private_key_fails_before_transport_construction(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.pem"
    oversized.write_bytes(b"x" * (MAX_EVIDENCE_KEY_BYTES + 1))
    service = AppServerService(tmp_path / "service")
    try:
        with pytest.raises(PortableEvidenceVerificationError, match="exceeds maximum size"):
            _construct_with_key(service, tmp_path / "transport", oversized)
        assert not (tmp_path / "transport").exists()
    finally:
        service.close()


def test_symlink_private_key_is_rejected_before_transport_construction(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("symlink boundary is POSIX-qualified in CI")
    actual = tmp_path / "actual.pem"
    actual.write_text("not relevant\n", encoding="utf-8")
    linked = tmp_path / "linked.pem"
    linked.symlink_to(actual)
    service = AppServerService(tmp_path / "service")
    try:
        with pytest.raises(PortableEvidenceVerificationError, match="symbolic link"):
            _construct_with_key(service, tmp_path / "transport", linked)
        assert not (tmp_path / "transport").exists()
    finally:
        service.close()
