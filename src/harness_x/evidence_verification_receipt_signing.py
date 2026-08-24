"""M60 detached Ed25519 signatures for exact M58 verification-receipt bytes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .evidence_signing import (
    EvidenceSigningError,
    _crypto,
    _exclusive_write,
    _load_private_key,
    _load_public_key,
    _public_key_fingerprint,
    _signature_bytes,
    _signature_text,
)
from .evidence_verification import PortableEvidenceVerificationError, _bounded_regular_file
from .evidence_verification_receipt_reconciliation import (
    MAX_EVIDENCE_VERIFICATION_RECEIPT_BYTES,
)

MAX_EVIDENCE_VERIFICATION_RECEIPT_SIGNATURE_BYTES = 64 * 1024
RECEIPT_SIGNATURE_SCHEMA_VERSION = "app-evidence-verification-receipt-signature-v1"


class EvidenceVerificationReceiptSignatureError(PortableEvidenceVerificationError):
    """Detached receipt signature input/output is malformed or does not verify."""


class EvidenceVerificationReceiptSignatureEnvelope(BaseModel):
    """Strict detached signature envelope for exact verification-receipt bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["app-evidence-verification-receipt-signature-v1"] = (
        RECEIPT_SIGNATURE_SCHEMA_VERSION
    )
    algorithm: Literal["ed25519"] = "ed25519"
    key_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


@dataclass(frozen=True, slots=True)
class SignedEvidenceVerificationReceipt:
    output_path: str
    receipt_sha256: str
    key_fingerprint: str

    def summary(self) -> str:
        return (
            "receipt-signed: "
            f"receipt_sha256={self.receipt_sha256} "
            f"key={self.key_fingerprint} "
            f"output={self.output_path}"
        )


@dataclass(frozen=True, slots=True)
class VerifiedEvidenceVerificationReceiptSignature:
    receipt_sha256: str
    key_fingerprint: str

    def summary(self) -> str:
        return (
            "valid: receipt_signature=verified "
            f"receipt_sha256={self.receipt_sha256} "
            f"key={self.key_fingerprint}"
        )


def _receipt_signature_envelope_bytes(
    envelope: EvidenceVerificationReceiptSignatureEnvelope,
) -> bytes:
    return (
        json.dumps(
            envelope.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceVerificationReceiptSignatureError(
                f"receipt signature JSON contains duplicate object key: {key}"
            )
        result[key] = value
    return result


def _load_receipt_signature_envelope(
    signature_path: str | Path,
) -> EvidenceVerificationReceiptSignatureEnvelope:
    source = _bounded_regular_file(
        signature_path,
        maximum_bytes=MAX_EVIDENCE_VERIFICATION_RECEIPT_SIGNATURE_BYTES,
    )
    try:
        text = source.payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EvidenceVerificationReceiptSignatureError(
            "receipt signature envelope is not valid UTF-8"
        ) from exc
    try:
        raw = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except EvidenceVerificationReceiptSignatureError:
        raise
    except json.JSONDecodeError as exc:
        raise EvidenceVerificationReceiptSignatureError(
            f"receipt signature envelope is not valid JSON: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise EvidenceVerificationReceiptSignatureError(
            "receipt signature envelope JSON root must be an object"
        )
    try:
        envelope = EvidenceVerificationReceiptSignatureEnvelope.model_validate(raw)
    except ValidationError as exc:
        raise EvidenceVerificationReceiptSignatureError(
            f"receipt signature envelope does not satisfy {RECEIPT_SIGNATURE_SCHEMA_VERSION}: {exc}"
        ) from exc
    try:
        _signature_bytes(envelope.signature)
    except EvidenceSigningError as exc:
        raise EvidenceVerificationReceiptSignatureError(str(exc)) from exc
    if _receipt_signature_envelope_bytes(envelope) != source.payload:
        raise EvidenceVerificationReceiptSignatureError(
            "receipt signature envelope is not the canonical M60 serialization"
        )
    return envelope


def sign_evidence_verification_receipt(
    receipt_path: str | Path,
    *,
    private_key_path: str | Path,
    output_path: str | Path,
) -> SignedEvidenceVerificationReceipt:
    """Sign exact bounded receipt bytes without asserting receipt schema or truth."""

    receipt = _bounded_regular_file(
        receipt_path,
        maximum_bytes=MAX_EVIDENCE_VERIFICATION_RECEIPT_BYTES,
    )
    private_key = _load_private_key(private_key_path)
    public_key = private_key.public_key()
    envelope = EvidenceVerificationReceiptSignatureEnvelope(
        key_fingerprint=_public_key_fingerprint(public_key),
        receipt_sha256=receipt.source_sha256,
        signature=_signature_text(private_key.sign(receipt.payload)),
    )
    output = _exclusive_write(
        output_path,
        _receipt_signature_envelope_bytes(envelope),
        mode=0o644,
    )
    return SignedEvidenceVerificationReceipt(
        output_path=output,
        receipt_sha256=receipt.source_sha256,
        key_fingerprint=envelope.key_fingerprint,
    )


def verify_evidence_verification_receipt_signature(
    receipt_path: str | Path,
    *,
    signature_path: str | Path,
    public_key_path: str | Path,
) -> VerifiedEvidenceVerificationReceiptSignature:
    """Verify a detached M60 signature over exact bounded receipt bytes."""

    receipt = _bounded_regular_file(
        receipt_path,
        maximum_bytes=MAX_EVIDENCE_VERIFICATION_RECEIPT_BYTES,
    )
    envelope = _load_receipt_signature_envelope(signature_path)
    if envelope.receipt_sha256 != receipt.source_sha256:
        raise EvidenceVerificationReceiptSignatureError(
            "receipt signature envelope SHA-256 does not match receipt bytes"
        )

    public_key = _load_public_key(public_key_path)
    key_fingerprint = _public_key_fingerprint(public_key)
    if envelope.key_fingerprint != key_fingerprint:
        raise EvidenceVerificationReceiptSignatureError(
            "receipt signature envelope key fingerprint does not match public key"
        )

    InvalidSignature, _serialization, _private_type, _public_type = _crypto()
    try:
        public_key.verify(_signature_bytes(envelope.signature), receipt.payload)
    except InvalidSignature as exc:
        raise EvidenceVerificationReceiptSignatureError(
            "Ed25519 receipt signature does not verify for exact receipt bytes"
        ) from exc

    return VerifiedEvidenceVerificationReceiptSignature(
        receipt_sha256=receipt.source_sha256,
        key_fingerprint=key_fingerprint,
    )
