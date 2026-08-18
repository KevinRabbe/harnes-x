import json
from datetime import datetime, timezone

import pytest

from harness_x.core.clock import FixedClock
from harness_x.core.errors import ReplayError, ReplayMismatchError, TraceCorruptionError, TraceError
from harness_x.core.events import EventType, TraceEvent
from harness_x.core.ids import EventId, GoalId, MemoryId, SystemVersion, TaskId, TraceId
from harness_x.telemetry import ReplayState, TraceRecorder, TraceReplayer, TraceStore

NOW = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)


def ids() -> tuple[TraceId, TaskId]:
    return TraceId(value="trace_golden"), TaskId(value="task_golden")


def test_synthetic_trace_replays_and_exports(tmp_path) -> None:
    trace_id, task_id = ids()
    store = TraceStore(tmp_path / "trace.jsonl")
    recorder = TraceRecorder(
        store,
        trace_id,
        task_id,
        SystemVersion(value="0.1.0-alpha.0"),
        FixedClock(NOW),
    )
    goal = GoalId(value="goal_primary")
    memory = MemoryId(value="mem_fact")

    recorder.emit(EventType.TASK_CREATED, "orchestrator")
    recorder.emit(
        EventType.GOAL_CREATED,
        "goals",
        output_refs=[str(goal)],
        metadata={"status": "active"},
    )
    recorder.emit(
        EventType.MODE_CHANGED,
        "orchestrator",
        metadata={"to": "TASK_ACTIVE"},
    )
    recorder.emit(
        EventType.MEMORY_WRITTEN,
        "working_memory",
        output_refs=[str(memory)],
    )
    recorder.emit(
        EventType.BUDGET_CHANGED,
        "budget",
        metadata={"budget": {"max_reasoning_steps": 8}},
    )

    state = TraceReplayer().replay(store.events(trace_id=trace_id))
    assert state.memories == ["mem_fact"]
    assert state.goals == {"goal_primary": "active"}
    assert state.last_step == 5

    fixture = store.export_fixture(trace_id, state, tmp_path / "fixture.json")
    assert TraceReplayer().assert_fixture(fixture) == state

    loaded = json.loads((tmp_path / "fixture.json").read_text())
    assert loaded["expected_state"]["modes"]["task_golden"] == "TASK_ACTIVE"


def test_store_detects_out_of_order_append(tmp_path) -> None:
    trace_id, task_id = ids()
    store = TraceStore(tmp_path / "trace.jsonl")
    event = TraceEvent(
        event_id=EventId(value="event_two"),
        trace_id=trace_id,
        task_id=task_id,
        step=2,
        timestamp=NOW,
        event_type=EventType.TASK_CREATED,
        component="orchestrator",
        system_version=SystemVersion(value="v1"),
    )

    with pytest.raises(TraceError):
        store.append(event)


def test_store_detects_tampering(tmp_path) -> None:
    trace_id, task_id = ids()
    path = tmp_path / "trace.jsonl"
    store = TraceStore(path)
    recorder = TraceRecorder(
        store,
        trace_id,
        task_id,
        SystemVersion(value="v1"),
        FixedClock(NOW),
    )
    recorder.emit(EventType.TASK_CREATED, "orchestrator")

    path.write_text(path.read_text().replace("orchestrator", "tampered"))

    with pytest.raises(TraceCorruptionError):
        store.events()


def test_replayer_detects_semantically_invalid_trace() -> None:
    trace_id, task_id = ids()
    base = dict(
        trace_id=trace_id,
        task_id=task_id,
        timestamp=NOW,
        component="test",
        system_version=SystemVersion(value="v1"),
    )
    events = [
        TraceEvent(
            event_id=EventId(value="event_1"),
            step=1,
            event_type=EventType.TASK_CREATED,
            **base,
        ),
        TraceEvent(
            event_id=EventId(value="event_2"),
            step=2,
            event_type=EventType.MEMORY_EVICTED,
            input_refs=["mem_missing"],
            **base,
        ),
    ]

    with pytest.raises(ReplayError):
        TraceReplayer().replay(events)


def test_fixture_mismatch_detected(tmp_path) -> None:
    trace_id, task_id = ids()
    store = TraceStore(tmp_path / "trace.jsonl")
    recorder = TraceRecorder(
        store,
        trace_id,
        task_id,
        SystemVersion(value="v1"),
        FixedClock(NOW),
    )
    recorder.emit(EventType.TASK_CREATED, "orchestrator")

    wrong = ReplayState(trace_id=trace_id, last_step=99)
    fixture = store.export_fixture(trace_id, wrong)

    with pytest.raises(ReplayMismatchError):
        TraceReplayer().assert_fixture(fixture)


def test_task_and_component_queries(tmp_path) -> None:
    trace_id, task_id = ids()
    store = TraceStore(tmp_path / "trace.jsonl")
    recorder = TraceRecorder(
        store,
        trace_id,
        task_id,
        SystemVersion(value="v1"),
        FixedClock(NOW),
    )
    recorder.emit(EventType.TASK_CREATED, "orchestrator")
    recorder.emit(
        EventType.GATE_DECISION,
        "focus_gate",
        metadata={"decision": "continue"},
    )

    assert len(store.events(task_id=task_id)) == 2
    assert [event.component for event in store.events(component="focus_gate")] == [
        "focus_gate"
    ]
