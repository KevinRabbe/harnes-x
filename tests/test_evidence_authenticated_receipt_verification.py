from __future__ import annotations

import json
from pathlib import Path

import pytest

import harness_x.evidence_authenticated_receipt_verification as authenticated_module
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
from harness_x.evidence_authenticated_receipt_verification import (
    AuthenticatedEvidenceReceiptVerificationError,
    verify_authenticated_evidence_receipt,
)
from harness_x.evidence_capsule_extraction import MANIFEST_FILENAME, SIGNATURE_FILENAME
from harness_x.evidence_capsule_verification import verify_evidence_capsule
from harness_x.evidence_signing import generate_evidence_keypair
from harness_x.evidence_verification import PortableEvidenceVerificationError
from harness_x.evidence_verification_receipt import persist_verification_receipt
from harness_x.evidence_verification_receipt_reconciliation import (
    EvidenceVerificationReceiptReconciliationError,
)
from harness_x.evidence_verification_receipt_signing import (
    sign_evidence_verification_receipt,
)


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="compose authenticated receipt bytes with fresh evidence reconciliation",
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


def _prepared_authenticated_receipt(tmp_path: Path):
    evidence_generated, evidence_private, evidence_public = _keypair(tmp_path, "evidence")
    receipt_generated, receipt_private, receipt_public = _keypair(tmp_path, "receipt")
    assert evidence_generated.key_fingerprint != receipt_generated.key_fingerprint

    service = AppServerService(tmp_path / "service")
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    output = tmp_path / "service-output"
    output.mkdir(parents=True, exist_ok=True)
    snapshot = service.store.create_session(_request(workspace), output_root=output)
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
    signature = signer.render(
        manifest.payload,
        manifest_sha256=manifest.source_sha256,
    )
    capsule = render_signed_manifest_capsule(manifest, signature)
    capsule_path = tmp_path / "session-evidence-signed-manifest-pair.json"
    capsule_path.write_bytes(capsule.payload)

    receipt_source_dir = tmp_path / "receipt-source"
    receipt_source_dir.mkdir()
    verified = verify_evidence_capsule(
        capsule_path,
        output_dir=receipt_source_dir,
        public_key_path=evidence_public,
    )
    receipt_path = tmp_path / "verification-receipt.json"
    persisted = persist_verification_receipt(
        verified,
        receipt_path=receipt_path,
    )
    receipt_signature_path = tmp_path / "verification-receipt.sig.json"
    signed_receipt = sign_evidence_verification_receipt(
        receipt_path,
        private_key_path=receipt_private,
        output_path=receipt_signature_path,
    )
    assert persisted.receipt_sha256 == signed_receipt.receipt_sha256

    return {
        "service": service,
        "evidence_generated": evidence_generated,
        "evidence_public": evidence_public,
        "receipt_generated": receipt_generated,
        "receipt_private": receipt_private,
        "receipt_public": receipt_public,
        "capsule_path": capsule_path,
        "receipt_path": receipt_path,
        "receipt_signature_path": receipt_signature_path,
        "manifest": manifest.payload,
        "signature": signature.payload,
    }


def test_composition_succeeds_with_distinct_receipt_and_evidence_key_roles(tmp_path: Path) -> None:
    prepared = _prepared_authenticated_receipt(tmp_path)
    service = prepared["service"]
    try:
        output_dir = tmp_path / "fresh"
        output_dir.mkdir()
        result = verify_authenticated_evidence_receipt(
            prepared["receipt_path"],
            prepared["capsule_path"],
            receipt_signature_path=prepared["receipt_signature_path"],
            receipt_public_key_path=prepared["receipt_public"],
            evidence_public_key_path=prepared["evidence_public"],
            output_dir=output_dir,
        )

        assert result.authentication.key_fingerprint == prepared["receipt_generated"].key_fingerprint
        assert (
            result.reconciliation.verification.verification.key_fingerprint
            == prepared["evidence_generated"].key_fingerprint
        )
        assert result.authentication.receipt_sha256 == result.reconciliation.receipt_sha256
        assert prepared["receipt_generated"].key_fingerprint != prepared["evidence_generated"].key_fingerprint
        assert (output_dir / MANIFEST_FILENAME).read_bytes() == prepared["manifest"]
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == prepared["signature"]
        summary = result.summary()
        assert summary.startswith("valid: ")
        assert "receipt=reconciled" in summary
        assert "receipt_authentication=verified" in summary
        assert (
            f"receipt_authentication_key={prepared['receipt_generated'].key_fingerprint}"
            in summary
        )
    finally:
        service.close()


