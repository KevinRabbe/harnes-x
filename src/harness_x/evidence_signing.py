"""M52 detached Ed25519 signatures for exact portable evidence-manifest bytes."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .evidence_verification import (
    MAX_EVIDENCE_MANIFEST_BYTES,
    PortableEvidenceVerificationError,
    _bounded_regular_file,
)
from .snapshot_verification import (
    PortableEvidenceVerificationWithSnapshot,
    verify_portable_evidence_with_snapshot,
)

MAX_EVIDENCE_KEY_BYTES = 16 * 1024
MAX_EVIDENCE_SIGNATURE_BYTES = 64 * 1024
_KEY_FINGERPRINT_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_BASE64URL_SIGNATURE_RE = re.compile(r"[A-Za-z0-9_-]{86}\Z")


class EvidenceSigningError(PortableEvidenceVerificationError):
    """Detached evidence signing input/output is invalid or cryptographic work failed."""


class EvidenceSignatureEnvelope(BaseModel):
    """Strict portable detached-signature envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["app-evidence-signature-v1"] = "app-evidence-signature-v1"
    algorithm: Literal["ed25519"] = "ed25519"
    key_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


@dataclass(frozen=True, slots=True)
class GeneratedEvidenceKeypair:
    private_key_path: str
    public_key_path: str
    key_fingerprint: str

    def summary(self) -> str:
        return (
            "generated: "
            f"key={self.key_fingerprint} "
            f"private_key={self.private_key_path} "
            f"public_key={self.public_key_path}"
        )


@dataclass(frozen=True, slots=True)
class SignedEvidenceManifest:
    output_path: str
    manifest_sha256: str
    key_fingerprint: str

    def summary(self) -> str:
        return (
            "signed: "
            f"manifest_sha256={self.manifest_sha256} "
            f"key={self.key_fingerprint} "
            f"output={self.output_path}"
        )


@dataclass(frozen=True, slots=True)
class PortableEvidenceVerificationWithSignature:
    base: PortableEvidenceVerificationWithSnapshot
    signature_status: str
    key_fingerprint: str | None

    def summary(self) -> str:
        if self.signature_status == "not_supplied":
            return self.base.summary()
        key = "none" if self.key_fingerprint is None else self.key_fingerprint
        return f"{self.base.summary()} signature={self.signature_status} key={key}"


def _crypto():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError as exc:  # pragma: no cover - exercised without optional extra
        raise EvidenceSigningError(
            "Ed25519 evidence signing requires the optional evidence-signing extra"
        ) from exc
    return InvalidSignature, serialization, Ed25519PrivateKey, Ed25519PublicKey


def _public_key_fingerprint(public_key: Any) -> str:
    _invalid, serialization, _private_type, _public_type = _crypto()
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if len(raw) != 32:
        raise EvidenceSigningError("Ed25519 public key does not encode to 32 raw bytes")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _load_private_key(path: str | Path):
    _invalid, serialization, Ed25519PrivateKey, _public_type = _crypto()
    source = _bounded_regular_file(path, maximum_bytes=MAX_EVIDENCE_KEY_BYTES)
    try:
        key = serialization.load_pem_private_key(source.payload, password=None)
    except (TypeError, ValueError) as exc:
        raise EvidenceSigningError("private key is not valid unencrypted PEM") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise EvidenceSigningError("private key is not an Ed25519 private key")
    return key


def _load_public_key(path: str | Path):
    _invalid, serialization, _private_type, Ed25519PublicKey = _crypto()
    source = _bounded_regular_file(path, maximum_bytes=MAX_EVIDENCE_KEY_BYTES)
    try:
        key = serialization.load_pem_public_key(source.payload)
    except (TypeError, ValueError) as exc:
        raise EvidenceSigningError("public key is not valid PEM") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise EvidenceSigningError("public key is not an Ed25519 public key")
    return key


