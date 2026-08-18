from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness_x.config import load_config
from harness_x.core import CandidateId, FixedClock, GoalId, MemoryId, SystemVersion, TaskId, TraceId
from harness_x.core.contracts import Observation
from harness_x.core.events import EventType
from harness_x.core.provenance import Provenance, SourceKind, VerificationState
from harness_x.gates import ComputeGate, FocusGate, MaintenanceGate, RetrievalGate, WriteGate
from harness_x.memory import (
    EpisodeOutcome,
    EpisodicMemory,
    ErrorBuffer,
    GoalMemory,
    GoalStatus,
    WorkingState,
)
from harness_x.orchestrator import OperatingMode, TaskOrchestrator
from harness_x.routines import (
    ConsolidationRoutineRequest,
    RecoveryRoutineRequest,
    RoutineBindings,
    RoutineError,
    RoutineStatus,
    ScriptedReasoningStub,
    TaskRoutineRequest,
    VerificationRoutineRequest,
    build_scripted_routine_engine,
)
from harness_x.telemetry import TraceRecorder, TraceReplayer, TraceStore


def _system(tmp_path, *, suffix: str, working_capacity: int = 10):
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")
    clock = FixedClock(datetime(2026, 8, 18, 17, 0, tzinfo=timezone.utc))
    recorder = TraceRecorder(
        TraceStore(tmp_path / f"{suffix}.jsonl"),
        TraceId(value=f"trace_{suffix}"),
        TaskId(value=f"task_{suffix}"),
        SystemVersion(value="test-v1"),
        clock,
    )
    orchestrator = TaskOrchestrator.create(recorder, budget=config.budget)
    goals = GoalMemory(recorder)
    working = WorkingState(recorder, capacity_units=working_capacity)
    episodic = EpisodicMemory(recorder)
    errors = ErrorBuffer(recorder)
    stub = ScriptedReasoningStub()
    bindings = RoutineBindings(
        orchestrator=orchestrator,
        goals=goals,
        working=working,
        episodic=episodic,
        errors=errors,
        retrieval_gate=RetrievalGate(recorder, config.gates.retrieval),
        write_gate=WriteGate(recorder, config.gates.write),
        focus_gate=FocusGate(recorder, config.gates.focus),
        compute_gate=ComputeGate(recorder, config.gates.compute),
        maintenance_gate=MaintenanceGate(recorder, config.gates.maintenance),
        reasoning_stub=stub,
    )
    engine = build_scripted_routine_engine(bindings)
    provenance = Provenance(
        source_kind=SourceKind.TEST,
        source_ref=f"test:{suffix}",
        created_at=clock.now(),
        system_version=recorder.system_version,
        trace_id=recorder.trace_id,
        verification=VerificationState.VERIFIED,
    )
    return recorder, bindings, engine, provenance


def _active_goal(bindings: RoutineBindings, provenance: Provenance, *, suffix: str):
    goal = bindings.goals.create_goal(
        "Complete deterministic demo task",
        provenance,
        governing_constraints=("Do not bypass verification",),
        completion_criteria=("Expected result matches",),
        goal_id=GoalId(value=f"goal_{suffix}"),
    )
    bindings.orchestrator.start()
    return goal


def test_scripted_task_crosses_real_architecture_without_model(tmp_path) -> None:
    recorder, bindings, engine, provenance = _system(
        tmp_path,
        suffix="routine_success",
    )
    goal = _active_goal(bindings, provenance, suffix="routine_success")
    observation = Observation(
        task_id=recorder.task_id,
        kind="observation",
        content={"input": 21},
        provenance=provenance,
    )

    execution = engine.execute(
        "task",
        TaskRoutineRequest(
            goal_id=goal.goal_id,
            observation=observation,
            observation_priority=0.9,
            action_name="double",
            action_arguments={"value": 21},
            action_result={"value": 42},
            expected_result={"value": 42},
            required_result_keys=("value",),
            episode_summary="Doubled the deterministic input and verified the result",
        ),
    )

    assert execution.result.status == RoutineStatus.SUCCEEDED
    assert bindings.orchestrator.session.mode == OperatingMode.COMPLETE
    assert bindings.goals.get(goal.goal_id).status == GoalStatus.COMPLETE
    assert bindings.reasoning_stub.calls == 1
    assert len(bindings.episodic.all()) == 1
    assert bindings.episodic.all()[0].outcome == EpisodeOutcome.SUCCESS
    assert any(item.pinned for item in bindings.working.items())

    events = recorder.store.events(trace_id=recorder.trace_id)
    gate_components = {
        event.component
        for event in events
        if event.event_type == EventType.GATE_DECISION
    }
    assert {
        "gate.retrieval",
        "gate.write",
        "gate.focus",
        "gate.compute",
        "gate.maintenance",
    }.issubset(gate_components)

    started = [
        event.metadata["routine_name"]
        for event in events
        if event.event_type == EventType.ROUTINE_STARTED
    ]
    finished = [
        event.component
        for event in events
        if event.event_type == EventType.ROUTINE_FINISHED
    ]
    assert started == ["task", "verification"]
    assert "routine.task" in finished
    assert "routine.verification" in finished
    assert any(
        event.event_type == EventType.VERIFICATION_COMPLETED
        and event.metadata["accepted"] is True
        for event in events
    )
    assert any(
        event.event_type == EventType.ACTION_EXECUTED
        and event.metadata["external_side_effect"] is False
        for event in events
    )

    replay = TraceReplayer().replay(events)
    assert replay.modes[str(recorder.task_id)] == OperatingMode.COMPLETE.value


