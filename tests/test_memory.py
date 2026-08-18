from datetime import datetime, timezone

import pytest

from harness_x.core import (
    FixedClock,
    GoalTransitionError,
    MemoryCapacityError,
    MemoryNotFoundError,
    Provenance,
    SourceKind,
    SystemVersion,
    TaskId,
    TraceId,
    VerificationState,
)
from harness_x.memory import (
    EpisodeOutcome,
    EpisodicMemory,
    ErrorBuffer,
    ErrorSeverity,
    ErrorStatus,
    GoalMemory,
    GoalStatus,
    WorkingState,
)
from harness_x.orchestrator import TaskOrchestrator
from harness_x.telemetry import TraceRecorder, TraceStore


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _context(tmp_path):
    recorder = TraceRecorder(
        TraceStore(tmp_path / "trace.jsonl"),
        TraceId(value="trace_memory"),
        TaskId(value="task_memory"),
        SystemVersion(value="test-v1"),
        FixedClock(NOW),
    )
    TaskOrchestrator.create(recorder)
    provenance = Provenance(
        source_kind=SourceKind.TEST,
        source_ref="test:memory",
        created_at=NOW,
        system_version=recorder.system_version,
        trace_id=recorder.trace_id,
        verification=VerificationState.VERIFIED,
    )
    return recorder, provenance


def test_goal_survives_working_state_churn(tmp_path) -> None:
    recorder, provenance = _context(tmp_path)
    goals = GoalMemory(recorder)
    working = WorkingState(recorder, capacity_units=3)

    goal = goals.create_goal(
        "Finish the autonomous task",
        provenance,
        governing_constraints=("Do not lose the primary objective",),
        completion_criteria=("Verified final result exists",),
    )

    for index in range(6):
        working.add(
            kind="scratch",
            content={"index": index},
            priority=0.1 + index * 0.01,
            size_units=1,
            source=f"iteration:{index}",
            provenance=provenance,
        )

    restored = goals.retrieve(goal.goal_id)
    assert restored == goal
    assert restored.governing_constraints[0].pinned is True
    assert restored.governing_constraints[0].text == "Do not lose the primary objective"
    assert len(working.items()) == 3


def test_working_state_eviction_is_deterministic_and_never_evicts_pinned(tmp_path) -> None:
    recorder, provenance = _context(tmp_path)
    working = WorkingState(recorder, capacity_units=5)

    pinned = working.add(
        kind="governing",
        content={"goal": "stay"},
        priority=0.0,
        size_units=2,
        source="goal-memory",
        provenance=provenance,
        pinned=True,
    )
    low = working.add(
        kind="scratch",
        content={"value": "low"},
        priority=0.1,
        size_units=1,
        source="scratch",
        provenance=provenance,
    )
    high = working.add(
        kind="scratch",
        content={"value": "high"},
        priority=0.9,
        size_units=1,
        source="scratch",
        provenance=provenance,
    )

    working.add(
        kind="new",
        content={"value": "new"},
        priority=0.5,
        size_units=2,
        source="scratch",
        provenance=provenance,
    )

    assert working.get(pinned.memory_id).pinned is True
    assert working.get(high.memory_id).content["value"] == "high"
    with pytest.raises(MemoryNotFoundError):
        working.get(low.memory_id)
    assert working.pressure.used_units == 5
    assert working.pressure.pressure == 1.0


def test_working_state_refuses_to_evict_only_pinned_state(tmp_path) -> None:
    recorder, provenance = _context(tmp_path)
    working = WorkingState(recorder, capacity_units=2)
    pinned = working.add(
        kind="governing",
        content={},
        priority=0.0,
        size_units=2,
        source="goal-memory",
        provenance=provenance,
        pinned=True,
    )

    with pytest.raises(MemoryCapacityError):
        working.add(
            kind="scratch",
            content={},
            priority=1.0,
            size_units=1,
            source="scratch",
            provenance=provenance,
        )

    assert working.items() == (pinned,)


