from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import harness_x.evidence_verification_receipt_signing as signing_module
from harness_x.cli_entry import build_parser, main as cli_main
from harness_x.evidence_signing import generate_evidence_keypair
from harness_x.evidence_verification import PortableEvidenceVerificationError
from harness_x.evidence_verification_receipt_signing import (
    RECEIPT_SIGNATURE_SCHEMA_VERSION,
    EvidenceVerificationReceiptSignatureError,
    sign_evidence_verification_receipt,
    verify_evidence_verification_receipt_signature,
)


def _keypair(tmp_path: Path, prefix: str = "evidence"):
    private_key = tmp_path / f"{prefix}.private.pem"
    public_key = tmp_path / f"{prefix}.public.pem"
    generated = generate_evidence_keypair(
        private_key_path=private_key,
        public_key_path=public_key,
    )
    return generated, private_key, public_key


def _arbitrary_receipt(tmp_path: Path) -> Path:
    receipt = tmp_path / "verification-receipt.json"
    receipt.write_bytes(b'{"not":"necessarily-an-m58-receipt"}\n')
    return receipt


def _canonical_json_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def test_sign_and_verify_exact_arbitrary_receipt_bytes_without_m58_validation(
    tmp_path: Path,
) -> None:
    generated, private_key, public_key = _keypair(tmp_path)
    receipt = _arbitrary_receipt(tmp_path)
    signature = tmp_path / "verification-receipt.sig.json"

    signed = sign_evidence_verification_receipt(
        receipt,
        private_key_path=private_key,
        output_path=signature,
    )
    receipt_bytes = receipt.read_bytes()
    receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
    assert signed.receipt_sha256 == receipt_sha256
    assert signed.key_fingerprint == generated.key_fingerprint
    assert signed.output_path == str(signature)
    assert signed.summary().startswith("receipt-signed: ")

    envelope_bytes = signature.read_bytes()
    assert envelope_bytes.endswith(b"\n")
    envelope = json.loads(envelope_bytes.decode("utf-8"))
    assert list(envelope) == sorted(envelope)
    assert set(envelope) == {
        "algorithm",
        "key_fingerprint",
        "receipt_sha256",
        "schema_version",
        "signature",
    }
    assert envelope["schema_version"] == RECEIPT_SIGNATURE_SCHEMA_VERSION
    assert envelope["algorithm"] == "ed25519"
    assert envelope["key_fingerprint"] == generated.key_fingerprint
    assert envelope["receipt_sha256"] == receipt_sha256
    assert len(envelope["signature"]) == 86
    assert _canonical_json_bytes(envelope) == envelope_bytes

    verified = verify_evidence_verification_receipt_signature(
        receipt,
        signature_path=signature,
        public_key_path=public_key,
    )
    assert verified.receipt_sha256 == receipt_sha256
    assert verified.key_fingerprint == generated.key_fingerprint
    assert verified.summary() == (
        "valid: receipt_signature=verified "
        f"receipt_sha256={receipt_sha256} key={generated.key_fingerprint}"
    )


def test_one_byte_receipt_tamper_fails_exact_byte_verification(tmp_path: Path) -> None:
    _generated, private_key, public_key = _keypair(tmp_path)
    receipt = _arbitrary_receipt(tmp_path)
    signature = tmp_path / "verification-receipt.sig.json"
    sign_evidence_verification_receipt(
        receipt,
        private_key_path=private_key,
        output_path=signature,
    )

    receipt.write_bytes(receipt.read_bytes()[:-1] + b" ")
    with pytest.raises(
        EvidenceVerificationReceiptSignatureError,
        match="SHA-256 does not match receipt bytes",
    ):
        verify_evidence_verification_receipt_signature(
            receipt,
            signature_path=signature,
            public_key_path=public_key,
        )


def test_wrong_public_key_fails_without_receipt_semantic_claim(tmp_path: Path) -> None:
    _generated, private_key, _public_key = _keypair(tmp_path)
    _wrong_generated, _wrong_private, wrong_public = _keypair(tmp_path, "wrong")
    receipt = _arbitrary_receipt(tmp_path)
    signature = tmp_path / "verification-receipt.sig.json"
    sign_evidence_verification_receipt(
        receipt,
        private_key_path=private_key,
        output_path=signature,
    )

    with pytest.raises(
        EvidenceVerificationReceiptSignatureError,
        match="key fingerprint does not match public key",
    ):
        verify_evidence_verification_receipt_signature(
            receipt,
            signature_path=signature,
            public_key_path=wrong_public,
        )


def test_envelope_receipt_hash_tamper_fails_before_ed25519_verification(tmp_path: Path) -> None:
    _generated, private_key, public_key = _keypair(tmp_path)
    receipt = _arbitrary_receipt(tmp_path)
    signature = tmp_path / "verification-receipt.sig.json"
    sign_evidence_verification_receipt(
        receipt,
        private_key_path=private_key,
        output_path=signature,
    )

    envelope = json.loads(signature.read_text(encoding="utf-8"))
    envelope["receipt_sha256"] = "0" * 64
    signature.write_bytes(_canonical_json_bytes(envelope))
    with pytest.raises(
        EvidenceVerificationReceiptSignatureError,
        match="SHA-256 does not match receipt bytes",
    ):
        verify_evidence_verification_receipt_signature(
            receipt,
            signature_path=signature,
            public_key_path=public_key,
        )


def test_noncanonical_signature_envelope_serialization_is_rejected(tmp_path: Path) -> None:
    _generated, private_key, public_key = _keypair(tmp_path)
    receipt = _arbitrary_receipt(tmp_path)
    signature = tmp_path / "verification-receipt.sig.json"
    sign_evidence_verification_receipt(
        receipt,
        private_key_path=private_key,
        output_path=signature,
    )

    envelope = json.loads(signature.read_text(encoding="utf-8"))
    signature.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(
        EvidenceVerificationReceiptSignatureError,
        match="not the canonical M60 serialization",
    ):
        verify_evidence_verification_receipt_signature(
            receipt,
            signature_path=signature,
            public_key_path=public_key,
        )


