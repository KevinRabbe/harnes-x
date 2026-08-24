from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import harness_x.evidence_verification_receipt as receipt_module
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
from harness_x.evidence_signing import EvidenceSigningError, generate_evidence_keypair
from harness_x.evidence_verification_receipt import (
    VERIFICATION_RECEIPT_SCHEMA_VERSION,
    persist_verification_receipt,
    render_verification_receipt,
)


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="export one deterministic offline verification receipt",
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


def test_receipt_renderer_is_deterministic_and_contains_only_frozen_result_metadata(
    tmp_path: Path,
) -> None:
    service, generated, public_key, capsule_path, manifest, _signature = _valid_capsule(tmp_path)
    try:
        output_dir = tmp_path / "verified"
        output_dir.mkdir()
        verified = verify_evidence_capsule(
            capsule_path,
            output_dir=output_dir,
            public_key_path=public_key,
        )

        first = render_verification_receipt(verified)
        second = render_verification_receipt(verified)
        assert first.payload == second.payload
        assert first.source_bytes == len(first.payload)
        assert first.source_sha256 == hashlib.sha256(first.payload).hexdigest()
        assert first.payload.endswith(b"\n")

        receipt = json.loads(first.payload.decode("utf-8"))
        assert list(receipt) == sorted(receipt)
        assert receipt["schema_version"] == VERIFICATION_RECEIPT_SCHEMA_VERSION
        assert receipt["algorithm"] == "ed25519"
        assert receipt["capsule_status"] == "validated"
        assert receipt["signature_status"] == "verified"
        assert receipt["manifest_sha256"] == hashlib.sha256(manifest).hexdigest()
        assert receipt["manifest_bytes"] == len(manifest)
        assert receipt["key_fingerprint"] == generated.key_fingerprint
        assert receipt["manifest_filename"] == MANIFEST_FILENAME
        assert receipt["signature_filename"] == SIGNATURE_FILENAME
        text = first.payload.decode("utf-8")
        assert str(tmp_path) not in text
        assert str(capsule_path) not in text
        assert "timestamp" not in text
    finally:
        service.close()


def test_persist_receipt_accepts_only_an_already_successful_frozen_m57_result(tmp_path: Path) -> None:
    service, generated, public_key, capsule_path, manifest, signature = _valid_capsule(tmp_path)
    try:
        output_dir = tmp_path / "verified"
        output_dir.mkdir()
        verified = verify_evidence_capsule(
            capsule_path,
            output_dir=output_dir,
            public_key_path=public_key,
        )
        receipt_path = tmp_path / "verification-receipt.json"
        result = persist_verification_receipt(
            verified,
            receipt_path=receipt_path,
        )

        assert (output_dir / MANIFEST_FILENAME).read_bytes() == manifest
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == signature
        receipt_bytes = receipt_path.read_bytes()
        assert result.receipt_path == str(receipt_path)
        assert result.receipt_sha256 == hashlib.sha256(receipt_bytes).hexdigest()
        assert result.verification.verification.signature_status == "verified"
        assert result.verification.verification.key_fingerprint == generated.key_fingerprint
        summary = result.summary()
        assert summary.startswith("valid: ")
        assert "capsule=validated" in summary
        assert f"receipt={receipt_path}" in summary
        assert f"receipt_sha256={result.receipt_sha256}" in summary
    finally:
        service.close()


def test_wrong_key_cli_failure_creates_no_receipt_and_retains_unverified_pair(tmp_path: Path) -> None:
    service, _generated, _public_key, capsule_path, manifest, signature = _valid_capsule(tmp_path)
    wrong_private = tmp_path / "wrong.private.pem"
    wrong_public = tmp_path / "wrong.public.pem"
    generate_evidence_keypair(
        private_key_path=wrong_private,
        public_key_path=wrong_public,
    )
    try:
        output_dir = tmp_path / "wrong-key"
        output_dir.mkdir()
        receipt_path = tmp_path / "should-not-exist.json"
        with pytest.raises(SystemExit) as exc_info:
            cli_main(
                [
                    "verify-evidence-capsule",
                    str(capsule_path),
                    "--output-dir",
                    str(output_dir),
                    "--public-key",
                    str(wrong_public),
                    "--receipt",
                    str(receipt_path),
                ]
            )
        assert exc_info.value.code == 2
        assert not receipt_path.exists()
        assert (output_dir / MANIFEST_FILENAME).read_bytes() == manifest
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == signature
    finally:
        service.close()


def test_receipt_overwrite_failure_is_nonzero_and_does_not_delete_verified_pair(tmp_path: Path) -> None:
    service, _generated, public_key, capsule_path, manifest, signature = _valid_capsule(tmp_path)
    try:
        output_dir = tmp_path / "verified"
        output_dir.mkdir()
        verified = verify_evidence_capsule(
            capsule_path,
            output_dir=output_dir,
            public_key_path=public_key,
        )
        receipt_path = tmp_path / "existing-receipt.json"
        receipt_path.write_text("existing", encoding="utf-8")

        with pytest.raises(EvidenceSigningError, match="refusing to overwrite"):
            persist_verification_receipt(
                verified,
                receipt_path=receipt_path,
            )

        assert receipt_path.read_text(encoding="utf-8") == "existing"
        assert (output_dir / MANIFEST_FILENAME).read_bytes() == manifest
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == signature
    finally:
        service.close()


def test_verify_capsule_cli_receipt_is_optional_and_additive(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, _generated, public_key, capsule_path, _manifest, _signature = _valid_capsule(tmp_path)
    try:
        parser = build_parser()
        help_text = parser.format_help()
        assert "verify-evidence-capsule" in help_text
        command = parser.parse_args(
            [
                "verify-evidence-capsule",
                str(capsule_path),
                "--output-dir",
                str(tmp_path),
                "--public-key",
                str(public_key),
            ]
        )
        assert command.receipt is None

        output_dir = tmp_path / "cli-output"
        output_dir.mkdir()
        receipt_path = tmp_path / "cli-receipt.json"
        assert (
            cli_main(
                [
                    "verify-evidence-capsule",
                    str(capsule_path),
                    "--output-dir",
                    str(output_dir),
                    "--public-key",
                    str(public_key),
                    "--receipt",
                    str(receipt_path),
                ]
            )
            == 0
        )
        output = capsys.readouterr().out.strip()
        assert output.startswith("valid: ")
        assert f"receipt={receipt_path}" in output
        assert receipt_path.is_file()
    finally:
        service.close()


def test_receipt_module_contains_no_crypto_network_time_or_capsule_parser_surface() -> None:
    source = Path(receipt_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "cryptography",
        "Ed25519PrivateKey",
        "Ed25519PublicKey",
        "_load_public_key",
        "_signature_bytes",
        "base64",
        "json.loads",
        "urllib",
        "requests",
        "httpx",
        "socket",
        "datetime",
        "time.time",
        "verify_evidence_capsule(",
    ):
        assert forbidden not in source
    assert "persist_verification_receipt(" in source
    assert "_exclusive_write(" in source
    assert "json.dumps(" in source
