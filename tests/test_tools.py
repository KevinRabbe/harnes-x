from datetime import datetime, timezone
from pathlib import Path

from harness_x.config import load_config
from harness_x.core import ComputeBudget, FixedClock, GoalId, SystemVersion, TaskId, TraceId
from harness_x.core.contracts import ActionProposal, Observation
from harness_x.core.events import EventType
from harness_x.core.ids import CandidateId
from harness_x.core.provenance import Provenance, SourceKind, VerificationState
from harness_x.gates import ComputeGate, FocusGate, MaintenanceGate, RetrievalGate, WriteGate
from harness_x.memory import EpisodicMemory, ErrorBuffer, GoalMemory, GoalStatus, WorkingState
from harness_x.orchestrator import OperatingMode, TaskOrchestrator
from harness_x.routines import (
    RoutineBindings,
    RoutineStatus,
    ToolAwareScriptedReasoningStub,
    ToolTaskRoutineRequest,
    build_tool_routine_engine,
)
from harness_x.telemetry import TraceRecorder, TraceReplayer, TraceStore
from harness_x.tools import ToolExecutor, ToolStatus, build_default_registry


def _provenance(recorder: TraceRecorder) -> Provenance:
    return Provenance(
        source_kind=SourceKind.TEST,
        source_ref="test:tools",
        created_at=recorder.clock.now(),
        system_version=recorder.system_version,
        trace_id=recorder.trace_id,
        verification=VerificationState.VERIFIED,
    )


def _runtime(tmp_path, *, suffix: str, budget: ComputeBudget | None = None):
    recorder = TraceRecorder(
        TraceStore(tmp_path / f"{suffix}.jsonl"),
        TraceId(value=f"trace_{suffix}"),
        TaskId(value=f"task_{suffix}"),
        SystemVersion(value="test-v1"),
        FixedClock(datetime(2026, 8, 18, 18, 0, tzinfo=timezone.utc)),
    )
    orchestrator = TaskOrchestrator.create(recorder, budget=budget or ComputeBudget())
    orchestrator.start()
    registry = build_default_registry(
        sandbox_root=tmp_path / f"sandbox_{suffix}",
        key_values={"answer": "42"},
    )
    executor = ToolExecutor(registry, recorder, orchestrator)
    return recorder, orchestrator, executor


def _proposal(recorder: TraceRecorder, *, candidate: str, tool: str, arguments: dict):
    return ActionProposal(
        candidate_id=CandidateId(value=f"candidate_{candidate}"),
        task_id=recorder.task_id,
        tool_name=tool,
        arguments=arguments,
        provenance=_provenance(recorder),
    )


def test_unregistered_tool_is_denied_without_execution_or_budget_use(tmp_path) -> None:
    recorder, orchestrator, executor = _runtime(tmp_path, suffix="tool_missing")
    proposal = _proposal(
        recorder,
        candidate="missing",
        tool="does_not_exist",
        arguments={},
    )

    result = executor.execute(
        proposal,
        routine_allowed_tools=("does_not_exist",),
        granted_permissions=frozenset(),
    )

    assert result.status == ToolStatus.NOT_FOUND
    assert orchestrator.session.usage.tool_actions == 0
    events = recorder.store.events(trace_id=recorder.trace_id)
    assert any(event.event_type == EventType.TOOL_PERMISSION_CHECKED for event in events)
    assert not any(event.event_type == EventType.ACTION_EXECUTED for event in events)


def test_permission_failure_is_observable_then_same_tool_can_recover(tmp_path) -> None:
    recorder, orchestrator, executor = _runtime(tmp_path, suffix="tool_permission")
    proposal = _proposal(
        recorder,
        candidate="permission",
        tool="kv_read",
        arguments={"key": "answer"},
    )

    denied = executor.execute(
        proposal,
        routine_allowed_tools=("kv_read",),
        granted_permissions=frozenset(),
    )
    assert denied.status == ToolStatus.DENIED
    assert orchestrator.session.mode == OperatingMode.TASK_ACTIVE
    assert orchestrator.session.usage.tool_actions == 0

    allowed = executor.execute(
        proposal,
        routine_allowed_tools=("kv_read",),
        granted_permissions=frozenset({"kv.read"}),
    )
    assert allowed.status == ToolStatus.SUCCEEDED
    assert allowed.output == {"found": True, "value": "42"}
    assert orchestrator.session.usage.tool_actions == 1


