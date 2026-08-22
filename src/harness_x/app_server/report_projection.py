"""Read-only projection of the canonical coding-task report for one App Server session."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .protocol import AppEvent, AppEventKind, AppSessionSnapshot

_MAX_REPORT_BYTES = 2 * 1024 * 1024
_REPORT_FILENAME = "coding-task-report.json"
_REPORT_ARTIFACT_KIND = "coding_task_report"


class ReportUnavailableError(RuntimeError):
    """The session does not have a durable coding report to project yet."""


class ReportCorruptionError(RuntimeError):
    """Durable report metadata or source bytes violate the M38 projection contract."""


class CodingReportProjection(BaseModel):
    """Bounded read-only projection of exact report source bytes plus parsed JSON."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-coding-report-projection-v1"] = (
        "app-coding-report-projection-v1"
    )
    session_id: str = Field(pattern=r"^app_[0-9a-f]{32}$")
    artifact_event_sequence: int = Field(ge=1)
    source_path: str
    source_bytes: int = Field(ge=0, le=_MAX_REPORT_BYTES)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
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


def _read_bounded_regular_file(path: Path, *, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    flags |= nofollow
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReportCorruptionError(f"cannot open coding report source: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ReportCorruptionError("coding report source is not a regular file")
        if metadata.st_size > maximum_bytes:
            raise ReportCorruptionError(
                f"coding report exceeds {maximum_bytes} byte projection limit"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            raise ReportCorruptionError(
                f"coding report exceeds {maximum_bytes} byte projection limit"
            )
        return payload
    finally:
        os.close(descriptor)


def build_coding_report_projection(
    *,
    snapshot: AppSessionSnapshot,
    events: tuple[AppEvent, ...],
    maximum_bytes: int = _MAX_REPORT_BYTES,
) -> CodingReportProjection:
    """Validate and project the one canonical durable coding report for ``snapshot``."""

    if maximum_bytes < 1 or maximum_bytes > _MAX_REPORT_BYTES:
        raise ValueError(f"maximum_bytes must be between 1 and {_MAX_REPORT_BYTES}")
    _output_root, report_path = _canonical_report_path(snapshot)
    artifact = _artifact_event(snapshot, events, report_path)
    payload = _read_bounded_regular_file(report_path, maximum_bytes=maximum_bytes)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReportCorruptionError("coding report source is not valid UTF-8") from exc
    try:
        report = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportCorruptionError(f"coding report source is not valid JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise ReportCorruptionError("coding report JSON root must be an object")
    return CodingReportProjection(
        session_id=snapshot.session_id,
        artifact_event_sequence=artifact.sequence,
        source_path=str(report_path),
        source_bytes=len(payload),
        source_sha256=hashlib.sha256(payload).hexdigest(),
        report=report,
    )
