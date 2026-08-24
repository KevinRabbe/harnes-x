"""M62 orchestration of frozen M57 verification, M58 receipt export, and M60 signing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .evidence_capsule_verification import verify_evidence_capsule
from .evidence_verification import PortableEvidenceVerificationError
from .evidence_verification_receipt import (
    VerifiedEvidenceCapsuleWithReceipt,
    persist_verification_receipt,
)
from .evidence_verification_receipt_signing import (
    SignedEvidenceVerificationReceipt,
    sign_evidence_verification_receipt,
)


class SignedEvidenceReceiptExportError(PortableEvidenceVerificationError):
    """Frozen M58 persistence and frozen M60 signing did not identify the same receipt bytes."""


@dataclass(frozen=True, slots=True)
class VerifiedSignedEvidenceReceiptExport:
    """Fresh frozen evidence verification with one persisted and signed receipt."""

    receipt: VerifiedEvidenceCapsuleWithReceipt
    signature: SignedEvidenceVerificationReceipt

    def summary(self) -> str:
        return (
            f"{self.receipt.summary()} "
            "receipt_signature=signed "
            f"receipt_signature_key={self.signature.key_fingerprint} "
            f"receipt_signature_output={self.signature.output_path}"
        )


def export_signed_evidence_receipt(
    capsule_path: str | Path,
    *,
    output_dir: str | Path,
    evidence_public_key_path: str | Path,
    receipt_path: str | Path,
    receipt_private_key_path: str | Path,
    receipt_signature_path: str | Path,
    snapshot_path: str | Path | None = None,
    lifecycle_path: str | Path | None = None,
    report_path: str | Path | None = None,
    trace_path: str | Path | None = None,
) -> VerifiedSignedEvidenceReceiptExport:
    """Freshly verify, persist the canonical receipt, sign it, and pin its byte identity."""

    verification = verify_evidence_capsule(
        capsule_path,
        output_dir=output_dir,
        public_key_path=evidence_public_key_path,
        snapshot_path=snapshot_path,
        lifecycle_path=lifecycle_path,
        report_path=report_path,
        trace_path=trace_path,
    )
    receipt = persist_verification_receipt(
        verification,
        receipt_path=receipt_path,
    )
    signature = sign_evidence_verification_receipt(
        receipt.receipt_path,
        private_key_path=receipt_private_key_path,
        output_path=receipt_signature_path,
    )
    if receipt.receipt_sha256 != signature.receipt_sha256:
        raise SignedEvidenceReceiptExportError(
            "verification receipt changed between persistence and detached signing reads"
        )

    return VerifiedSignedEvidenceReceiptExport(
        receipt=receipt,
        signature=signature,
    )