def test_input_is_validated_before_tool_budget_or_execution(tmp_path) -> None:
    recorder, orchestrator, executor = _runtime(tmp_path, suffix="tool_input")
    proposal = _proposal(
        recorder,
        candidate="badinput",
        tool="calculator",
        arguments={"operation": "multiply", "a": 3},
    )

    result = executor.execute(
        proposal,
        routine_allowed_tools=("calculator",),
        granted_permissions=frozenset(),
    )

    assert result.status == ToolStatus.INVALID_INPUT
    assert orchestrator.session.usage.tool_actions == 0
    assert not any(
        event.event_type == EventType.ACTION_EXECUTED
        for event in recorder.store.events(trace_id=recorder.trace_id)
    )


def test_tool_failure_and_timeout_are_normalized_not_raised(tmp_path) -> None:
    recorder, orchestrator, executor = _runtime(tmp_path, suffix="tool_failures")
    failed = executor.execute(
        _proposal(
            recorder,
            candidate="failure",
            tool="unreliable",
            arguments={"value": "x", "fail": True},
        ),
        routine_allowed_tools=("unreliable",),
        granted_permissions=frozenset({"test.unreliable"}),
    )
    timed_out = executor.execute(
        _proposal(
            recorder,
            candidate="timeout",
            tool="unreliable",
            arguments={"value": "x", "delay_seconds": 0.1},
        ),
        routine_allowed_tools=("unreliable",),
        granted_permissions=frozenset({"test.unreliable"}),
    )

    assert failed.status == ToolStatus.FAILED
    assert timed_out.status == ToolStatus.TIMEOUT
    assert orchestrator.session.usage.tool_actions == 2


def test_sandbox_writer_cannot_escape_declared_root(tmp_path) -> None:
    recorder, _, executor = _runtime(tmp_path, suffix="tool_sandbox")
    escaped = executor.execute(
        _proposal(
            recorder,
            candidate="escape",
            tool="sandbox_write",
            arguments={"relative_path": "../escape.txt", "content": "no"},
        ),
        routine_allowed_tools=("sandbox_write",),
        granted_permissions=frozenset({"sandbox.write"}),
    )
    assert escaped.status == ToolStatus.FAILED
    assert not (tmp_path / "escape.txt").exists()


def _tool_system(tmp_path, *, suffix: str, permissions: frozenset[str] = frozenset()):
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")
    recorder = TraceRecorder(
        TraceStore(tmp_path / f"{suffix}.jsonl"),
        TraceId(value=f"trace_{suffix}"),
        TaskId(value=f"task_{suffix}"),
        SystemVersion(value="test-v1"),
        FixedClock(datetime(2026, 8, 18, 18, 30, tzinfo=timezone.utc)),
    )
    orchestrator = TaskOrchestrator.create(recorder, budget=config.budget)
    registry = build_default_registry(
        sandbox_root=tmp_path / f"sandbox_{suffix}",
        key_values={"answer": "42"},
    )
    executor = ToolExecutor(registry, recorder, orchestrator)
    stub = ToolAwareScriptedReasoningStub()
    bindings = RoutineBindings(
        orchestrator=orchestrator,
        goals=GoalMemory(recorder),
        working=WorkingState(recorder, capacity_units=10),
        episodic=EpisodicMemory(recorder),
        errors=ErrorBuffer(recorder),
        retrieval_gate=RetrievalGate(recorder, config.gates.retrieval),
        write_gate=WriteGate(recorder, config.gates.write),
        focus_gate=FocusGate(recorder, config.gates.focus),
        compute_gate=ComputeGate(recorder, config.gates.compute),
        maintenance_gate=MaintenanceGate(recorder, config.gates.maintenance),
        reasoning_stub=stub,
        tool_executor=executor,
        tool_permissions=permissions,
    )
    return recorder, bindings, build_tool_routine_engine(bindings), _provenance(recorder)