def _safe_output_path(path: str | Path) -> Path:
    supplied = Path(path).expanduser()
    lexical = Path(os.path.abspath(os.fspath(supplied)))
    parent = lexical.parent
    try:
        parent_metadata = os.lstat(parent)
    except OSError as exc:
        raise EvidenceSigningError(f"output parent is unavailable: {parent}: {exc}") from exc
    if not stat.S_ISDIR(parent_metadata.st_mode):
        raise EvidenceSigningError(f"output parent is not a directory: {parent}")
    try:
        resolved_parent = parent.resolve(strict=True)
    except OSError as exc:
        raise EvidenceSigningError(f"cannot resolve output parent: {parent}: {exc}") from exc
    if resolved_parent != parent:
        raise EvidenceSigningError(
            f"output path resolves through symbolic-link parent substitution: {parent}"
        )
    return lexical


def _exclusive_write(path: str | Path, payload: bytes, *, mode: int) -> str:
    lexical = _safe_output_path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    created = False
    failure: EvidenceSigningError | None = None
    try:
        descriptor = os.open(lexical, flags, mode)
        created = True
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise EvidenceSigningError(f"output is not a regular file: {lexical}")
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            written = handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if written != len(payload):
            raise EvidenceSigningError(f"short write while creating output: {lexical}")
    except FileExistsError:
        failure = EvidenceSigningError(f"refusing to overwrite existing output: {lexical}")
    except EvidenceSigningError as exc:
        failure = exc
    except OSError as exc:
        failure = EvidenceSigningError(f"cannot create output: {lexical}: {exc}")
    except Exception as exc:  # defensive cleanup for unexpected local I/O failures
        failure = EvidenceSigningError(f"cannot create output: {lexical}: {exc}")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if failure is not None:
        if created:
            try:
                os.unlink(lexical)
            except OSError:
                pass
        raise failure
    if not created:
        raise EvidenceSigningError(f"output was not created: {lexical}")
    return str(lexical)


def generate_evidence_keypair(
    *,
    private_key_path: str | Path,
    public_key_path: str | Path,
) -> GeneratedEvidenceKeypair:
    _invalid, serialization, Ed25519PrivateKey, _public_type = _crypto()
    private_target = _safe_output_path(private_key_path)
    public_target = _safe_output_path(public_key_path)
    if private_target == public_target:
        raise EvidenceSigningError("private and public key outputs must be different paths")
    if os.path.lexists(private_target):
        raise EvidenceSigningError(f"refusing to overwrite existing output: {private_target}")
    if os.path.lexists(public_target):
        raise EvidenceSigningError(f"refusing to overwrite existing output: {public_target}")

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_created: str | None = None
    try:
        private_created = _exclusive_write(private_target, private_pem, mode=0o600)
        public_created = _exclusive_write(public_target, public_pem, mode=0o644)
    except Exception:
        if private_created is not None:
            try:
                os.unlink(private_created)
            except OSError:
                pass
        raise

    return GeneratedEvidenceKeypair(
        private_key_path=private_created,
        public_key_path=public_created,
        key_fingerprint=_public_key_fingerprint(public_key),
    )


def _signature_text(signature: bytes) -> str:
    return base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


def _signature_bytes(value: str) -> bytes:
    if _BASE64URL_SIGNATURE_RE.fullmatch(value) is None:
        raise EvidenceSigningError("signature envelope contains invalid Ed25519 base64url text")
    try:
        decoded = base64.urlsafe_b64decode(value + "==")
    except Exception as exc:
        raise EvidenceSigningError("signature envelope contains invalid base64url") from exc
    if len(decoded) != 64 or _signature_text(decoded) != value:
        raise EvidenceSigningError("signature envelope contains non-canonical Ed25519 signature")
    return decoded


