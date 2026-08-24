"""M59 exact-byte reconciliation of M58 receipts against fresh frozen verification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .evidence_capsule_verification import VerifiedEvidenceCapsule, verify_evidence_capsule
from .evidence_verification import PortableEvidenceVerificationError, _bounded_regular_file
from .evidence_verification_receipt import render_verification_receipt

MAX_EVIDENCE_VERIFICATION_RECEIPT_BYTES = 64 * 1024


class EvidenceVerificationReceiptReconciliationError(PortableEvidenceVerificationError):
    """A supplied M58 receipt does not reconcile with a fresh frozen verification result."""


@dataclass(frozen=True, slots=True)
class ReconciledEvidenceVerificationReceipt:
    """One supplied receipt reconciled against one fresh frozen M57 result."""

    verification: VerifiedEvidenceCapsule
    receipt_path: str
    receipt_sha256: str

    def summary(self) -> str:
        return (
            f"{self.verification.summary()} "
            "receipt=reconciled "
            f"receipt_path={self.receipt_path} "
            f"receipt_sha256={self.receipt_sha256}"
        )


def reconcile_evidence_verification_receipt(
    receipt_path: str | Path,
    capsule_path: str | Path,
    *,
    output_dir: str | Path,
    public_key_path: str | Path,
    snapshot_path: str | Path | None = None,
    lifecycle_path: str | Path | None = None,
    report_path: str | Path | None = None,
    trace_path: str | Path | None = None,
) -> ReconciledEvidenceVerificationReceipt:
    """Require supplied receipt bytes to equal frozen M58 output for fresh M57 success."""

    receipt = _bounded_regular_file(
        receipt_path,
        maximum_bytes=MAX_EVIDENCE_VERIFICATION_RECEIPT_BYTES,
    )
    verification = verify_evidence_capsule(
        capsule_path,
        output_dir=output_dir,
        public_key_path=public_key_path,
        snapshot_path=snapshot_path,
        lifecycle_path=lifecycle_path,
        report_path=report_path,
        trace_path=trace_path,
    )
    expected = render_verification_receipt(verification)
    if receipt.payload != expected.payload:
        raise EvidenceVerificationReceiptReconciliationError(
            "verification receipt bytes do not match fresh frozen verification result"
        )

    return ReconciledEvidenceVerificationReceipt(
        verification=verification,
        receipt_path=receipt.path,
        receipt_sha256=receipt.source_sha256,
    )
