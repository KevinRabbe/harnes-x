"""M57 orchestration of frozen M56 capsule extraction and frozen M52 verification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .evidence_capsule_extraction import ExtractedEvidenceCapsule, extract_evidence_capsule
from .evidence_signing import (
    PortableEvidenceVerificationWithSignature,
    verify_portable_evidence_with_signature,
)


@dataclass(frozen=True, slots=True)
class VerifiedEvidenceCapsule:
    """One successful M56 extraction followed by frozen M52 verification."""

    extraction: ExtractedEvidenceCapsule
    verification: PortableEvidenceVerificationWithSignature

    def summary(self) -> str:
        return (
            f"{self.verification.summary()} "
            "capsule=validated "
            f"extracted_manifest={self.extraction.manifest_path} "
            f"extracted_signature={self.extraction.signature_path}"
        )


def verify_evidence_capsule(
    capsule_path: str | Path,
    *,
    output_dir: str | Path,
    public_key_path: str | Path,
    snapshot_path: str | Path | None = None,
    lifecycle_path: str | Path | None = None,
    report_path: str | Path | None = None,
    trace_path: str | Path | None = None,
) -> VerifiedEvidenceCapsule:
    """Extract exact M55 bytes, then delegate all trust verification to frozen M52."""

    extraction = extract_evidence_capsule(
        capsule_path,
        output_dir=output_dir,
    )
    verification = verify_portable_evidence_with_signature(
        extraction.manifest_path,
        signature_path=extraction.signature_path,
        public_key_path=public_key_path,
        snapshot_path=snapshot_path,
        lifecycle_path=lifecycle_path,
        report_path=report_path,
        trace_path=trace_path,
    )
    return VerifiedEvidenceCapsule(
        extraction=extraction,
        verification=verification,
    )
