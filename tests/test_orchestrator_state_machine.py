from datetime import datetime, timezone

import pytest

from harness_x.core import (
    BudgetExhaustedError,
    ComputeBudget,
    FixedClock,
    InvalidTransitionError,
    SystemVersion,
    TaskId,
    TraceId,
)
from harness_x.core.events import EventType
from harness_x.orchestrator import (
    BudgetDelta,
    OperatingMode,
    SchedulerHooks,
    TaskOrchestrator,
)
from harness_x.telemetry import TraceRecorder, TraceReplayer, TraceStore


def _recorder(tmp_path, *, task="task_root", trace="trace_root") -> TraceRecorder:
    return TraceRecorder(
        TraceStore(tmp_path / "trace.jsonl"),
        TraceId(value=trace),
        TaskId(value=task),
        SystemVersion(value="test-v1"),
        FixedClock(datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)),
    )


def test_normal_lifecycle_is_traced_and_replayable(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    task = TaskOrchestrator.create(recorder)

    assert task.session.mode == OperatingMode.READY
    task.start()
    task.enter_verification("result_ready")
    task.complete("verified")

    events = recorder.store.events(trace_id=recorder.trace_id)
    assert [event.event_type for event in events] == [
        EventType.TASK_CREATED,
        EventType.MODE_CHANGED,
        EventType.MODE_CHANGED,
        EventType.MODE_CHANGED,
    ]
    replay = TraceReplayer().replay(events)
    assert replay.modes[str(recorder.task_id)] == OperatingMode.COMPLETE.value


def test_illegal_transition_is_rejected_without_emitting_event(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    task = TaskOrchestrator.create(recorder)

    with pytest.raises(InvalidTransitionError):
        task.complete("cannot_skip_execution")

    assert task.session.mode == OperatingMode.READY
    assert len(recorder.store.events(trace_id=recorder.trace_id)) == 1


def test_recovery_and_maintenance_are_explicit_modes(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    task = TaskOrchestrator.create(recorder)
    task.start()
    task.enter_recovery("tool_failed")
    task.enter_maintenance("repeated_failure_pattern")
    task.transition(OperatingMode.TASK_ACTIVE, "maintenance_finished")

    assert task.session.mode == OperatingMode.TASK_ACTIVE


def test_terminal_task_cannot_restart(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    task = TaskOrchestrator.create(recorder)
    task.start()
    task.complete("done")

    with pytest.raises(InvalidTransitionError):
        task.start("restart")


def test_budget_exhaustion_suspends_before_overrun(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    task = TaskOrchestrator.create(
        recorder,
        budget=ComputeBudget(
            max_reasoning_steps=1,
            max_tool_actions=2,
            max_output_tokens=100,
        ),
    )
    task.start()
    task.consume_budget(BudgetDelta(reasoning_steps=1), reason="reasoning_step")

    with pytest.raises(BudgetExhaustedError):
        task.consume_budget(BudgetDelta(reasoning_steps=1), reason="reasoning_step")

    assert task.session.usage.reasoning_steps == 1
    assert task.session.mode == OperatingMode.SUSPENDED
    assert task.session.resume_mode == OperatingMode.TASK_ACTIVE

    events = recorder.store.events(trace_id=recorder.trace_id)
    rejected = [
        event
        for event in events
        if event.event_type == EventType.BUDGET_CHANGED
        and event.metadata.get("accepted") is False
    ]
    assert len(rejected) == 1
    assert rejected[0].metadata["exhausted"] == ["reasoning_steps"]


def test_scheduler_hooks_observe_but_do_not_own_state(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    hooks = SchedulerHooks()
    transitions = []
    budgets = []
    hooks.on_transition(transitions.append)
    hooks.on_budget_change(budgets.append)
    task = TaskOrchestrator.create(recorder, hooks=hooks)

    task.start()
    task.consume_budget(BudgetDelta(tool_actions=1), reason="tool_call")

    assert [(notice.source, notice.target) for notice in transitions] == [
        (OperatingMode.READY, OperatingMode.TASK_ACTIVE)
    ]
    assert budgets[0].snapshot.usage.tool_actions == 1
    assert task.session.usage.tool_actions == 1