def test_wrong_receipt_public_key_fails_before_fresh_extraction(tmp_path: Path) -> None:
    prepared = _prepared_authenticated_receipt(tmp_path)
    _wrong_generated, _wrong_private, wrong_public = _keypair(tmp_path, "wrong-receipt")
    service = prepared["service"]
    try:
        output_dir = tmp_path / "must-stay-empty"
        output_dir.mkdir()
        with pytest.raises(PortableEvidenceVerificationError):
            verify_authenticated_evidence_receipt(
                prepared["receipt_path"],
                prepared["capsule_path"],
                receipt_signature_path=prepared["receipt_signature_path"],
                receipt_public_key_path=wrong_public,
                evidence_public_key_path=prepared["evidence_public"],
                output_dir=output_dir,
            )
        assert list(output_dir.iterdir()) == []
    finally:
        service.close()


def test_tampered_receipt_signature_fails_before_fresh_extraction(tmp_path: Path) -> None:
    prepared = _prepared_authenticated_receipt(tmp_path)
    service = prepared["service"]
    try:
        signature_path = prepared["receipt_signature_path"]
        envelope = json.loads(signature_path.read_text(encoding="utf-8"))
        current = envelope["signature"][0]
        envelope["signature"] = ("A" if current != "A" else "B") + envelope["signature"][1:]
        signature_path.write_bytes(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        )

        output_dir = tmp_path / "must-stay-empty"
        output_dir.mkdir()
        with pytest.raises(PortableEvidenceVerificationError):
            verify_authenticated_evidence_receipt(
                prepared["receipt_path"],
                prepared["capsule_path"],
                receipt_signature_path=signature_path,
                receipt_public_key_path=prepared["receipt_public"],
                evidence_public_key_path=prepared["evidence_public"],
                output_dir=output_dir,
            )
        assert list(output_dir.iterdir()) == []
    finally:
        service.close()


def test_authenticated_but_stale_receipt_fails_after_fresh_extraction(tmp_path: Path) -> None:
    prepared = _prepared_authenticated_receipt(tmp_path)
    service = prepared["service"]
    try:
        receipt_path = prepared["receipt_path"]
        receipt_path.write_bytes(receipt_path.read_bytes()[:-1] + b" \n")
        stale_signature = tmp_path / "stale-receipt.sig.json"
        sign_evidence_verification_receipt(
            receipt_path,
            private_key_path=prepared["receipt_private"],
            output_path=stale_signature,
        )

        output_dir = tmp_path / "stale-output"
        output_dir.mkdir()
        with pytest.raises(EvidenceVerificationReceiptReconciliationError):
            verify_authenticated_evidence_receipt(
                receipt_path,
                prepared["capsule_path"],
                receipt_signature_path=stale_signature,
                receipt_public_key_path=prepared["receipt_public"],
                evidence_public_key_path=prepared["evidence_public"],
                output_dir=output_dir,
            )
        assert (output_dir / MANIFEST_FILENAME).read_bytes() == prepared["manifest"]
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == prepared["signature"]
    finally:
        service.close()


def test_wrong_evidence_public_key_fails_after_authentication_and_retains_extracted_pair(
    tmp_path: Path,
) -> None:
    prepared = _prepared_authenticated_receipt(tmp_path)
    _wrong_generated, _wrong_private, wrong_evidence_public = _keypair(tmp_path, "wrong-evidence")
    service = prepared["service"]
    try:
        output_dir = tmp_path / "wrong-evidence-output"
        output_dir.mkdir()
        with pytest.raises(PortableEvidenceVerificationError):
            verify_authenticated_evidence_receipt(
                prepared["receipt_path"],
                prepared["capsule_path"],
                receipt_signature_path=prepared["receipt_signature_path"],
                receipt_public_key_path=prepared["receipt_public"],
                evidence_public_key_path=wrong_evidence_public,
                output_dir=output_dir,
            )
        assert (output_dir / MANIFEST_FILENAME).read_bytes() == prepared["manifest"]
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == prepared["signature"]
    finally:
        service.close()


