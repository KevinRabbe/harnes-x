from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import harness_x.evidence_signed_receipt_export as export_module
from harness_x.app_server import (
    AppEventKind,
    AppServerService,
    AppSessionStatus,
    CodingSessionRequest,
    build_terminal_evidence_manifest,
    render_terminal_evidence_manifest,
)
from harness_x.app_server.evidence_capsule import render_signed_manifest_capsule
from harness_x.app_server.evidence_signature import EvidenceManifestSigner
from harness_x.cli_entry import build_parser, main as cli_main
from harness_x.evidence_capsule_extraction import MANIFEST_FILENAME, SIGNATURE_FILENAME
from harness_x.evidence_signing import generate_evidence_keypair
from harness_x.evidence_signed_receipt_export import (
    SignedEvidenceReceiptExportError,
    export_signed_evidence_receipt,
)
from harness_x.evidence_verification import PortableEvidenceVerificationError
from harness_x.evidence_verification_receipt_signing import (
    verify_evidence_verification_receipt_signature,
)


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="freshly verify and export one signed verification receipt",
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def _keypair(tmp_path: Path, prefix: str):
    private_key = tmp_path / f"{prefix}.private.pem"
    public_key = tmp_path / f"{prefix}.public.pem"
    generated = generate_evidence_keypair(
        private_key_path=private_key,
        public_key_path=public_key,
    )
    return generated, private_key, public_key


def _prepared_capsule(tmp_path: Path):
    evidence_generated, evidence_private, evidence_public = _keypair(tmp_path, "evidence")
    receipt_generated, receipt_private, receipt_public = _keypair(tmp_path, "receipt")
    assert evidence_generated.key_fingerprint != receipt_generated.key_fingerprint

    service = AppServerService(tmp_path / "service")
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service_output = tmp_path / "service-output"
    service_output.mkdir(parents=True, exist_ok=True)
    snapshot = service.store.create_session(_request(workspace), output_root=service_output)
    snapshot = service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.RUNNING,
        kind=AppEventKind.SESSION_STARTED,
    )
    snapshot = service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.SUCCEEDED,
        kind=AppEventKind.SESSION_COMPLETED,
    )
    manifest = render_terminal_evidence_manifest(
        build_terminal_evidence_manifest(
            snapshot=snapshot,
            events=service.store.events(snapshot.session_id),
        )
    )
    signer = EvidenceManifestSigner.from_private_key_path(evidence_private)
    evidence_signature = signer.render(
        manifest.payload,
        manifest_sha256=manifest.source_sha256,
    )
    capsule = render_signed_manifest_capsule(manifest, evidence_signature)
    capsule_path = tmp_path / "session-evidence-signed-manifest-pair.json"
    capsule_path.write_bytes(capsule.payload)
    return {
        "service": service,
        "evidence_generated": evidence_generated,
        "evidence_public": evidence_public,
        "receipt_generated": receipt_generated,
        "receipt_private": receipt_private,
        "receipt_public": receipt_public,
        "capsule_path": capsule_path,
        "manifest": manifest.payload,
        "evidence_signature": evidence_signature.payload,
    }


def test_export_succeeds_with_distinct_evidence_and_receipt_signing_key_roles(
    tmp_path: Path,
) -> None:
    prepared = _prepared_capsule(tmp_path)
    service = prepared["service"]
    try:
        output_dir = tmp_path / "verified-output"
        output_dir.mkdir()
        receipt_path = tmp_path / "verification-receipt.json"
        receipt_signature_path = tmp_path / "verification-receipt.sig.json"

        result = export_signed_evidence_receipt(
            prepared["capsule_path"],
            output_dir=output_dir,
            evidence_public_key_path=prepared["evidence_public"],
            receipt_path=receipt_path,
            receipt_private_key_path=prepared["receipt_private"],
            receipt_signature_path=receipt_signature_path,
        )

        assert prepared["evidence_generated"].key_fingerprint != prepared["receipt_generated"].key_fingerprint
        assert (
            result.receipt.verification.verification.key_fingerprint
            == prepared["evidence_generated"].key_fingerprint
        )
        assert result.signature.key_fingerprint == prepared["receipt_generated"].key_fingerprint
        assert result.receipt.receipt_sha256 == result.signature.receipt_sha256
        assert result.receipt.receipt_sha256 == hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        assert (output_dir / MANIFEST_FILENAME).read_bytes() == prepared["manifest"]
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == prepared["evidence_signature"]
        assert receipt_path.is_file()
        assert receipt_signature_path.is_file()

        verified_receipt_signature = verify_evidence_verification_receipt_signature(
            receipt_path,
            signature_path=receipt_signature_path,
            public_key_path=prepared["receipt_public"],
        )
        assert verified_receipt_signature.receipt_sha256 == result.receipt.receipt_sha256
        assert verified_receipt_signature.key_fingerprint == prepared["receipt_generated"].key_fingerprint

        summary = result.summary()
        assert summary.startswith("valid: ")
        assert f"receipt={receipt_path}" in summary
        assert f"receipt_sha256={result.receipt.receipt_sha256}" in summary
        assert "receipt_signature=signed" in summary
        assert f"receipt_signature_key={prepared['receipt_generated'].key_fingerprint}" in summary
        assert f"receipt_signature_output={receipt_signature_path}" in summary
    finally:
        service.close()


