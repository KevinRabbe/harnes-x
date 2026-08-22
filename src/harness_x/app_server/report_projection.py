"""Read-only projection and export validation for one canonical coding-task report."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .protocol import AppEvent, AppEventKind, AppSessionSnapshot
from .report_attestation import (
    MAX_REPORT_BYTES,
    REPORT_ATTESTATION_SCHEMA_VERSION,
    ReportAttestationCaptureError,
    ReportSource,
    read_report_source,
)

_REPORT_FILENAME = "coding-task-report.json"
_REPORT_ARTIFACT_KIND = "coding_task_report"
_ATTESTATION_KEYS = frozenset(
    {
        "attestation_schema_version",
        "attestation_status",
        "source_digest_algorithm",
        "source_bytes",
        "source_sha256",
        "attestation_error",
    }
)

ReportAttestationStatus = Literal["verified", "legacy_unattested", "unavailable"]


class ReportUnavailableError(RuntimeError):
    """The session does not have a durable coding report to project yet."""


class ReportCorruptionError(RuntimeError):
    """Durable report metadata or source bytes violate the projection contract."""


@dataclass(frozen=True, slots=True)
class ValidatedCodingReport:
    """One report validation result bound to the exact source bytes that were checked."""

    session_id: str
    artifact_event_sequence: int
    artifact_event_hash: str
    source_path: str
    source: ReportSource
    attestation_status: ReportAttestationStatus
    attested_source_bytes: int | None
    attested_source_sha256: str | None
    attestation_error: str | None
    report: dict[str, Any]


class CodingReportProjection(BaseModel):
    """Bounded report projection plus durable-content-attestation state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-coding-report-projection-v2"] = (
        "app-coding-report-projection-v2"
    )
    session_id: str = Field(pattern=r"^app_[0-9a-f]{32}$")
    artifact_event_sequence: int = Field(ge=1)
    artifact_event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str
    source_bytes: int = Field(ge=0, le=MAX_REPORT_BYTES)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attestation_status: ReportAttestationStatus
    attested_source_bytes: int | None = Field(default=None, ge=0, le=MAX_REPORT_BYTES)
    attested_source_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    attestation_error: str | None = Field(default=None, max_length=1000)
    report: dict[str, Any]


def _canonical_report_path(snapshot: AppSessionSnapshot) -> tuple[Path, Path]:
    if not snapshot.status.terminal or snapshot.coding_report_path is None:
        raise ReportUnavailableError("coding report is not available for this session")

    output_root = Path(snapshot.output_root)
    if not output_root.is_absolute():
        raise ReportCorruptionError("session output_root is not absolute")
    output_root = output_root.resolve()
    expected = output_root / _REPORT_FILENAME

    recorded = Path(snapshot.coding_report_path)
    if not recorded.is_absolute() or recorded != expected:
        raise ReportCorruptionError("coding_report_path is not the canonical session report path")
    if recorded.is_symlink():
        raise ReportCorruptionError("coding report cannot be a symbolic link")
    try:
        resolved = recorded.resolve(strict=True)
    except OSError as exc:
        raise ReportCorruptionError(f"coding report source is unavailable: {exc}") from exc
    if resolved != expected or resolved.parent != output_root:
        raise ReportCorruptionError("coding report escapes the session output root")
    return output_root, resolved


def _artifact_event(
    snapshot: AppSessionSnapshot,
    events: tuple[AppEvent, ...],
    report_path: Path,
) -> AppEvent:
    candidates = tuple(
        event
        for event in events
        if event.kind == AppEventKind.ARTIFACT_AVAILABLE
        and event.payload.get("artifact_kind") == _REPORT_ARTIFACT_KIND
    )
    if len(candidates) != 1:
        raise ReportCorruptionError(
            f"expected exactly one durable coding report artifact event, found {len(candidates)}"
        )
    event = candidates[0]
    if event.session_id != snapshot.session_id:
        raise ReportCorruptionError("coding report artifact belongs to a different session")
    raw_path = event.payload.get("path")
    if not isinstance(raw_path, str):
        raise ReportCorruptionError("coding report artifact path is missing")
    artifact_path = Path(raw_path)
    if not artifact_path.is_absolute() or artifact_path != report_path:
        raise ReportCorruptionError("coding report artifact path does not match the snapshot")
    return event


