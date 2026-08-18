from datetime import datetime, timezone

from harness_x.config import load_config
from harness_x.core import ComputeBudget, FixedClock, MemoryId, SystemVersion, TaskId, TraceId
from harness_x.core.events import EventType
from harness_x.core.provenance import Provenance, SourceKind
from harness_x.gates import (
    ComputeAction,
    ComputeGate,
    ComputeRequest,
    FocusCandidate,
    FocusGate,
    FocusRequest,
    MaintenanceGate,
    MaintenanceRequest,
    RetrievalGate,
    RetrievalRequest,
    WriteGate,
    WriteRequest,
)
from harness_x.memory import WorkingState
from harness_x.orchestrator.budgets import BudgetDelta, BudgetUsage
from harness_x.telemetry import TraceRecorder, TraceStore


def _config():
    return load_config("configs/default.yaml")


def _recorder(tmp_path) -> TraceRecorder:
    return TraceRecorder(
        TraceStore(tmp_path / "trace.jsonl"),
        TraceId(value="trace_gates"),
        TaskId(value="task_gates"),
        SystemVersion(value="test-v1"),
        FixedClock(datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)),
    )


def _provenance(recorder: TraceRecorder) -> Provenance:
    return Provenance(
        source_kind=SourceKind.TEST,
        source_ref="test:gates",
        created_at=recorder.clock.now(),
        system_version=recorder.system_version,
        trace_id=recorder.trace_id,
    )


def test_same_retrieval_state_and_policy_produces_same_decision(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    gate = RetrievalGate(recorder, _config().gates.retrieval)
    request = RetrievalRequest(
        current_routine="debugging",
        unresolved_entities=("entity:db",),
        uncertainty=True,
        working_pressure=0.4,
        recent_retrieval_count=1,
        query="prior database failure",
    )

    first = gate.evaluate(request)
    second = gate.evaluate(request)

    assert first.decision == second.decision
    assert first.input_fingerprint == second.input_fingerprint
    assert first.policy_version == "retrieval-v0"
    assert first.decision["retrieve"] is True
    assert first.decision["targets"] == ["episodic", "error"]


def test_retrieval_gate_can_suppress_unneeded_reads_under_pressure(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    gate = RetrievalGate(recorder, _config().gates.retrieval)

    decision = gate.evaluate(
        RetrievalRequest(
            current_routine="execution",
            working_pressure=0.95,
            recent_retrieval_count=0,
        )
    )

    assert decision.decision["retrieve"] is False
    assert decision.decision["reason"] == "working_pressure"


def test_write_gate_routes_only_accepted_state(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    gate = WriteGate(recorder, _config().gates.write)

    accepted = gate.evaluate(
        WriteRequest(
            accepted=True,
            kind="anomaly",
            source_ref="event_source",
            verification_ref="verification_ok",
        )
    )
    rejected = gate.evaluate(
        WriteRequest(
            accepted=False,
            kind="goal",
            source_ref="candidate_rejected",
        )
    )

    assert accepted.decision == {
        "write": True,
        "memory_class": "error",
        "reason": "accepted",
    }
    assert rejected.decision["write"] is False
    assert rejected.decision["memory_class"] is None


def test_focus_gate_proposes_order_and_pins_without_mutating_memory(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    working = WorkingState(recorder, capacity_units=20)
    provenance = _provenance(recorder)
    low = working.add(
        kind="fact",
        content={"name": "low"},
        priority=0.2,
        size_units=2,
        source="test",
        provenance=provenance,
        memory_id=MemoryId(value="mem_low"),
    )
    high = working.add(
        kind="goal_context",
        content={"name": "high"},
        priority=0.95,
        size_units=2,
        source="test",
        provenance=provenance,
        memory_id=MemoryId(value="mem_high"),
    )

    gate = FocusGate(recorder, _config().gates.focus)
    decision = gate.evaluate(
        FocusRequest(
            candidates=tuple(
                FocusCandidate(
                    memory_id=item.memory_id,
                    priority=item.priority,
                    pinned=item.pinned,
                    created_step=item.created_step,
                    last_used_step=item.last_used_step,
                )
                for item in working.items()
            )
        )
    )

    assert decision.decision["focus_order"] == [str(high.memory_id), str(low.memory_id)]
    assert decision.decision["proposed_pin_ids"] == [str(high.memory_id)]
    assert working.get(high.memory_id).pinned is False
    assert working.get(low.memory_id).pinned is False


def test_compute_gate_respects_stop_conditions_and_hard_budget(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    gate = ComputeGate(recorder, _config().gates.compute)
    budget = ComputeBudget(
        max_reasoning_steps=2,
        max_tool_actions=1,
        max_output_tokens=100,
    )

    allowed = gate.evaluate(
        ComputeRequest(
            budget=budget,
            usage=BudgetUsage(reasoning_steps=1),
            requested=BudgetDelta(reasoning_steps=1),
        )
    )
    exhausted = gate.evaluate(
        ComputeRequest(
            budget=budget,
            usage=BudgetUsage(reasoning_steps=2),
            requested=BudgetDelta(reasoning_steps=1),
        )
    )
    stopped = gate.evaluate(
        ComputeRequest(
            budget=budget,
            usage=BudgetUsage(),
            explicit_stop=True,
        )
    )

    assert allowed.decision["action"] == ComputeAction.ALLOW.value
    assert exhausted.decision["action"] == ComputeAction.SUSPEND.value
    assert exhausted.decision["exceeded_dimensions"] == ["reasoning_steps"]
    assert stopped.decision["action"] == ComputeAction.STOP.value


def test_maintenance_gate_uses_configured_measurable_pressure(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    gate = MaintenanceGate(recorder, _config().gates.maintenance)

    quiet = gate.evaluate(
        MaintenanceRequest(
            working_pressure=0.4,
            unresolved_error_count=1,
            repeated_failure_count=0,
        )
    )
    pressured = gate.evaluate(
        MaintenanceRequest(
            working_pressure=0.9,
            unresolved_error_count=3,
            repeated_failure_count=2,
        )
    )

    assert quiet.decision["trigger"] is False
    assert pressured.decision["trigger"] is True
    assert pressured.decision["reasons"] == [
        "working_pressure",
        "unresolved_errors",
        "repeated_failures",
    ]


def test_every_gate_decision_is_versioned_and_traced(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    config = _config().gates

    RetrievalGate(recorder, config.retrieval).evaluate(
        RetrievalRequest(current_routine="execution", working_pressure=0.0)
    )
    WriteGate(recorder, config.write).evaluate(
        WriteRequest(accepted=True, kind="observation", source_ref="obs:1")
    )
    FocusGate(recorder, config.focus).evaluate(FocusRequest())
    ComputeGate(recorder, config.compute).evaluate(
        ComputeRequest(budget=ComputeBudget(), usage=BudgetUsage())
    )
    MaintenanceGate(recorder, config.maintenance).evaluate(
        MaintenanceRequest(working_pressure=0.0)
    )

    events = recorder.store.events(trace_id=recorder.trace_id)
    gate_events = [event for event in events if event.event_type == EventType.GATE_DECISION]

    assert len(gate_events) == 5
    assert {event.metadata["gate_id"] for event in gate_events} == {
        "retrieval",
        "write",
        "focus",
        "compute",
        "maintenance",
    }
    assert all(event.metadata["policy_version"] for event in gate_events)
    assert all(len(event.metadata["input_fingerprint"]) == 64 for event in gate_events)
