"""M53 in-memory rendering of frozen M52 detached manifest signatures."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness_x.evidence_signing import (
    EvidenceSignatureEnvelope,
    EvidenceSigningError,
    _envelope_bytes,
    _load_private_key,
    _public_key_fingerprint,
    _signature_text,
)


@dataclass(frozen=True, slots=True)
class RenderedEvidenceSignature:
    """Deterministic detached signature response bytes for one exact manifest."""

    payload: bytes
    source_bytes: int
    manifest_sha256: str
    key_fingerprint: str
    algorithm: str = "ed25519"


@dataclass(frozen=True, slots=True)
class EvidenceManifestSigner:
    """Process-memory Ed25519 signer loaded once from the operator-selected key."""

    private_key: Any
    key_fingerprint: str

    @classmethod
    def from_private_key_path(cls, path: str | Path) -> "EvidenceManifestSigner":
        private_key = _load_private_key(path)
        return cls(
            private_key=private_key,
            key_fingerprint=_public_key_fingerprint(private_key.public_key()),
        )

    def render(
        self,
        manifest_payload: bytes,
        *,
        manifest_sha256: str | None = None,
    ) -> RenderedEvidenceSignature:
        """Sign exact rendered manifest bytes using the frozen M52 envelope."""

        computed_sha256 = hashlib.sha256(manifest_payload).hexdigest()
        if manifest_sha256 is not None and manifest_sha256 != computed_sha256:
            raise EvidenceSigningError(
                "rendered manifest SHA-256 does not match exact manifest payload"
            )
        signature = self.private_key.sign(manifest_payload)
        envelope = EvidenceSignatureEnvelope(
            key_fingerprint=self.key_fingerprint,
            manifest_sha256=computed_sha256,
            signature=_signature_text(signature),
        )
        payload = _envelope_bytes(envelope)
        return RenderedEvidenceSignature(
            payload=payload,
            source_bytes=len(payload),
            manifest_sha256=computed_sha256,
            key_fingerprint=self.key_fingerprint,
        )
