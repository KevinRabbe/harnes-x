"""Bounded content attestation for the canonical App Server coding report."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_REPORT_BYTES = 2 * 1024 * 1024
REPORT_ATTESTATION_SCHEMA_VERSION = "app-artifact-content-attestation-v1"


class ReportAttestationCaptureError(RuntimeError):
    """The report source could not be safely read for durable attestation."""


class ReportContentAttestation(BaseModel):
    """Exact byte identity captured before the artifact event is appended."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-artifact-content-attestation-v1"] = (
        "app-artifact-content-attestation-v1"
    )
    algorithm: Literal["sha256"] = "sha256"
    source_bytes: int = Field(ge=0, le=MAX_REPORT_BYTES)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ReportSource:
    payload: bytes
    source_bytes: int
    source_sha256: str


def read_report_source(
    path: str | Path,
    *,
    maximum_bytes: int = MAX_REPORT_BYTES,
) -> ReportSource:
    """Read one regular non-symlink file with a hard byte bound and exact SHA-256."""

    if maximum_bytes < 1 or maximum_bytes > MAX_REPORT_BYTES:
        raise ValueError(f"maximum_bytes must be between 1 and {MAX_REPORT_BYTES}")

    source_path = Path(path)
    if source_path.is_symlink():
        raise ReportAttestationCaptureError("coding report cannot be a symbolic link")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source_path, flags)
    except OSError as exc:
        raise ReportAttestationCaptureError(
            f"cannot open coding report source: {exc}"
        ) from exc

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ReportAttestationCaptureError(
                "coding report source is not a regular file"
            )
        if metadata.st_size > maximum_bytes:
            raise ReportAttestationCaptureError(
                f"coding report exceeds {maximum_bytes} byte attestation limit"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            raise ReportAttestationCaptureError(
                f"coding report exceeds {maximum_bytes} byte attestation limit"
            )
        return ReportSource(
            payload=payload,
            source_bytes=len(payload),
            source_sha256=hashlib.sha256(payload).hexdigest(),
        )
    finally:
        os.close(descriptor)


def capture_report_attestation(
    path: str | Path,
    *,
    maximum_bytes: int = MAX_REPORT_BYTES,
) -> ReportContentAttestation:
    """Capture the exact current report byte count and SHA-256 for ledger persistence."""

    source = read_report_source(path, maximum_bytes=maximum_bytes)
    return ReportContentAttestation(
        source_bytes=source.source_bytes,
        source_sha256=source.source_sha256,
    )