def test_wrong_evidence_public_key_creates_no_receipt_or_receipt_signature(tmp_path: Path) -> None:
    prepared = _prepared_capsule(tmp_path)
    _wrong_generated, _wrong_private, wrong_public = _keypair(tmp_path, "wrong-evidence")
    service = prepared["service"]
    try:
        output_dir = tmp_path / "wrong-evidence-output"
        output_dir.mkdir()
        receipt_path = tmp_path / "must-not-exist-receipt.json"
        receipt_signature_path = tmp_path / "must-not-exist-receipt.sig.json"

        with pytest.raises(PortableEvidenceVerificationError):
            export_signed_evidence_receipt(
                prepared["capsule_path"],
                output_dir=output_dir,
                evidence_public_key_path=wrong_public,
                receipt_path=receipt_path,
                receipt_private_key_path=prepared["receipt_private"],
                receipt_signature_path=receipt_signature_path,
            )

        assert not receipt_path.exists()
        assert not receipt_signature_path.exists()
        assert (output_dir / MANIFEST_FILENAME).read_bytes() == prepared["manifest"]
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == prepared["evidence_signature"]
    finally:
        service.close()


def test_receipt_persistence_failure_prevents_signature_attempt(tmp_path: Path) -> None:
    prepared = _prepared_capsule(tmp_path)
    service = prepared["service"]
    try:
        output_dir = tmp_path / "receipt-failure-output"
        output_dir.mkdir()
        receipt_path = tmp_path / "existing-receipt.json"
        receipt_path.write_text("sentinel", encoding="utf-8")
        receipt_signature_path = tmp_path / "must-not-exist.sig.json"

        with pytest.raises(PortableEvidenceVerificationError, match="refusing to overwrite"):
            export_signed_evidence_receipt(
                prepared["capsule_path"],
                output_dir=output_dir,
                evidence_public_key_path=prepared["evidence_public"],
                receipt_path=receipt_path,
                receipt_private_key_path=prepared["receipt_private"],
                receipt_signature_path=receipt_signature_path,
            )

        assert receipt_path.read_text(encoding="utf-8") == "sentinel"
        assert not receipt_signature_path.exists()
        assert (output_dir / MANIFEST_FILENAME).read_bytes() == prepared["manifest"]
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == prepared["evidence_signature"]
    finally:
        service.close()


def test_receipt_signing_failure_preserves_persisted_receipt_and_verified_outputs(tmp_path: Path) -> None:
    prepared = _prepared_capsule(tmp_path)
    service = prepared["service"]
    try:
        output_dir = tmp_path / "signing-failure-output"
        output_dir.mkdir()
        receipt_path = tmp_path / "persisted-receipt.json"
        receipt_signature_path = tmp_path / "existing-signature.json"
        receipt_signature_path.write_text("sentinel", encoding="utf-8")

        with pytest.raises(PortableEvidenceVerificationError, match="refusing to overwrite"):
            export_signed_evidence_receipt(
                prepared["capsule_path"],
                output_dir=output_dir,
                evidence_public_key_path=prepared["evidence_public"],
                receipt_path=receipt_path,
                receipt_private_key_path=prepared["receipt_private"],
                receipt_signature_path=receipt_signature_path,
            )

        assert receipt_path.is_file()
        assert receipt_path.read_bytes().endswith(b"\n")
        assert receipt_signature_path.read_text(encoding="utf-8") == "sentinel"
        assert (output_dir / MANIFEST_FILENAME).read_bytes() == prepared["manifest"]
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == prepared["evidence_signature"]
    finally:
        service.close()


