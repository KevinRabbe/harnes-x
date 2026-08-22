from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from harness_x.app_server.trace_projection import (
    build_trace_projection_page,
    load_verified_trace_records,
)
from harness_x.core import EventId, EventType, SystemVersion, TaskId, TraceEvent, TraceId
from harness_x.core.errors import TraceCorruptionError
from harness_x.telemetry import TraceStore
from harness_x.telemetry.trace_store import TraceRecord


def _trace(tmp_path: Path):
    trace_id = TraceId.new()
    task_id = TaskId.new()
    path = tmp_path / f"{trace_id.value}.jsonl"
    store = TraceStore(path)
    base = datetime(2026, 8, 22, tzinfo=timezone.utc)
    first = TraceEvent(
        event_id=EventId.new(),
        trace_id=trace_id,
        task_id=task_id,
        step=1,
        timestamp=base,
        event_type=EventType.REASONING_REQUESTED,
        component="reasoning.service",
        system_version=SystemVersion(value="test"),
        input_refs=["goal:1"],
        metadata={
            "api_key": "must-not-stream",
            "max_output_tokens": 4096,
            "prompt_summary": "x" * 6000,
        },
    )
    second = TraceEvent(
        event_id=EventId.new(),
        trace_id=trace_id,
        task_id=task_id,
        step=2,
        timestamp=base + timedelta(seconds=1),
        event_type=EventType.TOOL_EXECUTION_FINISHED,
        component="tools.executor",
        system_version=SystemVersion(value="test"),
        output_refs=["tool-result:1"],
        metadata={"tool_name": "workspace_read", "success": True},
    )
    store.append(first)
    store.append(second)
    return path, trace_id, task_id


def test_projection_preserves_source_chain_identity_and_redacts_credentials(tmp_path: Path) -> None:
    path, trace_id, _ = _trace(tmp_path)
    raw_first = TraceRecord.model_validate_json(
        path.read_text(encoding="utf-8").splitlines()[0]
    )

    page = build_trace_projection_page(
        session_id="app_" + "1" * 32,
        trace_path=path,
        trace_id=trace_id.value,
        after=0,
        limit=20,
        terminal=True,
    )

    assert page.trace_attached is True
    assert tuple(item.step for item in page.events) == (1, 2)
    first = page.events[0]
    assert first.source_event_hash == raw_first.event_hash
    assert first.source_previous_hash is None
    assert first.metadata["api_key"] == "<redacted>"
    assert first.metadata["max_output_tokens"] == 4096
    assert "must-not-stream" not in first.model_dump_json()
    assert first.projection_truncated is True
    assert len(first.model_dump_json()) <= 16000


def test_projection_pages_by_authoritative_trace_step(tmp_path: Path) -> None:
    path, trace_id, _ = _trace(tmp_path)

    first_page = build_trace_projection_page(
        session_id="app_" + "2" * 32,
        trace_path=path,
        trace_id=trace_id.value,
        after=0,
        limit=1,
        terminal=True,
    )
    second_page = build_trace_projection_page(
        session_id="app_" + "2" * 32,
        trace_path=path,
        trace_id=trace_id.value,
        after=first_page.next_after,
        limit=1,
        terminal=True,
    )

    assert first_page.has_more is True
    assert first_page.next_after == 1
    assert tuple(item.step for item in second_page.events) == (2,)
    assert second_page.has_more is False
    assert second_page.next_after == 2


def test_active_projection_ignores_only_incomplete_final_line(tmp_path: Path) -> None:
    path, trace_id, _ = _trace(tmp_path)
    with path.open("ab") as handle:
        handle.write(b'{"record_schema_version":"1"')

    records, partial = load_verified_trace_records(
        path,
        expected_trace_id=trace_id.value,
        require_complete_final_line=False,
    )
    assert len(records) == 2
    assert partial is True

    page = build_trace_projection_page(
        session_id="app_" + "3" * 32,
        trace_path=path,
        trace_id=trace_id.value,
        after=0,
        limit=20,
        terminal=False,
    )
    assert len(page.events) == 2
    assert page.final_partial_line_ignored is True

    with pytest.raises(TraceCorruptionError, match="incomplete final record"):
        build_trace_projection_page(
            session_id="app_" + "3" * 32,
            trace_path=path,
            trace_id=trace_id.value,
            after=0,
            limit=20,
            terminal=True,
        )


def test_complete_trace_tamper_is_rejected_not_redacted_away(tmp_path: Path) -> None:
    path, trace_id, _ = _trace(tmp_path)
    rows = path.read_text(encoding="utf-8").splitlines()
    raw = json.loads(rows[0])
    raw["event"]["metadata"]["tool_name"] = "tampered"
    rows[0] = json.dumps(raw, separators=(",", ":"))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    with pytest.raises(TraceCorruptionError, match="event hash mismatch"):
        build_trace_projection_page(
            session_id="app_" + "4" * 32,
            trace_path=path,
            trace_id=trace_id.value,
            after=0,
            limit=20,
            terminal=True,
        )


def test_unattached_trace_page_is_explicitly_empty() -> None:
    page = build_trace_projection_page(
        session_id="app_" + "5" * 32,
        trace_path=None,
        trace_id=None,
        after=7,
        limit=20,
        terminal=False,
    )
    assert page.trace_attached is False
    assert page.next_after == 7
    assert page.events == ()
