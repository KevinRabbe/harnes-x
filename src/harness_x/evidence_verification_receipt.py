"""M58 deterministic unsigned receipts for successful frozen M57 verification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .evidence_capsule_extraction import MANIFEST_FILENAME, SIGNATURE_FILENAME
from .evidence_capsule_verification import VerifiedEvidenceCapsule
from .evidence_signing import _exclusive_write
from .evidence_verification import PortableEvidenceVerificationError

VERIFICATION_RECEIPT_SCHEMA_VERSION = "app-evidence-verification-receipt-v1"


class EvidenceVerificationReceiptError(PortableEvidenceVerificationError):
    """A successful frozen verification result cannot be rendered as an M58 receipt."""


@dataclass(frozen=True, slots=True)
class RenderedEvidenceVerificationReceipt:
    """Canonical unsigned M58 verification-receipt bytes."""

    payload: bytes
    source_bytes: int
    source_sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedEvidenceCapsuleWithReceipt:
    """Successful frozen M57 result plus one persisted deterministic receipt."""

    verification: VerifiedEvidenceCapsule
    receipt_path: str
    receipt_sha256: str

    def summary(self) -> str:
        return (
            f"{self.verification.summary()} "
            f"receipt={self.receipt_path} "
            f"receipt_sha256={self.receipt_sha256}"
        )


def render_verification_receipt(
    result: VerifiedEvidenceCapsule,
) -> RenderedEvidenceVerificationReceipt:
    """Render only metadata already established by frozen M56/M57/M52 success."""

    verification = result.verification
    if verification.signature_status != "verified" or verification.key_fingerprint is None:
        raise EvidenceVerificationReceiptError(
            "verification receipt requires a successful frozen M52 signature verification"
        )

    with_snapshot = verification.base
    base = with_snapshot.base
    extraction = result.extraction
    if extraction.manifest_sha256 != base.manifest_sha256:
        raise EvidenceVerificationReceiptError(
            "extracted manifest SHA-256 disagrees with frozen verification result"
        )
    if extraction.key_fingerprint != verification.key_fingerprint:
        raise EvidenceVerificationReceiptError(
            "extracted key fingerprint disagrees with frozen verification result"
        )
    if Path(extraction.manifest_path).name != MANIFEST_FILENAME:
        raise EvidenceVerificationReceiptError(
            "extracted manifest path does not use the frozen M43 filename"
        )
    if Path(extraction.signature_path).name != SIGNATURE_FILENAME:
        raise EvidenceVerificationReceiptError(
            "extracted signature path does not use the frozen M52 filename"
        )

    material = {
        "algorithm": "ed25519",
        "capsule_status": "validated",
        "key_fingerprint": verification.key_fingerprint,
        "lifecycle_events": base.lifecycle_events,
        "lifecycle_status": base.lifecycle_status,
        "manifest_bytes": base.manifest_bytes,
        "manifest_filename": MANIFEST_FILENAME,
        "manifest_sha256": base.manifest_sha256,
        "report_status": base.report_status,
        "schema_version": VERIFICATION_RECEIPT_SCHEMA_VERSION,
        "session_id": base.session_id,
        "signature_filename": SIGNATURE_FILENAME,
        "signature_status": verification.signature_status,
        "snapshot_revision": with_snapshot.snapshot_revision,
        "snapshot_status": with_snapshot.snapshot_status,
        "trace_records": base.trace_records,
        "trace_status": base.trace_status,
    }
    payload = (
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        + b"\n"
    )
    return RenderedEvidenceVerificationReceipt(
        payload=payload,
        source_bytes=len(payload),
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )


def persist_verification_receipt(
    result: VerifiedEvidenceCapsule,
    *,
    receipt_path: str | Path,
) -> VerifiedEvidenceCapsuleWithReceipt:
    """Persist one unsigned receipt after the caller has obtained frozen M57 success."""

    rendered = render_verification_receipt(result)
    output = _exclusive_write(receipt_path, rendered.payload, mode=0o644)
    return VerifiedEvidenceCapsuleWithReceipt(
        verification=result,
        receipt_path=output,
        receipt_sha256=rendered.source_sha256,
    )
