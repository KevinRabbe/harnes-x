from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

from harness_x.app_server.evidence_manifest import (
    CodingReportEvidenceUnavailable,
    LifecycleEvidence,
    TerminalEvidenceManifest,
    TraceEvidenceUnavailable,
)
from harness_x.evidence_signing import (
    EvidenceSigningError,
    generate_evidence_keypair,
    sign_evidence_manifest,
    verify_portable_evidence_with_signature,
)


def _manifest(path: Path, *, session_char: str = "a") -> TerminalEvidenceManifest:
    manifest = TerminalEvidenceManifest(
        session_id="app_" + session_char * 32,
        lifecycle=LifecycleEvidence(
            status="succeeded",
            snapshot_revision=2,
            snapshot_fingerprint="1" * 64,
            event_count=2,
            ledger_head_hash="2" * 64,
            ledger_head_kind="session_completed",
            created_at=datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 8, 23, 0, 1, tzinfo=timezone.utc),
        ),
        coding_report=CodingReportEvidenceUnavailable(),
        causal_trace=TraceEvidenceUnavailable(),
    )
    path.write_text(manifest.model_dump_json() + "\n", encoding="utf-8")
    return manifest


def _keypair(tmp_path: Path, stem: str = "evidence"):
    private_path = tmp_path / f"{stem}.private.pem"
    public_path = tmp_path / f"{stem}.public.pem"
    result = generate_evidence_keypair(
        private_key_path=private_path,
        public_key_path=public_path,
    )
    return result, private_path, public_path


def test_keygen_sign_verify_exact_manifest_bytes_and_deterministic_envelope(tmp_path: Path) -> None:
    manifest_path = tmp_path / "session-evidence-manifest.json"
    manifest = _manifest(manifest_path)
    keypair, private_path, public_path = _keypair(tmp_path)

    if os.name == "posix":
        assert stat.S_IMODE(private_path.stat().st_mode) == 0o600

    public_key = serialization.load_pem_public_key(public_path.read_bytes())
    assert isinstance(public_key, Ed25519PublicKey)
    raw_public = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    assert keypair.key_fingerprint == f"sha256:{hashlib.sha256(raw_public).hexdigest()}"

    first_path = tmp_path / "first.sig.json"
    second_path = tmp_path / "second.sig.json"
    first = sign_evidence_manifest(
        manifest_path,
        private_key_path=private_path,
        output_path=first_path,
    )
    second = sign_evidence_manifest(
        manifest_path,
        private_key_path=private_path,
        output_path=second_path,
    )
    assert first.manifest_sha256 == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert first.key_fingerprint == keypair.key_fingerprint == second.key_fingerprint
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first_path.read_bytes().endswith(b"\n")
    envelope = json.loads(first_path.read_text(encoding="utf-8"))
    assert list(envelope) == ["algorithm", "key_fingerprint", "manifest_sha256", "schema_version", "signature"]
    assert envelope["schema_version"] == "app-evidence-signature-v1"
    assert envelope["algorithm"] == "ed25519"
    assert len(envelope["signature"]) == 86

    verified = verify_portable_evidence_with_signature(
        manifest_path,
        signature_path=first_path,
        public_key_path=public_path,
    )
    assert verified.base.base.session_id == manifest.session_id
    assert verified.signature_status == "verified"
    assert verified.key_fingerprint == keypair.key_fingerprint
    assert f"signature=verified key={keypair.key_fingerprint}" in verified.summary()


def test_unsigned_verification_does_not_use_m52_manifest_reader(tmp_path: Path, monkeypatch) -> None:
    manifest_path = tmp_path / "manifest.json"
    _manifest(manifest_path)

    import harness_x.evidence_signing as signing

    def forbidden(*args, **kwargs):
        raise AssertionError("M52 bounded reader must not run without a signature")

    monkeypatch.setattr(signing, "_bounded_regular_file", forbidden)
    result = signing.verify_portable_evidence_with_signature(manifest_path)
    assert result.signature_status == "not_supplied"
    assert result.key_fingerprint is None