def test_duplicate_signature_envelope_key_is_rejected(tmp_path: Path) -> None:
    _generated, private_key, public_key = _keypair(tmp_path)
    receipt = _arbitrary_receipt(tmp_path)
    signature = tmp_path / "verification-receipt.sig.json"
    sign_evidence_verification_receipt(
        receipt,
        private_key_path=private_key,
        output_path=signature,
    )

    text = signature.read_text(encoding="utf-8")
    duplicate = text.replace(
        '{"algorithm":"ed25519",',
        '{"algorithm":"ed25519","algorithm":"ed25519",',
        1,
    )
    signature.write_text(duplicate, encoding="utf-8")
    with pytest.raises(
        EvidenceVerificationReceiptSignatureError,
        match="duplicate object key: algorithm",
    ):
        verify_evidence_verification_receipt_signature(
            receipt,
            signature_path=signature,
            public_key_path=public_key,
        )


def test_invalid_signature_text_and_extra_fields_are_rejected(tmp_path: Path) -> None:
    _generated, private_key, public_key = _keypair(tmp_path)
    receipt = _arbitrary_receipt(tmp_path)
    signature = tmp_path / "verification-receipt.sig.json"
    sign_evidence_verification_receipt(
        receipt,
        private_key_path=private_key,
        output_path=signature,
    )

    envelope = json.loads(signature.read_text(encoding="utf-8"))
    envelope["signature"] = "*" * 86
    signature.write_bytes(_canonical_json_bytes(envelope))
    with pytest.raises(EvidenceVerificationReceiptSignatureError, match="does not satisfy"):
        verify_evidence_verification_receipt_signature(
            receipt,
            signature_path=signature,
            public_key_path=public_key,
        )

    envelope = json.loads(
        sign_again(tmp_path, receipt, private_key, "extra-field.sig.json").read_text(
            encoding="utf-8"
        )
    )
    envelope["unexpected"] = True
    extra_signature = tmp_path / "extra-field.sig.json"
    extra_signature.write_bytes(_canonical_json_bytes(envelope))
    with pytest.raises(EvidenceVerificationReceiptSignatureError, match="does not satisfy"):
        verify_evidence_verification_receipt_signature(
            receipt,
            signature_path=extra_signature,
            public_key_path=public_key,
        )


def sign_again(tmp_path: Path, receipt: Path, private_key: Path, name: str) -> Path:
    target = tmp_path / name
    sign_evidence_verification_receipt(
        receipt,
        private_key_path=private_key,
        output_path=target,
    )
    return target


def test_signing_refuses_overwrite_and_symlink_receipt_input(tmp_path: Path) -> None:
    _generated, private_key, _public_key = _keypair(tmp_path)
    receipt = _arbitrary_receipt(tmp_path)
    signature = tmp_path / "verification-receipt.sig.json"
    signature.write_text("existing", encoding="utf-8")

    with pytest.raises(PortableEvidenceVerificationError, match="refusing to overwrite"):
        sign_evidence_verification_receipt(
            receipt,
            private_key_path=private_key,
            output_path=signature,
        )
    assert signature.read_text(encoding="utf-8") == "existing"

    real_receipt = tmp_path / "real-receipt.json"
    real_receipt.write_bytes(b"receipt\n")
    linked_receipt = tmp_path / "linked-receipt.json"
    linked_receipt.symlink_to(real_receipt)
    with pytest.raises(PortableEvidenceVerificationError):
        sign_evidence_verification_receipt(
            linked_receipt,
            private_key_path=private_key,
            output_path=tmp_path / "must-not-exist.sig.json",
        )
    assert not (tmp_path / "must-not-exist.sig.json").exists()


def test_cli_sign_and_verify_commands_are_additive(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    generated, private_key, public_key = _keypair(tmp_path)
    receipt = _arbitrary_receipt(tmp_path)
    signature = tmp_path / "verification-receipt.sig.json"

    help_text = build_parser().format_help()
    for command in (
        "verify-evidence-capsule",
        "reconcile-evidence-receipt",
        "sign-evidence-receipt",
        "verify-evidence-receipt-signature",
    ):
        assert command in help_text

    assert (
        cli_main(
            [
                "sign-evidence-receipt",
                str(receipt),
                "--private-key",
                str(private_key),
                "--output",
                str(signature),
            ]
        )
        == 0
    )
    signed_output = capsys.readouterr().out.strip()
    assert signed_output.startswith("receipt-signed: ")
    assert generated.key_fingerprint in signed_output

    assert (
        cli_main(
            [
                "verify-evidence-receipt-signature",
                str(receipt),
                "--signature",
                str(signature),
                "--public-key",
                str(public_key),
            ]
        )
        == 0
    )
    verified_output = capsys.readouterr().out.strip()
    assert verified_output.startswith("valid: receipt_signature=verified ")
    assert generated.key_fingerprint in verified_output


def test_m60_module_has_no_receipt_semantic_reconciliation_network_or_time_surface() -> None:
    source = Path(signing_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "render_verification_receipt",
        "reconcile_evidence_verification_receipt",
        "verify_evidence_capsule",
        "AppServer",
        "urllib",
        "requests",
        "httpx",
        "socket",
        "datetime",
        "time.time",
    ):
        assert forbidden not in source
    assert "_load_private_key" in source
    assert "_load_public_key" in source
    assert "_public_key_fingerprint" in source
    assert "_signature_bytes" in source
    assert "_exclusive_write" in source