def test_working_retrieval_updates_lru_without_changing_priority(tmp_path) -> None:
    recorder, provenance = _context(tmp_path)
    working = WorkingState(recorder, capacity_units=3)
    item = working.add(
        kind="dependency",
        content={"name": "x"},
        priority=0.4,
        size_units=1,
        source="planner",
        provenance=provenance,
    )

    retrieved = working.retrieve(item.memory_id)
    assert retrieved.last_used_step > item.last_used_step
    assert retrieved.priority == item.priority


def test_goal_history_subgoals_and_terminal_state_are_authoritative(tmp_path) -> None:
    recorder, provenance = _context(tmp_path)
    goals = GoalMemory(recorder)
    parent = goals.create_goal("Parent", provenance)
    child = goals.create_subgoal(parent.goal_id, "Child", provenance)

    assert child.parent_goal_id == parent.goal_id
    goals.update_status(child.goal_id, GoalStatus.BLOCKED, reason="waiting")
    goals.update_status(child.goal_id, GoalStatus.ACTIVE, reason="dependency_ready")
    completed = goals.update_status(child.goal_id, GoalStatus.COMPLETE, reason="verified")

    history = goals.history(child.goal_id)
    assert [entry.status for entry in history] == [
        GoalStatus.ACTIVE,
        GoalStatus.BLOCKED,
        GoalStatus.ACTIVE,
        GoalStatus.COMPLETE,
    ]
    assert completed.revision == 4

    with pytest.raises(GoalTransitionError):
        goals.update_status(child.goal_id, GoalStatus.ACTIVE, reason="reopen")


def test_episodic_memory_retrieves_previous_failed_attempt_without_embeddings(tmp_path) -> None:
    recorder, provenance = _context(tmp_path)
    episodes = EpisodicMemory(recorder)
    episodes.record(
        start_step=1,
        end_step=1,
        summary="Compile attempt failed because dependency alpha was missing",
        outcome=EpisodeOutcome.FAILURE,
        tags=("compile", "dependency"),
        entities=("alpha",),
        provenance=provenance,
    )
    episodes.record(
        start_step=1,
        end_step=1,
        summary="Documentation task completed successfully",
        outcome=EpisodeOutcome.SUCCESS,
        tags=("docs",),
        provenance=provenance,
    )

    results = episodes.search(
        query="dependency alpha",
        outcome=EpisodeOutcome.FAILURE,
    )
    assert len(results) == 1
    assert results[0].outcome == EpisodeOutcome.FAILURE
    assert "missing" in results[0].summary


def test_error_hypothesis_never_becomes_confirmed_without_resolution_evidence(tmp_path) -> None:
    recorder, provenance = _context(tmp_path)
    source_event = recorder.store.events(trace_id=recorder.trace_id)[0]
    errors = ErrorBuffer(recorder)
    record = errors.record(
        anomaly="Tool result contradicted expected state",
        source_event_id=source_event.event_id,
        severity=ErrorSeverity.ERROR,
        provenance=provenance,
    )

    investigating = errors.add_suspected_cause(
        record.memory_id,
        "The cached dependency graph may be stale",
        evidence_refs=(str(source_event.event_id),),
        confidence=0.6,
    )

    assert investigating.status == ErrorStatus.INVESTIGATING
    assert investigating.confirmed_cause is None
    assert investigating.resolution_evidence == ()
    assert investigating.suspected_causes[0].description.startswith("The cached")

    with pytest.raises(ValueError):
        errors.resolve(
            record.memory_id,
            resolution_evidence=(),
            confirmed_cause="stale dependency graph",
        )

    resolved = errors.resolve(
        record.memory_id,
        resolution_evidence=("tool:dependency-refresh",),
        confirmed_cause="stale dependency graph",
    )
    assert resolved.status == ErrorStatus.RESOLVED
    assert resolved.confirmed_cause == "stale dependency graph"
    assert resolved.resolution_evidence == ("tool:dependency-refresh",)
    assert errors.unresolved() == ()
