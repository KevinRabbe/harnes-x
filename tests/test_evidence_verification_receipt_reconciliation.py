from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import harness_x.evidence_verification_receipt_reconciliation as reconciliation_module
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
from harness_x.evidence_capsule_verification import verify_evidence_capsule
from harness_x.evidence_signing import generate_evidence_keypair
from harness_x.evidence_verification import PortableEvidenceVerificationError
from harness_x.evidence_verification_receipt import persist_verification_receipt
from harness_x.evidence_verification_receipt_reconciliation import (
    MAX_EVIDENCE_VERIFICATION_RECEIPT_BYTES,
    EvidenceVerificationReceiptReconciliationError,
    reconcile_evidence_verification_receipt,
)


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="reconcile one unsigned receipt against fresh frozen verification",
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def _valid_capsule(tmp_path: Path):
    private_key = tmp_path / "evidence.private.pem"
    public_key = tmp_path / "evidence.public.pem"
    generated = generate_evidence_keypair(
        private_key_path=private_key,
        public_key_path=public_key,
    )

    service = AppServerService(tmp_path / "service")
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    output = tmp_path / "output"
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
    signer = EvidenceManifestSigner.from_private_key_path(private_key)
    signature = signer.render(
        manifest.payload,
        manifest_sha256=manifest.source_sha256,
    )
    capsule = render_signed_manifest_capsule(manifest, signature)
    capsule_path = tmp_path / "session-evidence-signed-manifest-pair.json"
    capsule_path.write_bytes(capsule.payload)
    return service, generated, public_key, capsule_path, manifest.payload, signature.payload


def _receipt_for_capsule(tmp_path: Path, capsule_path: Path, public_key: Path) -> Path:
    first_output = tmp_path / "receipt-source-output"
    first_output.mkdir()
    verified = verify_evidence_capsule(
        capsule_path,
        output_dir=first_output,
        public_key_path=public_key,
    )
    receipt_path = tmp_path / "verification-receipt.json"
    persist_verification_receipt(
        verified,
        receipt_path=receipt_path,
    )
    return receipt_path


def test_valid_receipt_reconciles_against_fresh_frozen_verification(tmp_path: Path) -> None:
    service, generated, public_key, capsule_path, manifest, signature = _valid_capsule(tmp_path)
    try:
        receipt_path = _receipt_for_capsule(tmp_path, capsule_path, public_key)
        expected_receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        fresh_output = tmp_path / "fresh-output"
        fresh_output.mkdir()

        result = reconcile_evidence_verification_receipt(
            receipt_path,
            capsule_path,
            output_dir=fresh_output,
            public_key_path=public_key,
        )

        assert Path(result.receipt_path) == receipt_path.resolve()
        assert result.receipt_sha256 == expected_receipt_sha
        assert result.verification.verification.signature_status == "verified"
        assert result.verification.verification.key_fingerprint == generated.key_fingerprint
        assert (fresh_output / MANIFEST_FILENAME).read_bytes() == manifest
        assert (fresh_output / SIGNATURE_FILENAME).read_bytes() == signature
        summary = result.summary()
        assert summary.startswith("valid: ")
        assert "receipt=reconciled" in summary
        assert f"receipt_sha256={expected_receipt_sha}" in summary
    finally:
        service.close()


def test_one_byte_receipt_mismatch_fails_after_fresh_verification_and_keeps_pair(
    tmp_path: Path,
) -> None:
    service, _generated, public_key, capsule_path, manifest, signature = _valid_capsule(tmp_path)
    try:
        receipt_path = _receipt_for_capsule(tmp_path, capsule_path, public_key)
        supplied = receipt_path.read_bytes()
        assert supplied.endswith(b"\n")
        receipt_path.write_bytes(supplied[:-1] + b" ")

        fresh_output = tmp_path / "mismatch-output"
        fresh_output.mkdir()
        with pytest.raises(
            EvidenceVerificationReceiptReconciliationError,
            match="receipt bytes do not match",
        ):
            reconcile_evidence_verification_receipt(
                receipt_path,
                capsule_path,
                output_dir=fresh_output,
                public_key_path=public_key,
            )

        assert (fresh_output / MANIFEST_FILENAME).read_bytes() == manifest
        assert (fresh_output / SIGNATURE_FILENAME).read_bytes() == signature
        assert receipt_path.read_bytes() == supplied[:-1] + b" "
    finally:
        service.close()