def test_failed_verification_enters_recovery_and_reconstructs_state(tmp_path) -> None:
    recorder, bindings, engine, provenance = _system(
        tmp_path,
        suffix="routine_recovery",
    )
    goal = _active_goal(bindings, provenance, suffix="routine_recovery")
    observation = Observation(
        task_id=recorder.task_id,
        kind="observation",
        content={"input": 7},
        provenance=provenance,
    )

    failed = engine.execute(
        "task",
        TaskRoutineRequest(
            goal_id=goal.goal_id,
            observation=observation,
            action_name="triple",
            action_arguments={"value": 7},
            action_result={"value": 20},
            expected_result={"value": 21},
            episode_summary="Attempted to triple the input",
        ),
    )

    assert failed.result.status == RoutineStatus.BLOCKED
    assert bindings.orchestrator.session.mode == OperatingMode.RECOVERY
    error_id = MemoryId(value=failed.result.data["error_memory_id"])
    assert bindings.episodic.all()[0].outcome == EpisodeOutcome.FAILURE

    recovered = engine.execute(
        "recovery",
        RecoveryRoutineRequest(error_memory_id=error_id),
    )
    assert recovered.result.status == RoutineStatus.SUCCEEDED
    assert bindings.orchestrator.session.mode == OperatingMode.TASK_ACTIVE
    assert any(
        item.kind == "recovery_context" for item in bindings.working.items()
    )
    assert bindings.goals.get(goal.goal_id).status == GoalStatus.ACTIVE


def test_maintenance_gate_can_interrupt_task_before_stub_decision(tmp_path) -> None:
    recorder, bindings, engine, provenance = _system(
        tmp_path,
        suffix="routine_maintenance",
        working_capacity=1,
    )
    goal = _active_goal(bindings, provenance, suffix="routine_maintenance")
    observation = Observation(
        task_id=recorder.task_id,
        kind="observation",
        content={"pressure": "high"},
        provenance=provenance,
    )

    execution = engine.execute(
        "task",
        TaskRoutineRequest(
            goal_id=goal.goal_id,
            observation=observation,
            observation_size_units=1,
            action_name="should_not_run",
            action_result={"ok": True},
            expected_result={"ok": True},
            episode_summary="This should stop for maintenance first",
        ),
    )

    assert execution.result.status == RoutineStatus.BLOCKED
    assert execution.result.data["reason"] == "maintenance_required"
    assert bindings.orchestrator.session.mode == OperatingMode.MAINTENANCE
    assert bindings.reasoning_stub.calls == 0


def test_consolidation_returns_summary_without_semantic_promotion(tmp_path) -> None:
    recorder, bindings, engine, provenance = _system(
        tmp_path,
        suffix="routine_consolidation",
    )
    _active_goal(bindings, provenance, suffix="routine_consolidation")
    last_step = recorder.store.next_step(recorder.trace_id) - 1
    episode = bindings.episodic.record(
        start_step=1,
        end_step=last_step,
        summary="A compact prior episode",
        outcome=EpisodeOutcome.SUCCESS,
        provenance=provenance,
    )
    bindings.orchestrator.enter_maintenance("scheduled_maintenance")
    bindings.orchestrator.transition(
        OperatingMode.CONSOLIDATION,
        "consolidate_recent_episode",
    )

    execution = engine.execute(
        "consolidation",
        ConsolidationRoutineRequest(episode_ids=(episode.memory_id,)),
    )

    summary = execution.result.data["summary"]
    assert execution.result.status == RoutineStatus.SUCCEEDED
    assert summary["promoted"] is False
    assert summary["outcome_counts"] == {"success": 1}
    assert len(summary["fingerprint"]) == 64
    assert len(bindings.episodic.all()) == 1
    assert bindings.orchestrator.session.mode == OperatingMode.MAINTENANCE


def test_routine_preconditions_are_enforced_before_trace_start(tmp_path) -> None:
    recorder, bindings, engine, provenance = _system(
        tmp_path,
        suffix="routine_precondition",
    )
    _active_goal(bindings, provenance, suffix="routine_precondition")
    before = len(recorder.store.events(trace_id=recorder.trace_id))

    with pytest.raises(RoutineError):
        engine.execute(
            "verification",
            VerificationRoutineRequest(
                candidate_id=CandidateId(value="candidate_precondition"),
                actual={"ok": True},
                expected={"ok": True},
                provenance=provenance,
            ),
        )

    after_events = recorder.store.events(trace_id=recorder.trace_id)
    assert len(after_events) == before
    assert not any(
        event.event_type == EventType.ROUTINE_STARTED
        and event.metadata.get("routine_name") == "verification"
        for event in after_events
    )
