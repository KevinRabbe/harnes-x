"""M55 deterministic byte-preserving signed-manifest capsule rendering."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

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
