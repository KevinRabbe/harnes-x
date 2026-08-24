from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import harness_x.evidence_capsule_verification as verification_module
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


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="orchestrate exact capsule extraction and frozen verification",
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


def test_orchestration_extracts_exact_bytes_then_uses_frozen_m52_verifier(tmp_path: Path) -> None:
    service, generated, public_key, capsule_path, manifest, signature = _valid_capsule(tmp_path)
    try:
        output_dir = tmp_path / "verified"
        output_dir.mkdir()
        result = verify_evidence_capsule(
            capsule_path,
            output_dir=output_dir,
            public_key_path=public_key,
        )

        assert Path(result.extraction.manifest_path).read_bytes() == manifest
        assert Path(result.extraction.signature_path).read_bytes() == signature
        assert result.extraction.manifest_sha256 == hashlib.sha256(manifest).hexdigest()
        assert result.extraction.key_fingerprint == generated.key_fingerprint
        assert result.verification.signature_status == "verified"
        assert result.verification.key_fingerprint == generated.key_fingerprint
        summary = result.summary()
        assert summary.startswith("valid: ")
        assert "signature=verified" in summary
        assert "capsule=validated" in summary
        assert f"extracted_manifest={output_dir / MANIFEST_FILENAME}" in summary
        assert f"extracted_signature={output_dir / SIGNATURE_FILENAME}" in summary
    finally:
        service.close()


def test_wrong_public_key_fails_after_extraction_and_retains_unverified_files(tmp_path: Path) -> None:
    service, _generated, _public_key, capsule_path, manifest, signature = _valid_capsule(tmp_path)
    wrong_private = tmp_path / "wrong.private.pem"
    wrong_public = tmp_path / "wrong.public.pem"
    generate_evidence_keypair(
        private_key_path=wrong_private,
        public_key_path=wrong_public,
    )
    try:
        output_dir = tmp_path / "wrong-key-output"
        output_dir.mkdir()
        with pytest.raises(PortableEvidenceVerificationError):
            verify_evidence_capsule(
                capsule_path,
                output_dir=output_dir,
                public_key_path=wrong_public,
            )

        assert (output_dir / MANIFEST_FILENAME).read_bytes() == manifest
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == signature
    finally:
        service.close()


def test_orchestrator_forwards_only_extracted_pair_and_explicit_evidence_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / MANIFEST_FILENAME
    signature = tmp_path / SIGNATURE_FILENAME
    public_key = tmp_path / "public.pem"
    snapshot = tmp_path / "snapshot.json"
    lifecycle = tmp_path / "lifecycle.json"
    report = tmp_path / "report.json"
    trace = tmp_path / "trace.jsonl"

    class Extraction:
        manifest_path = str(manifest)
        signature_path = str(signature)
        manifest_sha256 = "a" * 64
        key_fingerprint = "sha256:" + "b" * 64

    class Verification:
        signature_status = "verified"
        key_fingerprint = Extraction.key_fingerprint

        def summary(self) -> str:
            return "valid: delegated"

    calls: dict[str, object] = {}

    def fake_extract(capsule_path, *, output_dir):
        calls["extract"] = (capsule_path, output_dir)
        return Extraction()

    def fake_verify(
        manifest_path,
        *,
        signature_path,
        public_key_path,
        snapshot_path,
        lifecycle_path,
        report_path,
        trace_path,
    ):
        calls["verify"] = (
            manifest_path,
            signature_path,
            public_key_path,
            snapshot_path,
            lifecycle_path,
            report_path,
            trace_path,
        )
        return Verification()

    monkeypatch.setattr(verification_module, "extract_evidence_capsule", fake_extract)
    monkeypatch.setattr(
        verification_module,
        "verify_portable_evidence_with_signature",
        fake_verify,
    )

    capsule = tmp_path / "capsule.json"
    output_dir = tmp_path / "output"
    result = verify_evidence_capsule(
        capsule,
        output_dir=output_dir,
        public_key_path=public_key,
        snapshot_path=snapshot,
        lifecycle_path=lifecycle,
        report_path=report,
        trace_path=trace,
    )

    assert calls["extract"] == (capsule, output_dir)
    assert calls["verify"] == (
        str(manifest),
        str(signature),
        public_key,
        snapshot,
        lifecycle,
        report,
        trace,
    )
    assert result.summary().startswith("valid: delegated capsule=validated")


def test_verify_capsule_cli_is_additive_and_success_summary_is_explicit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, generated, public_key, capsule_path, manifest, signature = _valid_capsule(tmp_path)
    try:
        help_text = build_parser().format_help()
        for command in (
            "verify-evidence",
            "evidence-keygen",
            "sign-evidence",
            "extract-evidence-capsule",
            "verify-evidence-capsule",
        ):
            assert command in help_text

        output_dir = tmp_path / "cli-output"
        output_dir.mkdir()
        assert (
            cli_main(
                [
                    "verify-evidence-capsule",
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
        assert "signature=verified" in output
        assert f"key={generated.key_fingerprint}" in output
        assert "capsule=validated" in output
        assert (output_dir / MANIFEST_FILENAME).read_bytes() == manifest
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == signature
    finally:
        service.close()


def test_verification_orchestrator_contains_no_crypto_network_or_parser_reimplementation() -> None:
    source = Path(verification_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "cryptography",
        "Ed25519",
        "_signature_bytes",
        "_load_public_key",
        "base64",
        "json.loads",
        "urllib",
        "requests",
        "httpx",
        "socket",
    ):
        assert forbidden not in source
    assert "extract_evidence_capsule(" in source
    assert "verify_portable_evidence_with_signature(" in source
