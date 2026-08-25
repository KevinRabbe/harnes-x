"""Grounded product work-activity projection for M70.

Product activity is derived only from existing App Server lifecycle events and verified causal
trace records. It never becomes an execution ledger or a second source of truth.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from harness_x.core import EventType
from harness_x.telemetry.trace_store import TraceRecord

from .protocol import AppEvent, AppEventKind, AppSessionSnapshot
from .trace_projection import load_verified_trace_records

_CURSOR_RE = re.compile(r"^a(?P<app>[0-9]{1,12}):t(?P<trace>[0-9]{1,12})$")
_MAX_SUMMARY = 1000
_MAX_DETAIL = 1000


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _activity_id(
    *,
    execution_id: str,
    source_type: str,
    source_sequence: int,
    source_hash: str,
    kind: str,
) -> str:
    digest = hashlib.sha256(
        _canonical(
            {
                "execution_id": execution_id,
                "source_type": source_type,
                "source_sequence": source_sequence,
                "source_hash": source_hash,
                "kind": kind,
            }
        )
    ).hexdigest()
    return f"activity_{digest[:32]}"


def _text(value: object, *, maximum: int = _MAX_DETAIL) -> str:
    result = str(value).strip()
    if len(result) <= maximum:
        return result
    return result[:maximum] + "...<truncated>"


class WorkActivityKind(StrEnum):
    WORK_STARTED = "work_started"
    STATUS_CHANGED = "status_changed"
    TOOL_COMPLETED = "tool_completed"
    FILE_CHANGED = "file_changed"
    VERIFICATION_RESULT = "verification_result"
    ASSISTANT_UPDATE = "assistant_update"
    WORK_COMPLETED = "work_completed"
    WORK_FAILED = "work_failed"
    WORK_CANCELLED = "work_cancelled"


class WorkActivityEvent(BaseModel):
    """One UI-safe event deterministically derived from one authoritative source record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["conversation-work-event-v1"] = "conversation-work-event-v1"
    event_id: str = Field(pattern=r"^activity_[0-9a-f]{32}$")
    execution_id: str = Field(pattern=r"^exec_[0-9a-f]{32}$")
    session_id: str = Field(pattern=r"^app_[0-9a-f]{32}$")
    kind: WorkActivityKind
    created_at: datetime
    summary: str = Field(min_length=1, max_length=_MAX_SUMMARY)
    data: dict[str, Any] = Field(default_factory=dict)
    source_type: Literal["app_event", "trace_event"]
    source_sequence: int = Field(ge=1)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkActivityPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["conversation-work-activity-page-v1"] = (
        "conversation-work-activity-page-v1"
    )
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    execution_id: str = Field(pattern=r"^exec_[0-9a-f]{32}$")
    session_id: str = Field(pattern=r"^app_[0-9a-f]{32}$")
    cursor: str
    next_cursor: str
    status: str
    terminal: bool
    trace_attached: bool
    has_more: bool
    events: tuple[WorkActivityEvent, ...] = ()


def parse_work_activity_cursor(value: str | None) -> tuple[int, int]:
    if value is None or value == "":
        return 0, 0
    if len(value) > 32:
        raise ValueError("work activity cursor is too long")
    match = _CURSOR_RE.fullmatch(value)
    if match is None:
        raise ValueError("work activity cursor must use aN:tM format")
    return int(match.group("app")), int(match.group("trace"))


def format_work_activity_cursor(app_sequence: int, trace_step: int) -> str:
    if app_sequence < 0 or trace_step < 0:
        raise ValueError("work activity cursor values cannot be negative")
    return f"a{app_sequence}:t{trace_step}"


