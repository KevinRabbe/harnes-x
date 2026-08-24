from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

import harness_x.evidence_capsule_extraction as extraction_module
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
from harness_x.evidence_capsule_extraction import (
    MANIFEST_FILENAME,
    MAX_EVIDENCE_CAPSULE_BYTES,
    SIGNATURE_FILENAME,
    EvidenceCapsuleExtractionError,
    extract_evidence_capsule,
    load_evidence_capsule,
)
from harness_x.evidence_signing import (
    EvidenceSigningError,
    generate_evidence_keypair,
    verify_portable_evidence_with_signature,
)


def _request(workspace: Path) -> CodingSessionRequest:
    return CodingSessionRequest(
        workspace_root=workspace,
        task="extract exact signed-manifest capsule payloads",
        model_profile="main",
        verification_commands=("python -m pytest",),
    )


def _terminal_manifest(tmp_path: Path):
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
    events = service.store.events(snapshot.session_id)
    manifest = build_terminal_evidence_manifest(snapshot=snapshot, events=events)
    rendered = render_terminal_evidence_manifest(manifest)
    return service, rendered


def _valid_capsule(tmp_path: Path):
    private_key = tmp_path / "evidence.private.pem"
    public_key = tmp_path / "evidence.public.pem"
    generated = generate_evidence_keypair(
        private_key_path=private_key,
        public_key_path=public_key,
    )
    service, manifest = _terminal_manifest(tmp_path / "session")
    signer = EvidenceManifestSigner.from_private_key_path(private_key)
    signature = signer.render(
        manifest.payload,
        manifest_sha256=manifest.source_sha256,
    )
    capsule = render_signed_manifest_capsule(manifest, signature)
    capsule_path = tmp_path / "session-evidence-signed-manifest-pair.json"
    capsule_path.write_bytes(capsule.payload)
    return (
        service,
        generated,
        public_key,
        capsule_path,
        capsule.payload,
        manifest.payload,
        signature.payload,
    )


def _canonical(raw: dict[str, object]) -> bytes:
    return (
        json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
        + b"\n"
    )


def _decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def test_extract_capsule_preserves_exact_m43_m52_bytes_and_verifies_with_frozen_m52(
    tmp_path: Path,
) -> None:
    service, generated, public_key, capsule_path, capsule_payload, manifest, signature = (
        _valid_capsule(tmp_path)
    )
    try:
        output_dir = tmp_path / "extracted"
        output_dir.mkdir()
        result = extract_evidence_capsule(capsule_path, output_dir=output_dir)

        assert Path(result.manifest_path).name == MANIFEST_FILENAME
        assert Path(result.signature_path).name == SIGNATURE_FILENAME
        assert Path(result.manifest_path).read_bytes() == manifest
        assert Path(result.signature_path).read_bytes() == signature
        assert result.key_fingerprint == generated.key_fingerprint
        assert result.manifest_sha256 == json.loads(capsule_payload)["manifest_sha256"]

        verified = verify_portable_evidence_with_signature(
            result.manifest_path,
            signature_path=result.signature_path,
            public_key_path=public_key,
        )
        assert verified.signature_status == "verified"
        assert verified.key_fingerprint == generated.key_fingerprint
    finally:
        service.close()