def test_noncanonical_or_stale_receipt_never_gets_fieldwise_acceptance(tmp_path: Path) -> None:
    service, _generated, public_key, capsule_path, _manifest, _signature = _valid_capsule(tmp_path)
    try:
        receipt_path = _receipt_for_capsule(tmp_path, capsule_path, public_key)
        canonical = receipt_path.read_bytes()
        receipt_path.write_bytes(b" " + canonical)
        fresh_output = tmp_path / "noncanonical-output"
        fresh_output.mkdir()

        with pytest.raises(EvidenceVerificationReceiptReconciliationError):
            reconcile_evidence_verification_receipt(
                receipt_path,
                capsule_path,
                output_dir=fresh_output,
                public_key_path=public_key,
            )
        assert (fresh_output / MANIFEST_FILENAME).is_file()
        assert (fresh_output / SIGNATURE_FILENAME).is_file()
    finally:
        service.close()


def test_receipt_boundary_failure_occurs_before_any_fresh_extraction(tmp_path: Path) -> None:
    service, _generated, public_key, capsule_path, _manifest, _signature = _valid_capsule(tmp_path)
    try:
        output_dir = tmp_path / "boundary-output"
        output_dir.mkdir()

        oversized = tmp_path / "oversized-receipt.json"
        oversized.write_bytes(b"x" * (MAX_EVIDENCE_VERIFICATION_RECEIPT_BYTES + 1))
        with pytest.raises(PortableEvidenceVerificationError, match="exceeds .* byte limit"):
            reconcile_evidence_verification_receipt(
                oversized,
                capsule_path,
                output_dir=output_dir,
                public_key_path=public_key,
            )
        assert list(output_dir.iterdir()) == []

        real_receipt = tmp_path / "real-receipt.json"
        real_receipt.write_text("{}\n", encoding="utf-8")
        linked = tmp_path / "receipt-link.json"
        linked.symlink_to(real_receipt)
        with pytest.raises(PortableEvidenceVerificationError, match="symbolic link"):
            reconcile_evidence_verification_receipt(
                linked,
                capsule_path,
                output_dir=output_dir,
                public_key_path=public_key,
            )
        assert list(output_dir.iterdir()) == []
    finally:
        service.close()


def test_wrong_public_key_fails_in_frozen_verifier_before_receipt_comparison(tmp_path: Path) -> None:
    service, _generated, public_key, capsule_path, manifest, signature = _valid_capsule(tmp_path)
    wrong_private = tmp_path / "wrong.private.pem"
    wrong_public = tmp_path / "wrong.public.pem"
    generate_evidence_keypair(
        private_key_path=wrong_private,
        public_key_path=wrong_public,
    )
    try:
        receipt_path = _receipt_for_capsule(tmp_path, capsule_path, public_key)
        receipt_before = receipt_path.read_bytes()
        output_dir = tmp_path / "wrong-key-output"
        output_dir.mkdir()

        with pytest.raises(PortableEvidenceVerificationError):
            reconcile_evidence_verification_receipt(
                receipt_path,
                capsule_path,
                output_dir=output_dir,
                public_key_path=wrong_public,
            )

        assert receipt_path.read_bytes() == receipt_before
        assert (output_dir / MANIFEST_FILENAME).read_bytes() == manifest
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == signature
    finally:
        service.close()


def test_reconcile_receipt_cli_is_additive_and_emits_success_only_on_exact_match(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, _generated, public_key, capsule_path, _manifest, _signature = _valid_capsule(tmp_path)
    try:
        receipt_path = _receipt_for_capsule(tmp_path, capsule_path, public_key)
        help_text = build_parser().format_help()
        for command in (
            "verify-evidence",
            "extract-evidence-capsule",
            "verify-evidence-capsule",
            "reconcile-evidence-receipt",
        ):
            assert command in help_text

        output_dir = tmp_path / "cli-output"
        output_dir.mkdir()
        assert (
            cli_main(
                [
                    "reconcile-evidence-receipt",
                    str(receipt_path),
                    str(capsule_path),
                    "--output-dir",
                    str(output_dir),
                    "--public-key",
                    str(public_key),
                ]
            )
            == 0
        )
        output = capsys.readouterr().out.strip()
        assert output.startswith("valid: ")
        assert "receipt=reconciled" in output
    finally:
        service.close()


def test_reconciliation_module_contains_no_receipt_parser_crypto_network_or_mutation() -> None:
    source = Path(reconciliation_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "json.loads",
        "json.dumps",
        "base64",
        "cryptography",
        "Ed25519",
        "_load_public_key",
        "_signature_bytes",
        "urllib",
        "requests",
        "httpx",
        "socket",
        "_exclusive_write",
        "write_bytes",
        "unlink",
    ):
        assert forbidden not in source
    assert "_bounded_regular_file(" in source
    assert "verify_evidence_capsule(" in source
    assert "render_verification_receipt(" in source