def test_m58_m60_receipt_sha_disagreement_is_rejected_in_phase_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Verification:
        pass

    class Receipt:
        receipt_path = str(tmp_path / "receipt.json")
        receipt_sha256 = "a" * 64

        def summary(self) -> str:
            return "valid: delegated receipt=receipt.json"

    class Signature:
        output_path = str(tmp_path / "receipt.sig.json")
        receipt_sha256 = "b" * 64
        key_fingerprint = "sha256:" + "1" * 64

    def fake_verify(capsule_path, **kwargs):
        calls.append("verify")
        assert capsule_path == tmp_path / "capsule.json"
        return Verification()

    def fake_persist(result, *, receipt_path):
        calls.append("persist")
        assert isinstance(result, Verification)
        assert receipt_path == tmp_path / "receipt.json"
        return Receipt()

    def fake_sign(receipt_path, *, private_key_path, output_path):
        calls.append("sign")
        assert receipt_path == Receipt.receipt_path
        assert private_key_path == tmp_path / "receipt.private.pem"
        assert output_path == tmp_path / "receipt.sig.json"
        return Signature()

    monkeypatch.setattr(export_module, "verify_evidence_capsule", fake_verify)
    monkeypatch.setattr(export_module, "persist_verification_receipt", fake_persist)
    monkeypatch.setattr(export_module, "sign_evidence_verification_receipt", fake_sign)

    with pytest.raises(
        SignedEvidenceReceiptExportError,
        match="changed between persistence and detached signing reads",
    ):
        export_signed_evidence_receipt(
            tmp_path / "capsule.json",
            output_dir=tmp_path / "output",
            evidence_public_key_path=tmp_path / "evidence.public.pem",
            receipt_path=tmp_path / "receipt.json",
            receipt_private_key_path=tmp_path / "receipt.private.pem",
            receipt_signature_path=tmp_path / "receipt.sig.json",
        )
    assert calls == ["verify", "persist", "sign"]


def test_persistence_failure_short_circuits_frozen_m60_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_verify(*args, **kwargs):
        calls.append("verify")
        return object()

    def fake_persist(*args, **kwargs):
        calls.append("persist")
        raise PortableEvidenceVerificationError("persistence failed")

    def forbidden_sign(*args, **kwargs):
        calls.append("sign")
        raise AssertionError("M60 signing must not run after M58 persistence failure")

    monkeypatch.setattr(export_module, "verify_evidence_capsule", fake_verify)
    monkeypatch.setattr(export_module, "persist_verification_receipt", fake_persist)
    monkeypatch.setattr(export_module, "sign_evidence_verification_receipt", forbidden_sign)

    with pytest.raises(PortableEvidenceVerificationError, match="persistence failed"):
        export_signed_evidence_receipt(
            tmp_path / "capsule.json",
            output_dir=tmp_path / "output",
            evidence_public_key_path=tmp_path / "evidence.public.pem",
            receipt_path=tmp_path / "receipt.json",
            receipt_private_key_path=tmp_path / "receipt.private.pem",
            receipt_signature_path=tmp_path / "receipt.sig.json",
        )
    assert calls == ["verify", "persist"]


def test_cli_export_command_is_additive_and_succeeds(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    prepared = _prepared_capsule(tmp_path)
    service = prepared["service"]
    try:
        help_text = build_parser().format_help()
        for command in (
            "verify-authenticated-evidence-receipt",
            "sign-evidence-receipt",
            "verify-evidence-receipt-signature",
            "export-signed-evidence-receipt",
        ):
            assert command in help_text

        output_dir = tmp_path / "cli-output"
        output_dir.mkdir()
        receipt_path = tmp_path / "cli-receipt.json"
        receipt_signature_path = tmp_path / "cli-receipt.sig.json"
        assert (
            cli_main(
                [
                    "export-signed-evidence-receipt",
                    str(prepared["capsule_path"]),
                    "--output-dir",
                    str(output_dir),
                    "--evidence-public-key",
                    str(prepared["evidence_public"]),
                    "--receipt",
                    str(receipt_path),
                    "--receipt-private-key",
                    str(prepared["receipt_private"]),
                    "--receipt-signature",
                    str(receipt_signature_path),
                ]
            )
            == 0
        )
        output = capsys.readouterr().out.strip()
        assert output.startswith("valid: ")
        assert "receipt_signature=signed" in output
        assert prepared["evidence_generated"].key_fingerprint in output
        assert prepared["receipt_generated"].key_fingerprint in output
        assert receipt_path.is_file()
        assert receipt_signature_path.is_file()
    finally:
        service.close()


def test_m62_module_contains_only_frozen_orchestration_and_sha_correlation() -> None:
    source = Path(export_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "cryptography",
        "Ed25519",
        "json",
        "base64",
        "_bounded_regular_file",
        "_exclusive_write",
        "_load_private_key",
        "open(",
        "read_bytes",
        "write_bytes",
        "urllib",
        "requests",
        "httpx",
        "socket",
        "datetime",
        "time.time",
        "AppServer",
    ):
        assert forbidden not in source
    assert "verify_evidence_capsule(" in source
    assert "persist_verification_receipt(" in source
    assert "sign_evidence_verification_receipt(" in source
    assert "receipt.receipt_sha256 != signature.receipt_sha256" in source
