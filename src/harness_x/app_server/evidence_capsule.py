"""M55 deterministic byte-preserving signed-manifest capsule rendering."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from harness_x.evidence_signing import (
    EvidenceSignatureEnvelope,
    EvidenceSigningError,
    _envelope_bytes,
    _signature_bytes,
)

from .evidence_manifest import RenderedEvidenceManifest
from .evidence_signature import RenderedEvidenceSignature

CAPSULE_SCHEMA_VERSION = "app-signed-manifest-capsule-v1"
CAPSULE_FILENAME = "session-evidence-signed-manifest-pair.json"


class EvidenceCapsuleRenderError(RuntimeError):
    """Exact manifest/signature bytes disagree with the M55 capsule contract."""


@dataclass(frozen=True, slots=True)
class RenderedEvidenceCapsule:
    """One deterministic response containing exact M43 and M52 byte strings."""

    payload: bytes
    source_bytes: int
    source_sha256: str
    manifest_sha256: str
    key_fingerprint: str
    algorithm: str = "ed25519"


def _base64url_without_padding(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _reject_signature_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceCapsuleRenderError(
                f"rendered signature contains duplicate envelope key: {key}"
            )
        result[key] = value
    return result


def _validate_exact_signature_payload(
    signature: RenderedEvidenceSignature,
    *,
    manifest_sha256: str,
) -> None:
    try:
        text = signature.payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EvidenceCapsuleRenderError(
            "rendered signature payload is not valid UTF-8"
        ) from exc
    try:
        raw = json.loads(text, object_pairs_hook=_reject_signature_duplicate_keys)
    except EvidenceCapsuleRenderError:
        raise
    except json.JSONDecodeError as exc:
        raise EvidenceCapsuleRenderError(
            f"rendered signature payload is not valid JSON: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise EvidenceCapsuleRenderError(
            "rendered signature payload JSON root is not an object"
        )
    try:
        envelope = EvidenceSignatureEnvelope.model_validate(raw)
    except ValidationError as exc:
        raise EvidenceCapsuleRenderError(
            f"rendered signature payload does not satisfy frozen M52 envelope: {exc}"
        ) from exc
    try:
        _signature_bytes(envelope.signature)
    except EvidenceSigningError as exc:
        raise EvidenceCapsuleRenderError(str(exc)) from exc
    if _envelope_bytes(envelope) != signature.payload:
        raise EvidenceCapsuleRenderError(
            "rendered signature payload is not the canonical frozen M52 serialization"
        )
    if envelope.algorithm != signature.algorithm:
        raise EvidenceCapsuleRenderError(
            "rendered signature payload algorithm disagrees with rendered metadata"
        )
    if envelope.key_fingerprint != signature.key_fingerprint:
        raise EvidenceCapsuleRenderError(
            "rendered signature payload key fingerprint disagrees with rendered metadata"
        )
    if envelope.manifest_sha256 != manifest_sha256:
        raise EvidenceCapsuleRenderError(
            "rendered signature payload refers to different manifest bytes"
        )


def render_signed_manifest_capsule(
    manifest: RenderedEvidenceManifest,
    signature: RenderedEvidenceSignature,
) -> RenderedEvidenceCapsule:
    """Wrap exact rendered manifest/signature bytes without reconstructing either payload."""

    observed_manifest_sha256 = hashlib.sha256(manifest.payload).hexdigest()
    if manifest.source_bytes != len(manifest.payload):
        raise EvidenceCapsuleRenderError(
            "rendered manifest byte count does not match exact manifest payload"
        )
    if manifest.source_sha256 != observed_manifest_sha256:
        raise EvidenceCapsuleRenderError(
            "rendered manifest SHA-256 does not match exact manifest payload"
        )
    if signature.source_bytes != len(signature.payload):
        raise EvidenceCapsuleRenderError(
            "rendered signature byte count does not match exact signature payload"
        )
    if signature.manifest_sha256 != observed_manifest_sha256:
        raise EvidenceCapsuleRenderError(
            "rendered signature refers to different manifest bytes"
        )
    if signature.algorithm != "ed25519":
        raise EvidenceCapsuleRenderError("rendered signature uses an unexpected algorithm")
    _validate_exact_signature_payload(
        signature,
        manifest_sha256=observed_manifest_sha256,
    )

    material = {
        "algorithm": signature.algorithm,
        "key_fingerprint": signature.key_fingerprint,
        "manifest_payload": _base64url_without_padding(manifest.payload),
        "manifest_sha256": observed_manifest_sha256,
        "schema_version": CAPSULE_SCHEMA_VERSION,
        "signature_payload": _base64url_without_padding(signature.payload),
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
    return RenderedEvidenceCapsule(
        payload=payload,
        source_bytes=len(payload),
        source_sha256=hashlib.sha256(payload).hexdigest(),
        manifest_sha256=observed_manifest_sha256,
        key_fingerprint=signature.key_fingerprint,
        algorithm=signature.algorithm,
    )