def test_cross_read_receipt_sha_disagreement_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Authentication:
        receipt_sha256 = "a" * 64
        key_fingerprint = "sha256:" + "1" * 64

    class Reconciliation:
        receipt_sha256 = "b" * 64

        def summary(self) -> str:
            return "valid: delegated receipt=reconciled"

    def fake_authenticate(receipt_path, *, signature_path, public_key_path):
        calls.append("authenticate")
        assert receipt_path == tmp_path / "receipt.json"
        assert signature_path == tmp_path / "receipt.sig.json"
        assert public_key_path == tmp_path / "receipt.public.pem"
        return Authentication()

    def fake_reconcile(
        receipt_path,
        capsule_path,
        *,
        output_dir,
        public_key_path,
        snapshot_path,
        lifecycle_path,
        report_path,
        trace_path,
    ):
        calls.append("reconcile")
        assert receipt_path == tmp_path / "receipt.json"
        assert capsule_path == tmp_path / "capsule.json"
        assert output_dir == tmp_path / "output"
        assert public_key_path == tmp_path / "evidence.public.pem"
        assert snapshot_path is None
        assert lifecycle_path is None
        assert report_path is None
        assert trace_path is None
        return Reconciliation()

    monkeypatch.setattr(
        authenticated_module,
        "verify_evidence_verification_receipt_signature",
        fake_authenticate,
    )
    monkeypatch.setattr(
        authenticated_module,
        "reconcile_evidence_verification_receipt",
        fake_reconcile,
    )

    with pytest.raises(
        AuthenticatedEvidenceReceiptVerificationError,
        match="changed between authentication and reconciliation reads",
    ):
        verify_authenticated_evidence_receipt(
            tmp_path / "receipt.json",
            tmp_path / "capsule.json",
            receipt_signature_path=tmp_path / "receipt.sig.json",
            receipt_public_key_path=tmp_path / "receipt.public.pem",
            evidence_public_key_path=tmp_path / "evidence.public.pem",
            output_dir=tmp_path / "output",
        )
    assert calls == ["authenticate", "reconcile"]


def test_cli_command_is_additive_and_succeeds_with_two_explicit_key_roles(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prepared = _prepared_authenticated_receipt(tmp_path)
    service = prepared["service"]
    try:
        help_text = build_parser().format_help()
        for command in (
            "reconcile-evidence-receipt",
            "sign-evidence-receipt",
            "verify-evidence-receipt-signature",
            "verify-authenticated-evidence-receipt",
        ):
            assert command in help_text

        output_dir = tmp_path / "cli-output"
        output_dir.mkdir()
        assert (
            cli_main(
                [
                    "verify-authenticated-evidence-receipt",
                    str(prepared["receipt_path"]),
                    str(prepared["capsule_path"]),
                    "--receipt-signature",
                    str(prepared["receipt_signature_path"]),
                    "--receipt-public-key",
                    str(prepared["receipt_public"]),
                    "--evidence-public-key",
                    str(prepared["evidence_public"]),
                    "--output-dir",
                    str(output_dir),
                ]
            )
            == 0
        )
        output = capsys.readouterr().out.strip()
        assert output.startswith("valid: ")
        assert "receipt=reconciled" in output
        assert "receipt_authentication=verified" in output
        assert prepared["receipt_generated"].key_fingerprint in output
        assert prepared["evidence_generated"].key_fingerprint in output
    finally:
        service.close()


def test_m61_module_contains_only_frozen_orchestration_and_sha_correlation() -> None:
    source = Path(authenticated_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "cryptography",
        "Ed25519",
        "json",
        "base64",
        "_bounded_regular_file",
        "open(",
        "read_bytes",
        "urllib",
        "requests",
        "httpx",
        "socket",
        "datetime",
        "time.time",
        "AppServer",
    ):
        assert forbidden not in source
    assert "verify_evidence_verification_receipt_signature(" in source
    assert "reconcile_evidence_verification_receipt(" in source
    assert "authentication.receipt_sha256 != reconciliation.receipt_sha256" in source
