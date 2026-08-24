from __future__ import annotations

from pathlib import Path

import pytest

from harness_x.evidence_signing import generate_evidence_keypair
from harness_x.evidence_verification import PortableEvidenceVerificationError
from harness_x.evidence_verification_receipt_signing import (
    sign_evidence_verification_receipt,
    verify_evidence_verification_receipt_signature,
)


def _keypair(tmp_path: Path):
    private_key = tmp_path / "evidence.private.pem"
    public_key = tmp_path / "evidence.public.pem"
    generate_evidence_keypair(
        private_key_path=private_key,
        public_key_path=public_key,
    )
    return private_key, public_key


def test_signing_rejects_symlink_private_key(tmp_path: Path) -> None:
    private_key, _public_key = _keypair(tmp_path)
    linked_private_key = tmp_path / "linked.private.pem"
    linked_private_key.symlink_to(private_key)
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b"receipt\n")
    output = tmp_path / "receipt.sig.json"

    with pytest.raises(PortableEvidenceVerificationError):
        sign_evidence_verification_receipt(
            receipt,
            private_key_path=linked_private_key,
            output_path=output,
        )
    assert not output.exists()


def test_signing_rejects_symlink_output_without_touching_target(tmp_path: Path) -> None:
    private_key, _public_key = _keypair(tmp_path)
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b"receipt\n")
    target = tmp_path / "target.sig.json"
    target.write_text("sentinel", encoding="utf-8")
    linked_output = tmp_path / "linked.sig.json"
    linked_output.symlink_to(target)

    with pytest.raises(PortableEvidenceVerificationError, match="refusing to overwrite"):
        sign_evidence_verification_receipt(
            receipt,
            private_key_path=private_key,
            output_path=linked_output,
        )
    assert target.read_text(encoding="utf-8") == "sentinel"


def test_verification_rejects_symlink_signature_and_public_key_inputs(tmp_path: Path) -> None:
    private_key, public_key = _keypair(tmp_path)
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b"receipt\n")
    signature = tmp_path / "receipt.sig.json"
    sign_evidence_verification_receipt(
        receipt,
        private_key_path=private_key,
        output_path=signature,
    )

    linked_signature = tmp_path / "linked.sig.json"
    linked_signature.symlink_to(signature)
    with pytest.raises(PortableEvidenceVerificationError):
        verify_evidence_verification_receipt_signature(
            receipt,
            signature_path=linked_signature,
            public_key_path=public_key,
        )

    linked_public_key = tmp_path / "linked.public.pem"
    linked_public_key.symlink_to(public_key)
    with pytest.raises(PortableEvidenceVerificationError):
        verify_evidence_verification_receipt_signature(
            receipt,
            signature_path=signature,
            public_key_path=linked_public_key,
        )