def _active_goal(bindings: RoutineBindings, provenance: Provenance, suffix: str):
    goal = bindings.goals.create_goal(
        "Complete tool-backed deterministic task",
        provenance,
        governing_constraints=("Never bypass tool permission checks",),
        completion_criteria=("Tool output is independently verified",),
        goal_id=GoalId(value=f"goal_{suffix}"),
    )
    bindings.orchestrator.start()
    return goal


def test_tool_backed_task_crosses_proposal_permission_execution_observation(tmp_path) -> None:
    recorder, bindings, engine, provenance = _tool_system(
        tmp_path,
        suffix="tool_task_success",
    )
    goal = _active_goal(bindings, provenance, "tool_task_success")
    execution = engine.execute(
        "task",
        ToolTaskRoutineRequest(
            goal_id=goal.goal_id,
            observation=Observation(
                task_id=recorder.task_id,
                kind="number",
                content={"value": 21},
                provenance=provenance,
            ),
            tool_name="calculator",
            tool_arguments={"operation": "multiply", "a": 21, "b": 2},
            expected_result={"value": 42.0},
            required_result_keys=("value",),
            episode_summary="Calculated and independently verified the doubled value",
        ),
    )

    assert execution.result.status == RoutineStatus.SUCCEEDED
    assert bindings.orchestrator.session.mode == OperatingMode.COMPLETE
    assert bindings.goals.get(goal.goal_id).status == GoalStatus.COMPLETE
    assert bindings.orchestrator.session.usage.reasoning_steps == 1
    assert bindings.orchestrator.session.usage.tool_actions == 1
    assert execution.result.data["tool_result"]["status"] == "succeeded"

    events = recorder.store.events(trace_id=recorder.trace_id)
    ordered_types = [event.event_type for event in events]
    assert EventType.ACTION_PROPOSED in ordered_types
    assert EventType.TOOL_PERMISSION_CHECKED in ordered_types
    assert EventType.TOOL_EXECUTION_FINISHED in ordered_types
    assert EventType.ACTION_EXECUTED in ordered_types
    assert any(
        event.event_type == EventType.OBSERVATION_RECEIVED
        and event.component == "routine.task.tool_result"
        for event in events
    )
    assert ordered_types.index(EventType.ACTION_PROPOSED) < ordered_types.index(
        EventType.TOOL_PERMISSION_CHECKED
    ) < ordered_types.index(EventType.ACTION_EXECUTED)

    replay = TraceReplayer().replay(events)
    assert replay.modes[str(recorder.task_id)] == OperatingMode.COMPLETE.value


def test_tool_permission_denial_becomes_recoverable_task_failure(tmp_path) -> None:
    recorder, bindings, engine, provenance = _tool_system(
        tmp_path,
        suffix="tool_task_denied",
        permissions=frozenset(),
    )
    goal = _active_goal(bindings, provenance, "tool_task_denied")

    execution = engine.execute(
        "task",
        ToolTaskRoutineRequest(
            goal_id=goal.goal_id,
            observation=Observation(
                task_id=recorder.task_id,
                kind="lookup",
                content={"key": "answer"},
                provenance=provenance,
            ),
            tool_name="kv_read",
            tool_arguments={"key": "answer"},
            expected_result={"found": True, "value": "42"},
            episode_summary="Read the answer from the declared key/value tool",
        ),
    )

    assert execution.result.status == RoutineStatus.BLOCKED
    assert execution.result.data["tool_status"] == "denied"
    assert bindings.orchestrator.session.mode == OperatingMode.RECOVERY
    assert bindings.orchestrator.session.usage.tool_actions == 0
    assert len(bindings.errors.unresolved()) == 1
    assert len(bindings.episodic.all()) == 1
    assert bindings.episodic.all()[0].outcome.value == "failure"
    assert not any(
        event.event_type == EventType.ACTION_EXECUTED
        for event in recorder.store.events(trace_id=recorder.trace_id)
    )