def test_capsule_parser_rejects_duplicate_unknown_noncanonical_and_mismatched_content(
    tmp_path: Path,
) -> None:
    service, _generated, _public_key, capsule_path, capsule_payload, _manifest, _signature = (
        _valid_capsule(tmp_path)
    )
    try:
        duplicate = capsule_payload.replace(
            b'"algorithm":"ed25519",',
            b'"algorithm":"ed25519","algorithm":"ed25519",',
            1,
        )
        duplicate_path = tmp_path / "duplicate.json"
        duplicate_path.write_bytes(duplicate)
        with pytest.raises(EvidenceCapsuleExtractionError, match="duplicate object key"):
            load_evidence_capsule(duplicate_path)

        raw = json.loads(capsule_payload)
        unknown = dict(raw)
        unknown["unexpected"] = True
        unknown_path = tmp_path / "unknown.json"
        unknown_path.write_bytes(_canonical(unknown))
        with pytest.raises(EvidenceCapsuleExtractionError, match="exact .* fields"):
            load_evidence_capsule(unknown_path)

        noncanonical_path = tmp_path / "noncanonical.json"
        noncanonical_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        with pytest.raises(EvidenceCapsuleExtractionError, match="canonical M55"):
            load_evidence_capsule(noncanonical_path)

        padded = dict(raw)
        padded["manifest_payload"] = str(padded["manifest_payload"]) + "="
        padded_path = tmp_path / "padded.json"
        padded_path.write_bytes(_canonical(padded))
        with pytest.raises(EvidenceCapsuleExtractionError, match="canonical base64url"):
            load_evidence_capsule(padded_path)

        wrong_hash = dict(raw)
        wrong_hash["manifest_sha256"] = "0" * 64
        wrong_hash_path = tmp_path / "wrong-hash.json"
        wrong_hash_path.write_bytes(_canonical(wrong_hash))
        with pytest.raises(EvidenceCapsuleExtractionError, match="manifest bytes do not match"):
            load_evidence_capsule(wrong_hash_path)

        signature_raw = json.loads(_decode(str(raw["signature_payload"])))
        signature_raw["key_fingerprint"] = "sha256:" + "f" * 64
        wrong_key = dict(raw)
        wrong_key["signature_payload"] = _encode(_canonical(signature_raw))
        wrong_key_path = tmp_path / "wrong-key.json"
        wrong_key_path.write_bytes(_canonical(wrong_key))
        with pytest.raises(EvidenceCapsuleExtractionError, match="key fingerprint disagrees"):
            load_evidence_capsule(wrong_key_path)

        noncanonical_signature = dict(raw)
        noncanonical_signature["signature_payload"] = _encode(
            (json.dumps(signature_raw, indent=2) + "\n").encode("utf-8")
        )
        noncanonical_signature["key_fingerprint"] = signature_raw["key_fingerprint"]
        noncanonical_signature_path = tmp_path / "noncanonical-signature.json"
        noncanonical_signature_path.write_bytes(_canonical(noncanonical_signature))
        with pytest.raises(EvidenceCapsuleExtractionError, match="canonical frozen M52"):
            load_evidence_capsule(noncanonical_signature_path)

        manifest_raw = json.loads(_decode(str(raw["manifest_payload"])))
        manifest_raw["fingerprint"] = "0" * 64
        bad_manifest = dict(raw)
        bad_manifest_bytes = (
            json.dumps(manifest_raw, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            + b"\n"
        )
        bad_manifest["manifest_payload"] = _encode(bad_manifest_bytes)
        import hashlib

        bad_manifest["manifest_sha256"] = hashlib.sha256(bad_manifest_bytes).hexdigest()
        signature_original = json.loads(_decode(str(raw["signature_payload"])))
        signature_original["manifest_sha256"] = bad_manifest["manifest_sha256"]
        bad_manifest["signature_payload"] = _encode(_canonical(signature_original))
        bad_manifest_path = tmp_path / "bad-manifest.json"
        bad_manifest_path.write_bytes(_canonical(bad_manifest))
        with pytest.raises(EvidenceCapsuleExtractionError, match="fingerprint does not match"):
            load_evidence_capsule(bad_manifest_path)
    finally:
        service.close()


def test_capsule_input_boundary_rejects_symlink_nonregular_and_oversize(tmp_path: Path) -> None:
    service, _generated, _public_key, capsule_path, _payload, _manifest, _signature = (
        _valid_capsule(tmp_path)
    )
    try:
        symlink = tmp_path / "capsule-link.json"
        symlink.symlink_to(capsule_path)
        with pytest.raises(EvidenceCapsuleExtractionError, match="symbolic link"):
            load_evidence_capsule(symlink)

        with pytest.raises(EvidenceCapsuleExtractionError, match="not a regular file"):
            load_evidence_capsule(tmp_path)

        oversized = tmp_path / "oversized.json"
        oversized.write_bytes(b"x" * (MAX_EVIDENCE_CAPSULE_BYTES + 1))
        with pytest.raises(EvidenceCapsuleExtractionError, match="exceeds .* byte limit"):
            load_evidence_capsule(oversized)
    finally:
        service.close()


def test_extraction_refuses_overwrite_and_rolls_back_first_output_on_second_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _generated, _public_key, capsule_path, _payload, _manifest, _signature = (
        _valid_capsule(tmp_path)
    )
    try:
        output_dir = tmp_path / "outputs"
        output_dir.mkdir()
        signature_target = output_dir / SIGNATURE_FILENAME
        signature_target.write_text("existing", encoding="utf-8")
        with pytest.raises(EvidenceCapsuleExtractionError, match="refusing to overwrite"):
            extract_evidence_capsule(capsule_path, output_dir=output_dir)
        assert not (output_dir / MANIFEST_FILENAME).exists()
        assert signature_target.read_text(encoding="utf-8") == "existing"

        signature_target.unlink()
        original_write = extraction_module._exclusive_write
        calls = 0

        def fail_second(path, payload, *, mode):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise EvidenceSigningError("injected second-output failure")
            return original_write(path, payload, mode=mode)

        monkeypatch.setattr(extraction_module, "_exclusive_write", fail_second)
        with pytest.raises(EvidenceCapsuleExtractionError, match="injected second-output failure"):
            extract_evidence_capsule(capsule_path, output_dir=output_dir)
        assert not (output_dir / MANIFEST_FILENAME).exists()
        assert not (output_dir / SIGNATURE_FILENAME).exists()
    finally:
        service.close()


def test_extraction_rejects_symlink_output_parent(tmp_path: Path) -> None:
    service, _generated, _public_key, capsule_path, _payload, _manifest, _signature = (
        _valid_capsule(tmp_path)
    )
    try:
        real = tmp_path / "real-output"
        real.mkdir()
        linked = tmp_path / "linked-output"
        linked.symlink_to(real, target_is_directory=True)
        with pytest.raises(EvidenceSigningError, match="output parent is not a directory"):
            extract_evidence_capsule(capsule_path, output_dir=linked)
        assert list(real.iterdir()) == []
    finally:
        service.close()


def test_extract_capsule_cli_is_lazy_additive_and_uses_fixed_outputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, generated, _public_key, capsule_path, _payload, manifest, signature = (
        _valid_capsule(tmp_path)
    )
    try:
        help_text = build_parser().format_help()
        assert "verify-evidence" in help_text
        assert "evidence-keygen" in help_text
        assert "sign-evidence" in help_text
        assert "extract-evidence-capsule" in help_text

        output_dir = tmp_path / "cli-output"
        output_dir.mkdir()
        assert (
            cli_main(
                [
                    "extract-evidence-capsule",
                    str(capsule_path),
                    "--output-dir",
                    str(output_dir),
                ]
            )
            == 0
        )
        output = capsys.readouterr().out.strip()
        assert output.startswith("extracted: ")
        assert f"key={generated.key_fingerprint}" in output
        assert (output_dir / MANIFEST_FILENAME).read_bytes() == manifest
        assert (output_dir / SIGNATURE_FILENAME).read_bytes() == signature
    finally:
        service.close()


def test_extraction_module_contains_no_network_or_public_key_trust_surface() -> None:
    source = Path(extraction_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "urllib",
        "requests",
        "httpx",
        "socket",
        "urlopen",
        "public_key_path",
        "verify_portable_evidence_with_signature",
        "crypto.subtle",
    ):
        assert forbidden not in source
    assert "_bounded_regular_file" in source
    assert "_exclusive_write" in source
    assert "_load_manifest" in source