def _app_event(
    *,
    execution_id: str,
    session_id: str,
    event: AppEvent,
) -> WorkActivityEvent | None:
    kind: WorkActivityKind
    summary: str
    data: dict[str, Any]

    if event.kind == AppEventKind.SESSION_CREATED:
        kind = WorkActivityKind.WORK_STARTED
        summary = "Harness X work is queued."
        data = {"status": "created"}
    elif event.kind == AppEventKind.SESSION_STARTED:
        kind = WorkActivityKind.STATUS_CHANGED
        summary = "Harness X started working."
        data = {"status": "running"}
    elif event.kind == AppEventKind.SESSION_STATUS:
        status = _text(event.payload.get("status", "unknown"), maximum=80)
        kind = WorkActivityKind.STATUS_CHANGED
        summary = f"Harness X status changed to {status}."
        data = {"status": status}
    elif event.kind == AppEventKind.SESSION_CANCEL_REQUESTED:
        kind = WorkActivityKind.STATUS_CHANGED
        summary = "Cancellation was requested."
        data = {"status": "cancel_requested"}
    elif event.kind == AppEventKind.SESSION_COMPLETED:
        kind = WorkActivityKind.WORK_COMPLETED
        summary = "Harness X completed this work successfully."
        data = {"status": "succeeded"}
    elif event.kind == AppEventKind.SESSION_FAILED:
        kind = WorkActivityKind.WORK_FAILED
        summary = "Harness X could not complete this work."
        data = {"status": "failed"}
    elif event.kind == AppEventKind.SESSION_CANCELLED:
        kind = WorkActivityKind.WORK_CANCELLED
        summary = "Harness X execution was cancelled."
        data = {"status": "cancelled"}
    else:
        return None

    return WorkActivityEvent(
        event_id=_activity_id(
            execution_id=execution_id,
            source_type="app_event",
            source_sequence=event.sequence,
            source_hash=event.event_hash,
            kind=kind.value,
        ),
        execution_id=execution_id,
        session_id=session_id,
        kind=kind,
        created_at=event.created_at,
        summary=summary,
        data=data,
        source_type="app_event",
        source_sequence=event.sequence,
        source_hash=event.event_hash,
    )


def _trace_event(
    *,
    execution_id: str,
    session_id: str,
    record: TraceRecord,
) -> WorkActivityEvent | None:
    event = record.event
    metadata = event.metadata
    kind: WorkActivityKind
    summary: str
    data: dict[str, Any]

    if event.event_type == EventType.CODING_PHASE_CHANGED:
        previous = _text(metadata.get("from", "unknown"), maximum=80)
        current = _text(metadata.get("to", "unknown"), maximum=80)
        reason = _text(metadata.get("reason", ""))
        kind = WorkActivityKind.STATUS_CHANGED
        summary = f"Coding phase: {current}."
        data = {"from": previous, "to": current}
        if reason:
            data["reason"] = reason
    elif event.event_type == EventType.TOOL_EXECUTION_FINISHED:
        result = metadata.get("result")
        if not isinstance(result, dict):
            return None
        tool_name = _text(result.get("tool_name", "tool"), maximum=120)
        status = _text(result.get("status", "unknown"), maximum=80)
        duration_raw = result.get("duration_ms")
        duration_ms = (
            round(float(duration_raw), 1)
            if isinstance(duration_raw, (int, float)) and not isinstance(duration_raw, bool)
            else None
        )
        kind = WorkActivityKind.TOOL_COMPLETED
        summary = (
            f"{tool_name} completed."
            if status == "succeeded"
            else f"{tool_name} finished with status {status}."
        )
        data = {"tool_name": tool_name, "status": status}
        if duration_ms is not None:
            data["duration_ms"] = duration_ms
    elif event.event_type == EventType.CODING_PLAN_UPDATED:
        if metadata.get("reason") != "workspace_mutated":
            return None
        raw_files = metadata.get("changed_files")
        if not isinstance(raw_files, list) or not raw_files:
            return None
        path = _text(raw_files[-1])
        kind = WorkActivityKind.FILE_CHANGED
        summary = f"Changed file: {path}"
        data = {"path": path, "changed_files_count": len(raw_files)}
    elif event.event_type == EventType.VERIFICATION_COMPLETED:
        passed = metadata.get("passed") is True
        configured = metadata.get("configured_commands")
        executed = metadata.get("executed_commands")
        raw_returncodes = metadata.get("returncodes")
        returncodes = (
            [int(item) for item in raw_returncodes if type(item) is int][:32]
            if isinstance(raw_returncodes, list)
            else []
        )
        kind = WorkActivityKind.VERIFICATION_RESULT
        summary = "Verification passed." if passed else "Verification failed."
        data = {
            "passed": passed,
            "configured_commands": int(configured) if type(configured) is int else None,
            "executed_commands": int(executed) if type(executed) is int else None,
            "returncodes": returncodes,
        }
    elif event.event_type == EventType.CODING_PROGRESS_ASSESSED:
        intervention = metadata.get("intervention")
        if not isinstance(intervention, dict):
            return None
        intervention_kind = _text(intervention.get("kind", "none"), maximum=120)
        if intervention_kind in {"", "none"}:
            return None
        reason = _text(intervention.get("reason", ""))
        kind = WorkActivityKind.ASSISTANT_UPDATE
        summary = f"Controller intervention: {intervention_kind}."
        data = {"intervention": intervention_kind}
        if reason:
            data["reason"] = reason
    elif event.event_type == EventType.ERROR_RECORDED:
        kind = WorkActivityKind.ASSISTANT_UPDATE
        summary = "Harness X recorded an execution error."
        data = {}
    else:
        return None

    return WorkActivityEvent(
        event_id=_activity_id(
            execution_id=execution_id,
            source_type="trace_event",
            source_sequence=event.step,
            source_hash=record.event_hash,
            kind=kind.value,
        ),
        execution_id=execution_id,
        session_id=session_id,
        kind=kind,
        created_at=event.timestamp,
        summary=summary,
        data=data,
        source_type="trace_event",
        source_sequence=event.step,
        source_hash=record.event_hash,
    )


