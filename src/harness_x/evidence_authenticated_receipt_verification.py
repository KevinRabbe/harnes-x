"""M61 composition of frozen M60 receipt authentication and frozen M59 reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .evidence_verification import PortableEvidenceVerificationError
from .evidence_verification_receipt_reconciliation import (
    ReconciledEvidenceVerificationReceipt,
    reconcile_evidence_verification_receipt,
)
from .evidence_verification_receipt_signing import (
    VerifiedEvidenceVerificationReceiptSignature,
    verify_evidence_verification_receipt_signature,
)


class AuthenticatedEvidenceReceiptVerificationError(PortableEvidenceVerificationError):
    """Frozen receipt authentication and reconciliation do not identify the same bytes."""


@dataclass(frozen=True, slots=True)
class AuthenticatedEvidenceVerificationReceipt:
    """One receipt accepted by frozen M60 and frozen M59 for the same byte identity."""

    reconciliation: ReconciledEvidenceVerificationReceipt
    authentication: VerifiedEvidenceVerificationReceiptSignature

    def summary(self) -> str:
        return (
            f"{self.reconciliation.summary()} "
            "receipt_authentication=verified "
            f"receipt_authentication_key={self.authentication.key_fingerprint}"
        )


def verify_authenticated_evidence_receipt(
    receipt_path: str | Path,
    capsule_path: str | Path,
    *,
    receipt_signature_path: str | Path,
    receipt_public_key_path: str | Path,
    evidence_public_key_path: str | Path,
    output_dir: str | Path,
    snapshot_path: str | Path | None = None,
    lifecycle_path: str | Path | None = None,
    report_path: str | Path | None = None,
    trace_path: str | Path | None = None,
) -> AuthenticatedEvidenceVerificationReceipt:
    """Authenticate receipt bytes first, then reconcile the same byte identity freshly."""

    authentication = verify_evidence_verification_receipt_signature(
        receipt_path,
        signature_path=receipt_signature_path,
        public_key_path=receipt_public_key_path,
    )
    reconciliation = reconcile_evidence_verification_receipt(
        receipt_path,
        capsule_path,
        output_dir=output_dir,
        public_key_path=evidence_public_key_path,
        snapshot_path=snapshot_path,
        lifecycle_path=lifecycle_path,
        report_path=report_path,
        trace_path=trace_path,
    )
    if authentication.receipt_sha256 != reconciliation.receipt_sha256:
        raise AuthenticatedEvidenceReceiptVerificationError(
            "verification receipt changed between authentication and reconciliation reads"
        )

    return AuthenticatedEvidenceVerificationReceipt(
        reconciliation=reconciliation,
        authentication=authentication,
    )
