from datetime import datetime, timezone

from harness_x.core import FixedClock, SystemVersion, TaskId, TraceId
from harness_x.core.events import EventType
from harness_x.orchestrator import TaskOrchestrator
from harness_x.telemetry import TraceRecorder, TraceStore


def _recorder(store, *, task: str, trace: str) -> TraceRecorder:
    return TraceRecorder(
        store,
        TraceId(value=trace),
        TaskId(value=task),
        SystemVersion(value="test-v1"),
        FixedClock(datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)),
    )


def test_parent_child_relationship_is_authoritative_and_traced(tmp_path) -> None:
    store = TraceStore(tmp_path / "trace.jsonl")
    parent_recorder = _recorder(store, task="task_parent", trace="trace_parent")
    child_recorder = _recorder(store, task="task_child", trace="trace_child")

    parent = TaskOrchestrator.create(parent_recorder)
    parent.start()
    child = parent.create_child(child_recorder)

    assert parent.session.child_task_ids == (child.session.task_id,)
    assert child.session.parent_task_id == parent.session.task_id

    parent_events = store.events(trace_id=parent_recorder.trace_id)
    assert parent_events[-1].event_type == EventType.TASK_CHILD_ADDED
    assert parent_events[-1].output_refs == [str(child.session.task_id)]

    child_events = store.events(trace_id=child_recorder.trace_id)
    assert child_events[0].event_type == EventType.TASK_CREATED
    assert child_events[0].metadata["parent_task_id"] == str(parent.session.task_id)
