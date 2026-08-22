"""Read-only, bounded projections of the authoritative Harness X causal trace.

M35 never writes a second reasoning/tool/verification ledger. It verifies complete TraceRecord
lines directly from the existing JSONL trace, tolerates only a currently-being-written final
partial line, and emits a bounded UI-safe projection that retains the source chain hashes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness_x.core import TraceId
from harness_x.core.errors import TraceCorruptionError
from harness_x.telemetry.trace_store import (
    RECORD_SCHEMA_VERSION,
    TraceRecord,
    _event_hash,
)

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "passwd",
        "secret",
        "access_token",
        "refresh_token",
        "cookie",
        "set-cookie",
    }
)
_MAX_METADATA_DEPTH = 6
_MAX_COLLECTION_ITEMS = 40
_MAX_STRING_CHARS = 4000
_MAX_PROJECTION_CHARS = 16000


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _sensitive_key(value: object) -> bool:
    normalized = str(value).strip().casefold().replace("-", "_")
    return normalized in {item.replace("-", "_") for item in _SENSITIVE_KEYS}


def _bounded_value(value: Any, *, depth: int = 0) -> tuple[Any, bool]:
    if depth >= _MAX_METADATA_DEPTH:
        return "<depth-truncated>", True
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    if isinstance(value, str):
        if len(value) <= _MAX_STRING_CHARS:
            return value, False
        return value[:_MAX_STRING_CHARS] + "...<truncated>", True
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        truncated = len(value) > _MAX_COLLECTION_ITEMS
        for key in sorted(value, key=lambda item: str(item))[:_MAX_COLLECTION_ITEMS]:
            text_key = str(key)
            if _sensitive_key(text_key):
                result[text_key] = "<redacted>"
                truncated = True
                continue
            child, child_truncated = _bounded_value(value[key], depth=depth + 1)
            result[text_key] = child
            truncated = truncated or child_truncated
        return result, truncated
    if isinstance(value, (list, tuple, set, frozenset)):
        rows = list(value)
        result = []
        truncated = len(rows) > _MAX_COLLECTION_ITEMS
        for item in rows[:_MAX_COLLECTION_ITEMS]:
            child, child_truncated = _bounded_value(item, depth=depth + 1)
            result.append(child)
            truncated = truncated or child_truncated
        return result, truncated
    text = str(value)
    if len(text) > _MAX_STRING_CHARS:
        text = text[:_MAX_STRING_CHARS] + "...<truncated>"
    return text, True


def _bounded_refs(values: list[str]) -> tuple[tuple[str, ...], bool]:
    truncated = len(values) > _MAX_COLLECTION_ITEMS
    result: list[str] = []
    for value in values[:_MAX_COLLECTION_ITEMS]:
        if len(value) > 1000:
            result.append(value[:1000] + "...<truncated>")
            truncated = True
        else:
            result.append(value)
    return tuple(result), truncated


class TraceProjectionEvent(BaseModel):
    """Bounded UI projection carrying exact source trace-chain identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-trace-projection-v1"] = "app-trace-projection-v1"
    source_record_schema_version: str
    source_event_hash: str = Field(min_length=64, max_length=64)
    source_previous_hash: str | None = Field(default=None, min_length=64, max_length=64)
    source_event_id: str
    trace_id: str
    task_id: str
    step: int = Field(ge=1)
    timestamp: datetime
    event_type: str
    component: str
    system_version: str
    input_refs: tuple[str, ...] = ()
    output_refs: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    projection_truncated: bool = False
    fingerprint: str = ""

    @model_validator(mode="after")
    def _derive_fingerprint(self) -> "TraceProjectionEvent":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(
            self,
            "fingerprint",
            hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest(),
        )
        return self


class TraceProjectionPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-trace-page-v1"] = "app-trace-page-v1"
    session_id: str
    trace_attached: bool
    trace_id: str | None = None
    trace_path: str | None = None
    after: int = Field(default=0, ge=0)
    limit: int = Field(ge=1, le=1000)
    next_after: int = Field(ge=0)
    has_more: bool
    final_partial_line_ignored: bool = False
    events: tuple[TraceProjectionEvent, ...] = ()


