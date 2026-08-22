"""Terminal-only exact-byte export validation for one attached causal trace."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from harness_x.core import TraceId
from harness_x.core.errors import TraceCorruptionError
from harness_x.telemetry.trace_store import TraceRecord

from .protocol import AppEvent, AppEventKind, AppSessionSnapshot
from .trace_projection import verify_trace_payload

MAX_TRACE_EXPORT_BYTES = 32 * 1024 * 1024


class TraceExportNotTerminalError(RuntimeError):
    """Raw trace export is unavailable while the authoritative trace may still be written."""


class TraceExportUnavailableError(RuntimeError):
    """A terminal session does not have an attached causal trace to export."""


@dataclass(frozen=True, slots=True)
class TraceExportSource:
    """One bounded regular-file read with exact current byte identity."""

    payload: bytes
    source_bytes: int
    source_sha256: str


@dataclass(frozen=True, slots=True)
class ValidatedTraceExport:
    """Canonical attachment evidence bound to the exact source bytes that were verified."""

    session_id: str
    trace_id: str
    trace_path: str
    source: TraceExportSource
    records: tuple[TraceRecord, ...]
    attachment_event_sequence: int
    attachment_event_hash: str
    final_event_hash: str | None


def _read_trace_export_source(
    path: str | Path,
    *,
    maximum_bytes: int,
) -> TraceExportSource:
    if maximum_bytes < 1 or maximum_bytes > MAX_TRACE_EXPORT_BYTES:
        raise ValueError(
            f"maximum_bytes must be between 1 and {MAX_TRACE_EXPORT_BYTES}"
        )

    source_path = Path(path)
    if source_path.is_symlink():
        raise TraceCorruptionError("causal trace export source cannot be a symbolic link")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source_path, flags)
    except OSError as exc:
        raise TraceCorruptionError(f"cannot open causal trace export source: {exc}") from exc

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise TraceCorruptionError("causal trace export source is not a regular file")
        if metadata.st_size > maximum_bytes:
            raise TraceCorruptionError(
                f"causal trace exceeds {maximum_bytes} byte export limit"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            raise TraceCorruptionError(
                f"causal trace exceeds {maximum_bytes} byte export limit"
            )
        return TraceExportSource(
            payload=payload,
            source_bytes=len(payload),
            source_sha256=hashlib.sha256(payload).hexdigest(),
        )
    finally:
        os.close(descriptor)


def _attachment_event(
    snapshot: AppSessionSnapshot,
    events: tuple[AppEvent, ...],
    *,
    expected_path: str,
) -> AppEvent:
    attached = tuple(event for event in events if event.kind == AppEventKind.TRACE_ATTACHED)
    if len(attached) != 1:
        raise TraceCorruptionError(
            "causal trace export requires exactly one durable TRACE_ATTACHED event"
        )
    event = attached[0]
    if event.session_id != snapshot.session_id or not event.verify_hash():
        raise TraceCorruptionError("causal trace attachment event identity/hash is invalid")
    if event.payload.get("trace_id") != snapshot.trace_id:
        raise TraceCorruptionError("causal trace attachment event trace_id disagrees with snapshot")
    if event.payload.get("trace_path") != expected_path:
        raise TraceCorruptionError("causal trace attachment event path disagrees with snapshot")
    return event


def read_validated_trace_export(
    *,
    snapshot: AppSessionSnapshot,
    events: tuple[AppEvent, ...],
    maximum_bytes: int = MAX_TRACE_EXPORT_BYTES,
) -> ValidatedTraceExport:
    """Validate one terminal canonical trace and retain the exact verified source bytes."""

    if maximum_bytes < 1 or maximum_bytes > MAX_TRACE_EXPORT_BYTES:
        raise ValueError(
            f"maximum_bytes must be between 1 and {MAX_TRACE_EXPORT_BYTES}"
        )
    if not snapshot.status.terminal:
        raise TraceExportNotTerminalError(
            "causal trace export is available only after the App Server session is terminal"
        )
    if snapshot.trace_id is None or snapshot.trace_path is None:
        raise TraceExportUnavailableError("terminal session has no attached causal trace")

    try:
        TraceId(value=snapshot.trace_id)
    except Exception as exc:
        raise TraceCorruptionError("attached causal trace has an invalid trace_id") from exc

    output_root = Path(snapshot.output_root)
    trace_path = Path(snapshot.trace_path)
    if not output_root.is_absolute() or not trace_path.is_absolute():
        raise TraceCorruptionError("causal trace export requires canonical absolute paths")
    expected_path = output_root / f"{snapshot.trace_id}.jsonl"
    if trace_path != expected_path or trace_path.parent != output_root:
        raise TraceCorruptionError(
            "attached causal trace path is not the canonical session trace path"
        )

    attachment = _attachment_event(
        snapshot,
        events,
        expected_path=str(expected_path),
    )
    source = _read_trace_export_source(trace_path, maximum_bytes=maximum_bytes)
    records, partial_ignored = verify_trace_payload(
        source.payload,
        expected_trace_id=snapshot.trace_id,
        require_complete_final_line=True,
        source_label=str(trace_path),
    )
    if partial_ignored:
        raise AssertionError("terminal trace verification cannot ignore a partial line")

    return ValidatedTraceExport(
        session_id=snapshot.session_id,
        trace_id=snapshot.trace_id,
        trace_path=str(trace_path),
        source=source,
        records=records,
        attachment_event_sequence=attachment.sequence,
        attachment_event_hash=attachment.event_hash,
        final_event_hash=(records[-1].event_hash if records else None),
    )