def test_signature_and_public_key_are_pairwise_required(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _manifest(manifest_path)
    with pytest.raises(EvidenceSigningError, match="must be supplied together"):
        verify_portable_evidence_with_signature(
            manifest_path,
            signature_path=tmp_path / "missing.sig.json",
        )
    with pytest.raises(EvidenceSigningError, match="must be supplied together"):
        verify_portable_evidence_with_signature(
            manifest_path,
            public_key_path=tmp_path / "missing.public.pem",
        )


def test_wrong_key_and_rewritten_manifest_are_rejected(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _manifest(manifest_path, session_char="a")
    _first, private_path, public_path = _keypair(tmp_path, "first")
    _second, _other_private, other_public = _keypair(tmp_path, "second")
    signature_path = tmp_path / "manifest.sig.json"
    sign_evidence_manifest(
        manifest_path,
        private_key_path=private_path,
        output_path=signature_path,
    )

    with pytest.raises(EvidenceSigningError, match="fingerprint does not match public key"):
        verify_portable_evidence_with_signature(
            manifest_path,
            signature_path=signature_path,
            public_key_path=other_public,
        )

    _manifest(manifest_path, session_char="b")
    with pytest.raises(EvidenceSigningError, match="manifest SHA-256 does not match manifest"):
        verify_portable_evidence_with_signature(
            manifest_path,
            signature_path=signature_path,
            public_key_path=public_path,
        )


def test_envelope_tamper_duplicate_unknown_and_bad_signature_are_rejected(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _manifest(manifest_path)
    _keypair_result, private_path, public_path = _keypair(tmp_path)
    signature_path = tmp_path / "manifest.sig.json"
    sign_evidence_manifest(
        manifest_path,
        private_key_path=private_path,
        output_path=signature_path,
    )
    original = json.loads(signature_path.read_text(encoding="utf-8"))

    duplicate = tmp_path / "duplicate.sig.json"
    duplicate.write_text(
        '{"schema_version":"app-evidence-signature-v1","schema_version":"app-evidence-signature-v1",'
        f'"algorithm":"ed25519","key_fingerprint":"{original["key_fingerprint"]}",'
        f'"manifest_sha256":"{original["manifest_sha256"]}","signature":"{original["signature"]}"}}\n',
        encoding="utf-8",
    )
    with pytest.raises(EvidenceSigningError, match="duplicate object key"):
        verify_portable_evidence_with_signature(
            manifest_path,
            signature_path=duplicate,
            public_key_path=public_path,
        )

    unknown = tmp_path / "unknown.sig.json"
    unknown_payload = dict(original)
    unknown_payload["extra"] = True
    unknown.write_text(json.dumps(unknown_payload) + "\n", encoding="utf-8")
    with pytest.raises(EvidenceSigningError, match="does not satisfy"):
        verify_portable_evidence_with_signature(
            manifest_path,
            signature_path=unknown,
            public_key_path=public_path,
        )

    bad = tmp_path / "bad.sig.json"
    bad_payload = dict(original)
    replacement = "A" if bad_payload["signature"][0] != "A" else "B"
    bad_payload["signature"] = replacement + bad_payload["signature"][1:]
    bad.write_text(json.dumps(bad_payload) + "\n", encoding="utf-8")
    with pytest.raises(EvidenceSigningError, match="signature does not verify"):
        verify_portable_evidence_with_signature(
            manifest_path,
            signature_path=bad,
            public_key_path=public_path,
        )


def test_keygen_and_sign_refuse_overwrite_and_non_ed25519_keys(tmp_path: Path) -> None:
    existing_private = tmp_path / "private.pem"
    existing_private.write_text("keep", encoding="utf-8")
    with pytest.raises(EvidenceSigningError, match="refusing to overwrite"):
        generate_evidence_keypair(
            private_key_path=existing_private,
            public_key_path=tmp_path / "public.pem",
        )
    assert existing_private.read_text(encoding="utf-8") == "keep"

    manifest_path = tmp_path / "manifest.json"
    _manifest(manifest_path)
    _keypair_result, private_path, _public_path = _keypair(tmp_path, "good")
    existing_output = tmp_path / "signature.json"
    existing_output.write_text("keep", encoding="utf-8")
    with pytest.raises(EvidenceSigningError, match="refusing to overwrite"):
        sign_evidence_manifest(
            manifest_path,
            private_key_path=private_path,
            output_path=existing_output,
        )
    assert existing_output.read_text(encoding="utf-8") == "keep"

    rsa_private = generate_private_key(public_exponent=65537, key_size=2048)
    rsa_path = tmp_path / "rsa.private.pem"
    rsa_path.write_bytes(
        rsa_private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    with pytest.raises(EvidenceSigningError, match="not an Ed25519 private key"):
        sign_evidence_manifest(
            manifest_path,
            private_key_path=rsa_path,
            output_path=tmp_path / "rsa.sig.json",
        )


def test_symlink_inputs_are_rejected(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("symlink boundary is POSIX-qualified in CI")
    manifest_path = tmp_path / "manifest.json"
    _manifest(manifest_path)
    _keypair_result, private_path, public_path = _keypair(tmp_path)
    signature_path = tmp_path / "manifest.sig.json"
    sign_evidence_manifest(
        manifest_path,
        private_key_path=private_path,
        output_path=signature_path,
    )

    linked_signature = tmp_path / "linked.sig.json"
    linked_signature.symlink_to(signature_path)
    with pytest.raises(EvidenceSigningError, match="symbolic link"):
        verify_portable_evidence_with_signature(
            manifest_path,
            signature_path=linked_signature,
            public_key_path=public_path,
        )

    linked_private = tmp_path / "linked.private.pem"
    linked_private.symlink_to(private_path)
    with pytest.raises(EvidenceSigningError, match="symbolic link"):
        sign_evidence_manifest(
            manifest_path,
            private_key_path=linked_private,
            output_path=tmp_path / "linked-key.sig.json",
        )