def _project_record(record: TraceRecord) -> TraceProjectionEvent:
    metadata, metadata_truncated = _bounded_value(record.event.metadata)
    if not isinstance(metadata, dict):
        metadata = {"value": metadata}
        metadata_truncated = True
    input_refs, input_truncated = _bounded_refs(record.event.input_refs)
    output_refs, output_truncated = _bounded_refs(record.event.output_refs)
    projection = TraceProjectionEvent(
        source_record_schema_version=record.record_schema_version,
        source_event_hash=record.event_hash,
        source_previous_hash=record.previous_hash,
        source_event_id=str(record.event.event_id),
        trace_id=str(record.event.trace_id),
        task_id=str(record.event.task_id),
        step=record.event.step,
        timestamp=record.event.timestamp,
        event_type=record.event.event_type.value,
        component=record.event.component,
        system_version=str(record.event.system_version),
        input_refs=input_refs,
        output_refs=output_refs,
        metadata=metadata,
        projection_truncated=(
            metadata_truncated or input_truncated or output_truncated
        ),
    )
    if len(projection.model_dump_json()) <= _MAX_PROJECTION_CHARS:
        return projection
    compact_metadata = {
        "projection_note": "metadata compacted to preserve M35 event size bound",
        "metadata_keys": tuple(sorted(metadata))[:_MAX_COLLECTION_ITEMS],
    }
    return TraceProjectionEvent(
        source_record_schema_version=record.record_schema_version,
        source_event_hash=record.event_hash,
        source_previous_hash=record.previous_hash,
        source_event_id=str(record.event.event_id),
        trace_id=str(record.event.trace_id),
        task_id=str(record.event.task_id),
        step=record.event.step,
        timestamp=record.event.timestamp,
        event_type=record.event.event_type.value,
        component=record.event.component,
        system_version=str(record.event.system_version),
        input_refs=input_refs[:12],
        output_refs=output_refs[:12],
        metadata=compact_metadata,
        projection_truncated=True,
    )


def load_verified_trace_records(
    path: str | Path,
    *,
    expected_trace_id: str,
    require_complete_final_line: bool,
) -> tuple[tuple[TraceRecord, ...], bool]:
    """Verify all complete source records and report whether a final partial line was ignored."""

    target = Path(path).resolve()
    try:
        payload = target.read_bytes()
    except OSError as exc:
        raise TraceCorruptionError(f"cannot read trace projection source {target}: {exc}") from exc
    partial_ignored = bool(payload) and not payload.endswith(b"\n")
    if partial_ignored:
        if require_complete_final_line:
            raise TraceCorruptionError(
                f"terminal trace has an incomplete final record: {target}"
            )
        boundary = payload.rfind(b"\n")
        payload = b"" if boundary < 0 else payload[: boundary + 1]

    records: list[TraceRecord] = []
    previous_hash: str | None = None
    previous_timestamp: datetime | None = None
    expected_step = 1
    for line_number, raw in enumerate(payload.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            record = TraceRecord.model_validate_json(raw)
        except Exception as exc:
            raise TraceCorruptionError(
                f"invalid trace projection record at line {line_number}: {exc}"
            ) from exc
        if record.record_schema_version != RECORD_SCHEMA_VERSION:
            raise TraceCorruptionError(
                f"unsupported trace record schema at line {line_number}"
            )
        if str(record.event.trace_id) != expected_trace_id:
            raise TraceCorruptionError(
                f"unexpected trace id at line {line_number}: {record.event.trace_id}"
            )
        if record.event.step != expected_step:
            raise TraceCorruptionError(
                f"trace projection expected step {expected_step}, got {record.event.step}"
            )
        if record.previous_hash != previous_hash:
            raise TraceCorruptionError(
                f"trace projection broken previous hash at step {record.event.step}"
            )
        if record.event_hash != _event_hash(record.event, record.previous_hash):
            raise TraceCorruptionError(
                f"trace projection event hash mismatch at step {record.event.step}"
            )
        if previous_timestamp is not None and record.event.timestamp < previous_timestamp:
            raise TraceCorruptionError(
                f"trace projection timestamp moved backwards at step {record.event.step}"
            )
        records.append(record)
        expected_step += 1
        previous_hash = record.event_hash
        previous_timestamp = record.event.timestamp
    return tuple(records), partial_ignored


def build_trace_projection_page(
    *,
    session_id: str,
    trace_path: str | Path | None,
    trace_id: str | None,
    after: int,
    limit: int,
    terminal: bool,
) -> TraceProjectionPage:
    if trace_path is None or trace_id is None:
        return TraceProjectionPage(
            session_id=session_id,
            trace_attached=False,
            after=after,
            limit=limit,
            next_after=after,
            has_more=False,
        )
    TraceId(value=trace_id)
    records, partial_ignored = load_verified_trace_records(
        trace_path,
        expected_trace_id=trace_id,
        require_complete_final_line=terminal,
    )
    available = [record for record in records if record.event.step > after]
    selected = available[:limit]
    projections = tuple(_project_record(record) for record in selected)
    return TraceProjectionPage(
        session_id=session_id,
        trace_attached=True,
        trace_id=trace_id,
        trace_path=str(Path(trace_path).resolve()),
        after=after,
        limit=limit,
        next_after=(projections[-1].step if projections else after),
        has_more=len(available) > len(selected),
        final_partial_line_ignored=partial_ignored,
        events=projections,
    )
