"""Shared runtime and metric derivation for Milestone 8 autonomy scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from harness_x.config import HarnessConfig
from harness_x.core import ComputeBudget, FixedClock, GoalId, SystemVersion, TaskId, TraceId
from harness_x.core.events import EventType
from harness_x.core.provenance import Provenance, SourceKind, VerificationState
from harness_x.gates import ComputeGate, FocusGate, MaintenanceGate, RetrievalGate, WriteGate
from harness_x.memory import (
    EpisodicMemory,
    ErrorBuffer,
    GoalMemory,
    GoalStatus,
    ProceduralMemory,
    SemanticMemory,
    WorkingState,
)
from harness_x.orchestrator import OperatingMode, TaskOrchestrator, can_transition
from harness_x.routines import (
    RecoveryRoutine,
    RoutineBindings,
    RoutineEngine,
    ToolAwareScriptedReasoningStub,
    VerificationRoutine,
)
from harness_x.telemetry import TraceRecorder, TraceReplayer, TraceStore
from harness_x.tools import ToolExecutor, ToolRegistry, build_default_registry

from .models import ScenarioMetrics
from .routines import BenchmarkMaintenanceRoutine, BenchmarkStepRoutine


AUTHORITATIVE_EVENT_TYPES = frozenset(
    {
        EventType.GOAL_CREATED,
        EventType.GOAL_UPDATED,
        EventType.MODE_CHANGED,
        EventType.CHECKPOINT_CREATED,
        EventType.MEMORY_WRITTEN,
        EventType.MEMORY_EVICTED,
        EventType.MEMORY_RETRIEVED,
        EventType.BUDGET_CHANGED,
        EventType.CANDIDATE_CREATED,
        EventType.CANDIDATE_EVALUATED,
        EventType.CANDIDATE_PROMOTED,
        EventType.CANDIDATE_REJECTED,
        EventType.CANDIDATE_INVALIDATED,
        EventType.ACTION_PROPOSED,
        EventType.TOOL_PERMISSION_CHECKED,
        EventType.TOOL_EXECUTION_FINISHED,
        EventType.ACTION_EXECUTED,
        EventType.VERIFICATION_COMPLETED,
        EventType.ERROR_RECORDED,
    }
)


@dataclass
class BenchmarkRuntime:
    """One isolated scripted scenario using the real architecture owners."""

    name: str
    root: Path
    config: HarnessConfig
    recorder: TraceRecorder
    orchestrator: TaskOrchestrator
    goals: GoalMemory
    working: WorkingState
    episodic: EpisodicMemory
    errors: ErrorBuffer
    semantic: SemanticMemory
    procedural: ProceduralMemory
    registry: ToolRegistry
    executor: ToolExecutor
    bindings: RoutineBindings
    engine: RoutineEngine
    provenance: Provenance

    @classmethod
    def create(
        cls,
        root: str | Path,
        config: HarnessConfig,
        *,
        name: str,
        working_capacity: int = 48,
        permissions: frozenset[str] = frozenset(
            {"kv.read", "sandbox.write", "test.unreliable"}
        ),
    ) -> "BenchmarkRuntime":
        output_root = Path(root)
        output_root.mkdir(parents=True, exist_ok=True)
        trace_path = output_root / f"{name}.jsonl"
        if trace_path.exists():
            trace_path.unlink()

        recorder = TraceRecorder(
            TraceStore(trace_path),
            TraceId(value=f"trace_benchmark_{name}"),
            TaskId(value=f"task_benchmark_{name}"),
            SystemVersion(value=f"{config.system_version.value}+benchmark8"),
            FixedClock(datetime(2026, 8, 18, 19, 0, tzinfo=timezone.utc)),
        )
        budget = ComputeBudget(
            max_reasoning_steps=1024,
            max_tool_actions=1024,
            max_output_tokens=max(8192, config.budget.max_output_tokens),
        )
        orchestrator = TaskOrchestrator.create(recorder, budget=budget)
        goals = GoalMemory(recorder)
        working = WorkingState(recorder, capacity_units=working_capacity)
        episodic = EpisodicMemory(recorder)
        errors = ErrorBuffer(recorder)
        semantic = SemanticMemory(recorder, episodic)
        procedural = ProceduralMemory(recorder, episodic)

        registry = build_default_registry(
            sandbox_root=output_root / f"sandbox_{name}",
            key_values={"answer": "42", "endpoint": "alpha"},
        )
        executor = ToolExecutor(registry, recorder, orchestrator)
        stub = ToolAwareScriptedReasoningStub()
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
            tool_executor=executor,
            tool_permissions=permissions,
        )
        engine = RoutineEngine(bindings)
        engine.register(BenchmarkStepRoutine())
        engine.register(BenchmarkMaintenanceRoutine())
        engine.register(VerificationRoutine())
        engine.register(RecoveryRoutine())

        provenance = Provenance(
            source_kind=SourceKind.TEST,
            source_ref=f"benchmark:{name}",
            created_at=recorder.clock.now(),
            system_version=recorder.system_version,
            trace_id=recorder.trace_id,
            verification=VerificationState.VERIFIED,
        )
        return cls(
            name=name,
            root=output_root,
            config=config,
            recorder=recorder,
            orchestrator=orchestrator,
            goals=goals,
            working=working,
            episodic=episodic,
            errors=errors,
            semantic=semantic,
            procedural=procedural,
            registry=registry,
            executor=executor,
            bindings=bindings,
            engine=engine,
            provenance=provenance,
        )

    def create_root_goal(self, title: str) -> GoalId:
        goal = self.goals.create_goal(
            title,
            self.provenance,
            governing_constraints=(
                "Preserve authoritative state and never bypass gates, verification, or tool permissions",
            ),
            completion_criteria=("All scripted scenario checks pass",),
            goal_id=GoalId(value=f"goal_benchmark_{self.name}"),
        )
        self.orchestrator.start(f"benchmark_start:{self.name}")
        return goal.goal_id

    def replace_orchestrator(self, restored: TaskOrchestrator) -> None:
        """Reconnect owner references after checkpoint restoration."""
        executor = ToolExecutor(self.registry, self.recorder, restored)
        self.orchestrator = restored
        self.executor = executor
        self.bindings.orchestrator = restored
        self.bindings.tool_executor = executor

    def finish(self, goal_id: GoalId) -> None:
        if self.orchestrator.session.mode != OperatingMode.TASK_ACTIVE:
            raise RuntimeError(
                f"benchmark {self.name} cannot finish from {self.orchestrator.session.mode.value}"
            )
        self.goals.update_status(
            goal_id,
            GoalStatus.COMPLETE,
            reason=f"benchmark_complete:{self.name}",
        )
        self.orchestrator.complete(f"benchmark_complete:{self.name}")


def derive_metrics(
    runtime: BenchmarkRuntime,
    goal_id: GoalId,
    *,
    state_correct: bool,
    checks: dict[str, bool],
    notes: tuple[str, ...] = (),
) -> ScenarioMetrics:
    """Compute benchmark metrics from the authoritative trace wherever possible."""

    events = runtime.recorder.store.events(trace_id=runtime.recorder.trace_id)
    trace_complete = bool(events) and [event.step for event in events] == list(
        range(1, len(events) + 1)
    )

    replay_valid = False
    try:
        replayed = TraceReplayer().replay(events)
        replay_valid = (
            replayed.last_step == len(events)
            and replayed.modes.get(str(runtime.recorder.task_id))
            == OperatingMode.COMPLETE.value
        )
    except Exception:
        replay_valid = False

    illegal_transitions = 0
    for event in events:
        if event.event_type != EventType.MODE_CHANGED:
            continue
        try:
            source = OperatingMode(str(event.metadata["from"]))
            target = OperatingMode(str(event.metadata["to"]))
        except (KeyError, ValueError):
            illegal_transitions += 1
            continue
        if not can_transition(source, target):
            illegal_transitions += 1

    goal_statuses: list[str] = []
    for event in events:
        refs = {*event.input_refs, *event.output_refs}
        if str(goal_id) not in refs:
            continue
        if event.event_type in {EventType.GOAL_CREATED, EventType.GOAL_UPDATED}:
            status = event.metadata.get("status")
            if isinstance(status, str):
                goal_statuses.append(status)
    terminal = {
        GoalStatus.COMPLETE.value,
        GoalStatus.FAILED.value,
        GoalStatus.CANCELLED.value,
    }
    goal_retained = (
        bool(goal_statuses)
        and goal_statuses[-1] == GoalStatus.COMPLETE.value
        and all(status not in terminal for status in goal_statuses[:-1])
    )

    maintenance_inputs = [
        event.metadata.get("input_state", {})
        for event in events
        if event.event_type == EventType.GATE_DECISION
        and event.component == "gate.maintenance"
    ]
    pressures = [
        float(item.get("working_pressure", 0.0))
        for item in maintenance_inputs
        if isinstance(item, dict)
    ]
    max_pressure = max(pressures, default=0.0)

    episodic_retrievals = [
        event
        for event in events
        if event.event_type == EventType.MEMORY_RETRIEVED
        and event.component == "memory.episodic"
    ]
    useful_retrievals = sum(
        int(event.metadata.get("result_count", 0)) > 0
        for event in episodic_retrievals
    )
    retrieval_attempts = len(episodic_retrievals)
    usefulness = (
        useful_retrievals / retrieval_attempts if retrieval_attempts else 1.0
    )

    recoveries = sum(
        event.event_type == EventType.MODE_CHANGED
        and event.metadata.get("to") == OperatingMode.RECOVERY.value
        for event in events
    )
    maintenance_cycles = sum(
        event.event_type == EventType.MODE_CHANGED
        and event.metadata.get("to") == OperatingMode.MAINTENANCE.value
        for event in events
    )
    suspensions = sum(
        event.event_type == EventType.MODE_CHANGED
        and event.metadata.get("to") == OperatingMode.SUSPENDED.value
        for event in events
    )
    checkpoints = sum(
        event.event_type == EventType.CHECKPOINT_CREATED for event in events
    )
    verification_failures = sum(
        event.event_type == EventType.VERIFICATION_COMPLETED
        and event.metadata.get("accepted") is False
        for event in events
    )
    action_count = sum(
        event.event_type == EventType.ACTION_EXECUTED for event in events
    )
    evictions = sum(
        event.event_type == EventType.MEMORY_EVICTED
        and event.component == "memory.working"
        for event in events
    )
    authoritative_transitions = sum(
        event.event_type in AUTHORITATIVE_EVENT_TYPES for event in events
    )

    passed = (
        state_correct
        and goal_retained
        and illegal_transitions == 0
        and trace_complete
        and replay_valid
        and all(checks.values())
    )
    return ScenarioMetrics(
        scenario=runtime.name,
        passed=passed,
        goal_retained=goal_retained,
        state_correct=state_correct,
        illegal_transitions=illegal_transitions,
        recoveries=recoveries,
        maintenance_cycles=maintenance_cycles,
        suspensions=suspensions,
        checkpoints=checkpoints,
        max_working_pressure=max_pressure,
        retrieval_attempts=retrieval_attempts,
        useful_retrievals=useful_retrievals,
        retrieval_usefulness=usefulness,
        action_count=action_count,
        verification_failures=verification_failures,
        working_evictions=evictions,
        trace_events=len(events),
        authoritative_transitions=authoritative_transitions,
        trace_complete=trace_complete,
        replay_valid=replay_valid,
        checks=checks,
        notes=notes,
    )