def _artifact_attestation(
    event: AppEvent,
) -> tuple[ReportAttestationStatus, int | None, str | None, str | None]:
    payload = event.payload
    present = _ATTESTATION_KEYS.intersection(payload)
    if not present:
        return "legacy_unattested", None, None, None

    if payload.get("attestation_schema_version") != REPORT_ATTESTATION_SCHEMA_VERSION:
        raise ReportCorruptionError("coding report artifact has an invalid attestation schema")

    status = payload.get("attestation_status")
    if status == "captured":
        required = {
            "attestation_schema_version",
            "attestation_status",
            "source_digest_algorithm",
            "source_bytes",
            "source_sha256",
        }
        if not required.issubset(payload) or "attestation_error" in payload:
            raise ReportCorruptionError("coding report captured attestation is incomplete")
        if payload.get("source_digest_algorithm") != "sha256":
            raise ReportCorruptionError("coding report attestation algorithm is not sha256")
        raw_bytes = payload.get("source_bytes")
        raw_sha = payload.get("source_sha256")
        if type(raw_bytes) is not int or raw_bytes < 0 or raw_bytes > MAX_REPORT_BYTES:
            raise ReportCorruptionError("coding report attested source_bytes is invalid")
        if (
            not isinstance(raw_sha, str)
            or len(raw_sha) != 64
            or any(ch not in "0123456789abcdef" for ch in raw_sha)
        ):
            raise ReportCorruptionError("coding report attested source_sha256 is invalid")
        return "verified", raw_bytes, raw_sha, None

    if status == "unavailable":
        required = {
            "attestation_schema_version",
            "attestation_status",
            "attestation_error",
        }
        forbidden = {"source_digest_algorithm", "source_bytes", "source_sha256"}
        if not required.issubset(payload) or forbidden.intersection(payload):
            raise ReportCorruptionError("coding report unavailable attestation is malformed")
        error = payload.get("attestation_error")
        if not isinstance(error, str) or not error.strip():
            raise ReportCorruptionError("coding report attestation_error is invalid")
        return "unavailable", None, None, error[:1000]

    raise ReportCorruptionError("coding report artifact has an invalid attestation status")


def read_validated_coding_report(
    *,
    snapshot: AppSessionSnapshot,
    events: tuple[AppEvent, ...],
    maximum_bytes: int = MAX_REPORT_BYTES,
) -> ValidatedCodingReport:
    """Validate one report and retain the exact source bytes that satisfied the checks."""

    if maximum_bytes < 1 or maximum_bytes > MAX_REPORT_BYTES:
        raise ValueError(f"maximum_bytes must be between 1 and {MAX_REPORT_BYTES}")
    _output_root, report_path = _canonical_report_path(snapshot)
    artifact = _artifact_event(snapshot, events, report_path)
    attestation_status, attested_bytes, attested_sha, attestation_error = (
        _artifact_attestation(artifact)
    )
    try:
        source = read_report_source(report_path, maximum_bytes=maximum_bytes)
    except ReportAttestationCaptureError as exc:
        raise ReportCorruptionError(str(exc)) from exc

    if attestation_status == "verified":
        if source.source_bytes != attested_bytes:
            raise ReportCorruptionError(
                "coding report current byte count does not match durable attestation"
            )
        if source.source_sha256 != attested_sha:
            raise ReportCorruptionError(
                "coding report current SHA-256 does not match durable attestation"
            )

    try:
        text = source.payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReportCorruptionError("coding report source is not valid UTF-8") from exc
    try:
        report = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportCorruptionError(f"coding report source is not valid JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise ReportCorruptionError("coding report JSON root must be an object")

    return ValidatedCodingReport(
        session_id=snapshot.session_id,
        artifact_event_sequence=artifact.sequence,
        artifact_event_hash=artifact.event_hash,
        source_path=str(report_path),
        source=source,
        attestation_status=attestation_status,
        attested_source_bytes=attested_bytes,
        attested_source_sha256=attested_sha,
        attestation_error=attestation_error,
        report=report,
    )


def build_coding_report_projection(
    *,
    snapshot: AppSessionSnapshot,
    events: tuple[AppEvent, ...],
    maximum_bytes: int = MAX_REPORT_BYTES,
) -> CodingReportProjection:
    """Validate and project the one canonical durable coding report for ``snapshot``."""

    validated = read_validated_coding_report(
        snapshot=snapshot,
        events=events,
        maximum_bytes=maximum_bytes,
    )
    return CodingReportProjection(
        session_id=validated.session_id,
        artifact_event_sequence=validated.artifact_event_sequence,
        artifact_event_hash=validated.artifact_event_hash,
        source_path=validated.source_path,
        source_bytes=validated.source.source_bytes,
        source_sha256=validated.source.source_sha256,
        attestation_status=validated.attestation_status,
        attested_source_bytes=validated.attested_source_bytes,
        attested_source_sha256=validated.attested_source_sha256,
        attestation_error=validated.attestation_error,
        report=validated.report,
    )
