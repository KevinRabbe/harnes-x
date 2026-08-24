"""M56 offline extraction of exact M55 signed-manifest capsule payloads."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from harness_x.app_server.evidence_capsule import CAPSULE_SCHEMA_VERSION
from harness_x.app_server.evidence_manifest import render_terminal_evidence_manifest

from .evidence_signing import (
    MAX_EVIDENCE_SIGNATURE_BYTES,
    EvidenceSignatureEnvelope,
    EvidenceSigningError,
    _envelope_bytes,
    _exclusive_write,
    _safe_output_path,
    _signature_bytes,
)
from .evidence_verification import (
    MAX_EVIDENCE_MANIFEST_BYTES,
    BoundedEvidenceSource,
    PortableEvidenceVerificationError,
    _bounded_regular_file,
    _load_manifest,
)

MAX_EVIDENCE_CAPSULE_BYTES = 4 * 1024 * 1024
MANIFEST_FILENAME = "session-evidence-manifest.json"
SIGNATURE_FILENAME = "session-evidence-manifest.sig.json"
_EXPECTED_CAPSULE_KEYS = (
    "algorithm",
    "key_fingerprint",
    "manifest_payload",
    "manifest_sha256",
    "schema_version",
    "signature_payload",
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_KEY_FINGERPRINT_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_BASE64URL_RE = re.compile(r"[A-Za-z0-9_-]+\Z")


class EvidenceCapsuleExtractionError(PortableEvidenceVerificationError):
    """An M55 capsule cannot be safely validated or extracted."""


@dataclass(frozen=True, slots=True)
class ValidatedEvidenceCapsule:
    """Exact decoded M43/M52 byte strings retained from one validated capsule."""

    manifest_payload: bytes
    signature_payload: bytes
    manifest_sha256: str
    key_fingerprint: str
    algorithm: str = "ed25519"


@dataclass(frozen=True, slots=True)
class ExtractedEvidenceCapsule:
    """Successful fixed-name extraction result."""

    manifest_path: str
    signature_path: str
    manifest_sha256: str
    key_fingerprint: str

    def summary(self) -> str:
        return (
            "extracted: "
            f"manifest_sha256={self.manifest_sha256} "
            f"key={self.key_fingerprint} "
            f"manifest={self.manifest_path} "
            f"signature={self.signature_path}"
        )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceCapsuleExtractionError(
                f"capsule JSON contains duplicate object key: {key}"
            )
        result[key] = value
    return result


def _decode_canonical_base64url(value: object, *, field: str) -> bytes:
    if not isinstance(value, str) or _BASE64URL_RE.fullmatch(value) is None:
        raise EvidenceCapsuleExtractionError(
            f"capsule {field} is not canonical base64url-without-padding text"
        )
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(value + padding)
    except (binascii.Error, ValueError) as exc:
        raise EvidenceCapsuleExtractionError(
            f"capsule {field} is not valid base64url"
        ) from exc
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if not decoded or canonical != value:
        raise EvidenceCapsuleExtractionError(
            f"capsule {field} is not canonical base64url-without-padding text"
        )
    return decoded


def _canonical_capsule_bytes(raw: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            raw,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        + b"\n"
    )


def _load_signature_payload(payload: bytes) -> EvidenceSignatureEnvelope:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EvidenceCapsuleExtractionError(
            "embedded signature envelope is not valid UTF-8"
        ) from exc
    try:
        raw = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except EvidenceCapsuleExtractionError:
        raise
    except json.JSONDecodeError as exc:
        raise EvidenceCapsuleExtractionError(
            f"embedded signature envelope is not valid JSON: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise EvidenceCapsuleExtractionError(
            "embedded signature envelope JSON root must be an object"
        )
    try:
        envelope = EvidenceSignatureEnvelope.model_validate(raw)
    except ValidationError as exc:
        raise EvidenceCapsuleExtractionError(
            f"embedded signature does not satisfy app-evidence-signature-v1: {exc}"
        ) from exc
    try:
        _signature_bytes(envelope.signature)
    except EvidenceSigningError as exc:
        raise EvidenceCapsuleExtractionError(str(exc)) from exc
    if _envelope_bytes(envelope) != payload:
        raise EvidenceCapsuleExtractionError(
            "embedded signature is not the canonical frozen M52 envelope serialization"
        )
    return envelope


def validate_evidence_capsule(
    source: BoundedEvidenceSource,
) -> ValidatedEvidenceCapsule:
    """Validate one exact M55 capsule without making a signature-trust decision."""

    try:
        text = source.payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EvidenceCapsuleExtractionError("capsule is not valid UTF-8") from exc
    try:
        raw = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except EvidenceCapsuleExtractionError:
        raise
    except json.JSONDecodeError as exc:
        raise EvidenceCapsuleExtractionError(f"capsule is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise EvidenceCapsuleExtractionError("capsule JSON root must be an object")
    if tuple(sorted(raw)) != _EXPECTED_CAPSULE_KEYS:
        raise EvidenceCapsuleExtractionError(
            "capsule does not contain the exact app-signed-manifest-capsule-v1 fields"
        )
    if _canonical_capsule_bytes(raw) != source.payload:
        raise EvidenceCapsuleExtractionError(
            "capsule is not the canonical M55 serialization"
        )
    if raw["schema_version"] != CAPSULE_SCHEMA_VERSION:
        raise EvidenceCapsuleExtractionError("capsule has an unexpected schema version")
    if raw["algorithm"] != "ed25519":
        raise EvidenceCapsuleExtractionError("capsule has an unexpected signature algorithm")

    manifest_sha256 = raw["manifest_sha256"]
    if not isinstance(manifest_sha256, str) or _SHA256_RE.fullmatch(manifest_sha256) is None:
        raise EvidenceCapsuleExtractionError("capsule manifest SHA-256 is invalid")
    key_fingerprint = raw["key_fingerprint"]
    if (
        not isinstance(key_fingerprint, str)
        or _KEY_FINGERPRINT_RE.fullmatch(key_fingerprint) is None
    ):
        raise EvidenceCapsuleExtractionError("capsule key fingerprint is invalid")

    manifest_payload = _decode_canonical_base64url(
        raw["manifest_payload"],
        field="manifest_payload",
    )
    signature_payload = _decode_canonical_base64url(
        raw["signature_payload"],
        field="signature_payload",
    )
    if len(manifest_payload) > MAX_EVIDENCE_MANIFEST_BYTES:
        raise EvidenceCapsuleExtractionError(
            f"embedded manifest exceeds {MAX_EVIDENCE_MANIFEST_BYTES} byte limit"
        )
    if len(signature_payload) > MAX_EVIDENCE_SIGNATURE_BYTES:
        raise EvidenceCapsuleExtractionError(
            f"embedded signature exceeds {MAX_EVIDENCE_SIGNATURE_BYTES} byte limit"
        )

    observed_manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    if observed_manifest_sha256 != manifest_sha256:
        raise EvidenceCapsuleExtractionError(
            "embedded manifest bytes do not match capsule manifest SHA-256"
        )

    manifest_source = BoundedEvidenceSource(
        path=f"{source.path}#manifest_payload",
        payload=manifest_payload,
        source_bytes=len(manifest_payload),
        source_sha256=observed_manifest_sha256,
    )
    try:
        manifest = _load_manifest(manifest_source)
    except PortableEvidenceVerificationError as exc:
        raise EvidenceCapsuleExtractionError(str(exc)) from exc
    if render_terminal_evidence_manifest(manifest).payload != manifest_payload:
        raise EvidenceCapsuleExtractionError(
            "embedded manifest is not the canonical frozen M43 serialization"
        )

    envelope = _load_signature_payload(signature_payload)
    if envelope.algorithm != raw["algorithm"]:
        raise EvidenceCapsuleExtractionError(
            "embedded signature algorithm disagrees with capsule metadata"
        )
    if envelope.key_fingerprint != key_fingerprint:
        raise EvidenceCapsuleExtractionError(
            "embedded signature key fingerprint disagrees with capsule metadata"
        )
    if envelope.manifest_sha256 != manifest_sha256:
        raise EvidenceCapsuleExtractionError(
            "embedded signature refers to different manifest bytes"
        )

    return ValidatedEvidenceCapsule(
        manifest_payload=manifest_payload,
        signature_payload=signature_payload,
        manifest_sha256=manifest_sha256,
        key_fingerprint=key_fingerprint,
    )


def load_evidence_capsule(path: str | Path) -> ValidatedEvidenceCapsule:
    """Read and validate one bounded regular M55 capsule file."""

    try:
        source = _bounded_regular_file(path, maximum_bytes=MAX_EVIDENCE_CAPSULE_BYTES)
    except PortableEvidenceVerificationError as exc:
        raise EvidenceCapsuleExtractionError(str(exc)) from exc
    return validate_evidence_capsule(source)


def _safe_output_target(path: Path) -> Path:
    try:
        return _safe_output_path(path)
    except PortableEvidenceVerificationError as exc:
        raise EvidenceCapsuleExtractionError(str(exc)) from exc


def _rollback_created_manifest(path: str, original: BaseException) -> None:
    try:
        os.unlink(path)
    except OSError as rollback_exc:
        raise EvidenceCapsuleExtractionError(
            f"{original}; additionally failed to roll back newly created manifest output: "
            f"{path}: {rollback_exc}"
        ) from rollback_exc


def extract_evidence_capsule(
    capsule_path: str | Path,
    *,
    output_dir: str | Path,
) -> ExtractedEvidenceCapsule:
    """Validate one capsule and write its two exact embedded byte strings under fixed names."""

    validated = load_evidence_capsule(capsule_path)
    output_root = Path(output_dir).expanduser()
    manifest_target = _safe_output_target(output_root / MANIFEST_FILENAME)
    signature_target = _safe_output_target(output_root / SIGNATURE_FILENAME)
    if manifest_target == signature_target:
        raise EvidenceCapsuleExtractionError("capsule output targets must be distinct")
    for target in (manifest_target, signature_target):
        if os.path.lexists(target):
            raise EvidenceCapsuleExtractionError(
                f"refusing to overwrite existing output: {target}"
            )

    manifest_created: str | None = None
    try:
        manifest_created = _exclusive_write(
            manifest_target,
            validated.manifest_payload,
            mode=0o644,
        )
        signature_created = _exclusive_write(
            signature_target,
            validated.signature_payload,
            mode=0o644,
        )
    except PortableEvidenceVerificationError as exc:
        if manifest_created is not None:
            _rollback_created_manifest(manifest_created, exc)
        raise EvidenceCapsuleExtractionError(str(exc)) from exc
    except Exception as exc:
        if manifest_created is not None:
            _rollback_created_manifest(manifest_created, exc)
        raise EvidenceCapsuleExtractionError(f"capsule extraction failed: {exc}") from exc

    return ExtractedEvidenceCapsule(
        manifest_path=manifest_created,
        signature_path=signature_created,
        manifest_sha256=validated.manifest_sha256,
        key_fingerprint=validated.key_fingerprint,
    )
