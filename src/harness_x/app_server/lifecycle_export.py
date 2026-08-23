"""Deterministic terminal App Server lifecycle export for portable offline verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .protocol import AppEvent, AppSessionSnapshot

MAX_LIFECYCLE_EXPORT_BYTES = 4 * 1024 * 1024
TerminalSessionStatus = Literal["succeeded", "failed", "cancelled"]


class LifecycleExportNotTerminalError(RuntimeError):
    """Lifecycle export is intentionally unavailable for mutable running sessions."""


class LifecycleExportCorruptionError(RuntimeError):
    """Snapshot or lifecycle events disagree with the durable App Server contract."""


class LifecycleExportTooLargeError(RuntimeError):
    """Generated lifecycle export exceeds the fixed portable evidence bound."""


class LifecycleLedgerExport(BaseModel):
    """Portable lifecycle correlation metadata plus the complete AppEvent chain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-lifecycle-ledger-export-v1"] = (
        "app-lifecycle-ledger-export-v1"
    )
    session_id: str = Field(pattern=r"^app_[0-9a-f]{32}$")
    status: TerminalSessionStatus
    snapshot_revision: int = Field(ge=1)
    snapshot_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_count: int = Field(ge=1)
    ledger_head_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ledger_head_kind: str
    created_at: datetime
    completed_at: datetime
    events: tuple[AppEvent, ...] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class RenderedLifecycleLedgerExport:
    payload: bytes
    source_bytes: int
    source_sha256: str
    event_count: int
    ledger_head_hash: str


def _validated_snapshot(snapshot: AppSessionSnapshot) -> None:
    if not snapshot.status.terminal:
        raise LifecycleExportNotTerminalError(
            "lifecycle export is available only after the App Server session is terminal"
        )
    if snapshot.completed_at is None:
        raise LifecycleExportCorruptionError("terminal session is missing completed_at")
    try:
        recomputed = AppSessionSnapshot.model_validate(snapshot.model_dump(mode="json"))
    except Exception as exc:
        raise LifecycleExportCorruptionError(
            f"session snapshot cannot be revalidated: {exc}"
        ) from exc
    if snapshot.fingerprint != recomputed.fingerprint:
        raise LifecycleExportCorruptionError(
            "session snapshot fingerprint does not match snapshot contents"
        )


def _validated_events(
    snapshot: AppSessionSnapshot,
    events: tuple[AppEvent, ...],
) -> None:
    if not events:
        raise LifecycleExportCorruptionError("terminal session lifecycle ledger is empty")

    previous_hash: str | None = None
    for expected_sequence, event in enumerate(events, start=1):
        if event.session_id != snapshot.session_id:
            raise LifecycleExportCorruptionError(
                f"cross-session lifecycle event at sequence {expected_sequence}"
            )
        if event.sequence != expected_sequence:
            raise LifecycleExportCorruptionError(
                f"non-contiguous lifecycle event sequence at {expected_sequence}"
            )
        if event.previous_hash != previous_hash:
            raise LifecycleExportCorruptionError(
                f"broken lifecycle previous hash at sequence {expected_sequence}"
            )
        if not event.verify_hash():
            raise LifecycleExportCorruptionError(
                f"lifecycle event hash mismatch at sequence {expected_sequence}"
            )
        previous_hash = event.event_hash

    if snapshot.event_count != len(events):
        raise LifecycleExportCorruptionError(
            "session snapshot event_count disagrees with lifecycle ledger"
        )
    if snapshot.latest_event_hash != previous_hash:
        raise LifecycleExportCorruptionError(
            "session snapshot latest_event_hash disagrees with lifecycle ledger head"
        )


def build_lifecycle_ledger_export(
    *,
    snapshot: AppSessionSnapshot,
    events: tuple[AppEvent, ...],
) -> LifecycleLedgerExport:
    """Validate terminal lifecycle state and build one deterministic portable model."""

    _validated_snapshot(snapshot)
    _validated_events(snapshot, events)
    status = snapshot.status.value
    if status not in {"succeeded", "failed", "cancelled"}:
        raise LifecycleExportCorruptionError("terminal session has an unsupported status")
    return LifecycleLedgerExport(
        session_id=snapshot.session_id,
        status=status,
        snapshot_revision=snapshot.revision,
        snapshot_fingerprint=snapshot.fingerprint,
        event_count=len(events),
        ledger_head_hash=events[-1].event_hash,
        ledger_head_kind=events[-1].kind.value,
        created_at=snapshot.created_at,
        completed_at=snapshot.completed_at,
        events=events,
    )


def render_lifecycle_ledger_export(
    export: LifecycleLedgerExport,
) -> RenderedLifecycleLedgerExport:
    """Serialize once and retain the exact generated bytes described by response headers."""

    payload = export.model_dump_json().encode("utf-8") + b"\n"
    if len(payload) > MAX_LIFECYCLE_EXPORT_BYTES:
        raise LifecycleExportTooLargeError(
            f"lifecycle export exceeds {MAX_LIFECYCLE_EXPORT_BYTES} byte limit"
        )
    return RenderedLifecycleLedgerExport(
        payload=payload,
        source_bytes=len(payload),
        source_sha256=hashlib.sha256(payload).hexdigest(),
        event_count=export.event_count,
        ledger_head_hash=export.ledger_head_hash,
    )