def build_work_activity_page(
    *,
    project_id: str,
    chat_id: str,
    execution_id: str,
    snapshot: AppSessionSnapshot,
    app_events: tuple[AppEvent, ...],
    cursor: str | None,
    limit: int = 100,
) -> WorkActivityPage:
    """Project incremental product activity from exact durable lifecycle/trace sources."""

    if limit < 1 or limit > 200:
        raise ValueError("work activity limit must be between 1 and 200")
    app_after, trace_after = parse_work_activity_cursor(cursor)
    current_cursor = format_work_activity_cursor(app_after, trace_after)

    max_app = app_events[-1].sequence if app_events else 0
    if app_after > max_app:
        raise ValueError("work activity cursor is ahead of App Server event history")

    trace_records: tuple[TraceRecord, ...] = ()
    if snapshot.trace_id is not None and snapshot.trace_path is not None:
        trace_records, _partial_ignored = load_verified_trace_records(
            snapshot.trace_path,
            expected_trace_id=snapshot.trace_id,
            require_complete_final_line=snapshot.status.terminal,
        )
    max_trace = trace_records[-1].event.step if trace_records else 0
    if trace_after > max_trace:
        raise ValueError("work activity cursor is ahead of causal trace history")

    candidates: list[tuple[datetime, int, int, str, AppEvent | TraceRecord]] = []
    candidates.extend(
        (item.created_at, 0, item.sequence, "app_event", item)
        for item in app_events
        if item.sequence > app_after
    )
    candidates.extend(
        (item.event.timestamp, 1, item.event.step, "trace_event", item)
        for item in trace_records
        if item.event.step > trace_after
    )
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))

    next_app = app_after
    next_trace = trace_after
    projected: list[WorkActivityEvent] = []
    processed = 0
    for _timestamp, _source_order, sequence, source_type, source in candidates:
        processed += 1
        if source_type == "app_event":
            next_app = max(next_app, sequence)
            assert isinstance(source, AppEvent)
            item = _app_event(
                execution_id=execution_id,
                session_id=snapshot.session_id,
                event=source,
            )
        else:
            next_trace = max(next_trace, sequence)
            assert isinstance(source, TraceRecord)
            item = _trace_event(
                execution_id=execution_id,
                session_id=snapshot.session_id,
                record=source,
            )
        if item is not None:
            projected.append(item)
            if len(projected) >= limit:
                break

    return WorkActivityPage(
        project_id=project_id,
        chat_id=chat_id,
        execution_id=execution_id,
        session_id=snapshot.session_id,
        cursor=current_cursor,
        next_cursor=format_work_activity_cursor(next_app, next_trace),
        status=snapshot.status.value,
        terminal=snapshot.status.terminal,
        trace_attached=snapshot.trace_id is not None,
        has_more=processed < len(candidates),
        events=tuple(projected),
    )