def _envelope_bytes(envelope: EvidenceSignatureEnvelope) -> bytes:
    return (
        json.dumps(
            envelope.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def sign_evidence_manifest(
    manifest_path: str | Path,
    *,
    private_key_path: str | Path,
    output_path: str | Path,
) -> SignedEvidenceManifest:
    manifest = _bounded_regular_file(
        manifest_path,
        maximum_bytes=MAX_EVIDENCE_MANIFEST_BYTES,
    )
    private_key = _load_private_key(private_key_path)
    public_key = private_key.public_key()
    signature = private_key.sign(manifest.payload)
    envelope = EvidenceSignatureEnvelope(
        key_fingerprint=_public_key_fingerprint(public_key),
        manifest_sha256=manifest.source_sha256,
        signature=_signature_text(signature),
    )
    output = _exclusive_write(output_path, _envelope_bytes(envelope), mode=0o644)
    return SignedEvidenceManifest(
        output_path=output,
        manifest_sha256=manifest.source_sha256,
        key_fingerprint=envelope.key_fingerprint,
    )


def _reject_signature_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceSigningError(f"signature JSON contains duplicate object key: {key}")
        result[key] = value
    return result


def _load_signature_envelope(path: str | Path) -> EvidenceSignatureEnvelope:
    source = _bounded_regular_file(path, maximum_bytes=MAX_EVIDENCE_SIGNATURE_BYTES)
    try:
        text = source.payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EvidenceSigningError("signature envelope is not valid UTF-8") from exc
    try:
        raw = json.loads(text, object_pairs_hook=_reject_signature_duplicate_keys)
    except EvidenceSigningError:
        raise
    except json.JSONDecodeError as exc:
        raise EvidenceSigningError(f"signature envelope is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise EvidenceSigningError("signature envelope JSON root must be an object")
    try:
        envelope = EvidenceSignatureEnvelope.model_validate(raw)
    except ValidationError as exc:
        raise EvidenceSigningError(
            f"signature envelope does not satisfy app-evidence-signature-v1: {exc}"
        ) from exc
    if _KEY_FINGERPRINT_RE.fullmatch(envelope.key_fingerprint) is None:
        raise EvidenceSigningError("signature envelope key fingerprint is invalid")
    if _SHA256_RE.fullmatch(envelope.manifest_sha256) is None:
        raise EvidenceSigningError("signature envelope manifest SHA-256 is invalid")
    _signature_bytes(envelope.signature)
    return envelope


def verify_portable_evidence_with_signature(
    manifest_path: str | Path,
    *,
    signature_path: str | Path | None = None,
    public_key_path: str | Path | None = None,
    snapshot_path: str | Path | None = None,
    lifecycle_path: str | Path | None = None,
    report_path: str | Path | None = None,
    trace_path: str | Path | None = None,
) -> PortableEvidenceVerificationWithSignature:
    if (signature_path is None) != (public_key_path is None):
        raise EvidenceSigningError("--signature and --public-key must be supplied together")

    base = verify_portable_evidence_with_snapshot(
        manifest_path,
        snapshot_path=snapshot_path,
        lifecycle_path=lifecycle_path,
        report_path=report_path,
        trace_path=trace_path,
    )
    if signature_path is None:
        return PortableEvidenceVerificationWithSignature(
            base=base,
            signature_status="not_supplied",
            key_fingerprint=None,
        )

    manifest = _bounded_regular_file(
        manifest_path,
        maximum_bytes=MAX_EVIDENCE_MANIFEST_BYTES,
    )
    if (
        manifest.source_bytes != base.base.manifest_bytes
        or manifest.source_sha256 != base.base.manifest_sha256
    ):
        raise EvidenceSigningError(
            "evidence manifest changed between consistency and signature verification reads"
        )

    envelope = _load_signature_envelope(signature_path)
    if envelope.manifest_sha256 != manifest.source_sha256:
        raise EvidenceSigningError("signature envelope manifest SHA-256 does not match manifest")

    public_key = _load_public_key(public_key_path)
    key_fingerprint = _public_key_fingerprint(public_key)
    if envelope.key_fingerprint != key_fingerprint:
        raise EvidenceSigningError("signature envelope key fingerprint does not match public key")

    InvalidSignature, _serialization, _private_type, _public_type = _crypto()
    try:
        public_key.verify(_signature_bytes(envelope.signature), manifest.payload)
    except InvalidSignature as exc:
        raise EvidenceSigningError("Ed25519 signature does not verify for exact manifest bytes") from exc

    return PortableEvidenceVerificationWithSignature(
        base=base,
        signature_status="verified",
        key_fingerprint=key_fingerprint,
    )
