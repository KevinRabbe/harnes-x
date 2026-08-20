from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from harness_x.config import (
    ComputeGateConfig,
    FocusGateConfig,
    MaintenanceGateConfig,
    RetrievalGateConfig,
    WriteGateConfig,
)
from harness_x.controllers import (
    GateModelRecommendation,
    GateTrainingDataError,
    UsefulnessState,
    collect_gate_training_dataset,
    load_gate_training_dataset,
)
from harness_x.core.clock import FixedClock
from harness_x.core.contracts import ComputeBudget
from harness_x.core.events import EventType
from harness_x.core.ids import EventId, MemoryId, SystemVersion, TaskId, TraceId
from harness_x.gates import (
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
from harness_x.orchestrator import BudgetDelta, BudgetUsage
from harness_x.telemetry import TraceRecorder, TraceStore


def _recorder(tmp_path, name: str = "gate-trace") -> TraceRecorder:
    return TraceRecorder(
        TraceStore(tmp_path / f"{name}.jsonl"),
        TraceId.new(),
        TaskId.new(),
        SystemVersion(value="m17-test-v1"),
        FixedClock(datetime(2026, 8, 20, tzinfo=timezone.utc)),
    )


def _last_gate_event(recorder: TraceRecorder):
    event = recorder.store.events(trace_id=recorder.trace_id)[-1]
    assert event.event_type == EventType.GATE_DECISION
    return event


def _build_rich_gate_trace(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.emit(EventType.TASK_CREATED, "test")

    retrieval = RetrievalGate(recorder, RetrievalGateConfig())
    retrieval.evaluate(
        RetrievalRequest(
            current_routine="research",
            unresolved_entities=("alpha",),
            uncertainty=False,
            working_pressure=0.2,
        )
    )
    retrieval_event = _last_gate_event(recorder)
    retrieved_id = MemoryId.new()
    recorder.emit(
        EventType.MEMORY_RETRIEVED,
        "memory.episodic",
        input_refs=(str(retrieved_id),),
        metadata={"result_count": 2, "memory_class": "episodic"},
    )
    # The next same-gate event closes the first decision interval.
    retrieval.evaluate(
        RetrievalRequest(
            current_routine="task",
            working_pressure=0.2,
        )
    )
    recorder.emit(EventType.OBSERVATION_RECEIVED, "test", metadata={"after": "retrieval"})

    write = WriteGate(recorder, WriteGateConfig())
    source_event = recorder.emit(EventType.OBSERVATION_RECEIVED, "test")
    write.evaluate(
        WriteRequest(
            accepted=True,
            kind="observation",
            source_ref=str(source_event.event_id),
        )
    )
    written_id = MemoryId.new()
    recorder.emit(
        EventType.MEMORY_WRITTEN,
        "memory.working",
        output_refs=(str(written_id),),
        metadata={"memory_class": "working"},
    )
    recorder.emit(
        EventType.MEMORY_RETRIEVED,
        "memory.working",
        input_refs=(str(written_id),),
        metadata={"result_count": 1, "memory_class": "working"},
    )

    focus = FocusGate(recorder, FocusGateConfig(max_focus_items=2))
    focus.evaluate(
        FocusRequest(
            candidates=(
                FocusCandidate(
                    memory_id=written_id,
                    priority=0.95,
                    created_step=1,
                    last_used_step=recorder.store.next_step(recorder.trace_id) - 1,
                ),
                FocusCandidate(
                    memory_id=MemoryId.new(),
                    priority=0.4,
                    created_step=1,
                    last_used_step=1,
                ),
            )
        )
    )
    recorder.emit(
        EventType.OBSERVATION_RECEIVED,
        "test.focus-consumer",
        input_refs=(str(written_id),),
    )

    compute = ComputeGate(recorder, ComputeGateConfig())
    compute.evaluate(
        ComputeRequest(
            budget=ComputeBudget(
                max_reasoning_steps=10,
                max_tool_actions=10,
                max_output_tokens=1000,
            ),
            usage=BudgetUsage(),
            requested=BudgetDelta(reasoning_steps=1),
        )
    )
    recorder.emit(EventType.REASONING_COMPLETED, "reasoning.test")

    maintenance = MaintenanceGate(
        recorder,
        MaintenanceGateConfig(working_pressure_trigger=0.85),
    )
    maintenance.evaluate(MaintenanceRequest(working_pressure=0.92))
    maintenance_event = _last_gate_event(recorder)
    recorder.emit(
        EventType.MODE_CHANGED,
        "orchestrator",
        metadata={"from": "task_active", "to": "maintenance"},
    )
    recorder.emit(
        EventType.MEMORY_EVICTED,
        "memory.working",
        input_refs=(str(written_id),),
    )
    maintenance.evaluate(MaintenanceRequest(working_pressure=0.40))
    # Keep the final gate decision's observation interval representable too.
    recorder.emit(EventType.OBSERVATION_RECEIVED, "test", metadata={"after": "maintenance"})

    recommendation = GateModelRecommendation(
        decision_event_id=retrieval_event.event_id,
        gate_id="retrieval",
        source="shadow-model",
        model_version="fixture-v1",
        recommendation={"retrieve": True, "targets": ["episodic"]},
        confidence=0.8,
    )
    return recorder, recommendation, maintenance_event


def test_collects_real_gate_decisions_with_outcomes_and_usefulness(tmp_path) -> None:
    recorder, recommendation, maintenance_event = _build_rich_gate_trace(tmp_path)
    trace_path = recorder.store.path
    before = trace_path.read_bytes()

    dataset = collect_gate_training_dataset(
        (trace_path,),
        recommendations=(recommendation,),
        outcome_horizon_steps=32,
    )

    assert trace_path.read_bytes() == before, "collection must not perturb the source trace"
    assert dataset.manifest.record_count == 7
    assert dataset.manifest.records_by_gate == {
        "retrieval": 2,
        "write": 1,
        "focus": 1,
        "compute": 1,
        "maintenance": 2,
    }
    assert dataset.manifest.model_recommendation_count == 1

    retrieval = next(
        record
        for record in dataset.records
        if record.decision_event_id == recommendation.decision_event_id
    )
    assert retrieval.state_features["unresolved_entities"] == ["alpha"]
    assert retrieval.policy_decision["retrieve"] is True
    assert retrieval.model_recommendation == recommendation
    assert retrieval.later_usefulness.state == UsefulnessState.POSITIVE
    assert retrieval.later_usefulness.rationale == "retrieval_returned_evidence"
    assert retrieval.actual_outcome.useful_retrievals == 1

    write = next(record for record in dataset.records if record.gate_id == "write")
    assert write.later_usefulness.state == UsefulnessState.POSITIVE
    assert write.later_usefulness.rationale == "written_memory_later_retrieved"

    focus = next(record for record in dataset.records if record.gate_id == "focus")
    assert focus.later_usefulness.state == UsefulnessState.POSITIVE
    assert focus.later_usefulness.score == 0.5

    compute = next(record for record in dataset.records if record.gate_id == "compute")
    assert compute.later_usefulness.state == UsefulnessState.POSITIVE
    assert compute.actual_outcome.reasoning_completions == 1

    maintenance = next(
        record for record in dataset.records if record.decision_event_id == maintenance_event.event_id
    )
    assert maintenance.later_usefulness.state == UsefulnessState.POSITIVE
    assert maintenance.actual_outcome.followup_gate_input_state == {
        "working_pressure": 0.4,
        "unresolved_error_count": 0,
        "repeated_failure_count": 0,
    }

    no_retrieval = [
        record
        for record in dataset.records
        if record.gate_id == "retrieval" and record.policy_decision["retrieve"] is False
    ][0]
    assert no_retrieval.later_usefulness.state == UsefulnessState.UNKNOWN
    assert no_retrieval.later_usefulness.score is None


def test_zero_result_retrieval_is_negative_but_no_counterfactual_is_invented(tmp_path) -> None:
    recorder = _recorder(tmp_path, "negative")
    recorder.emit(EventType.TASK_CREATED, "test")
    gate = RetrievalGate(recorder, RetrievalGateConfig(always_retrieve_routines=()))
    gate.evaluate(
        RetrievalRequest(
            current_routine="task",
            unresolved_entities=("missing",),
            working_pressure=0.1,
        )
    )
    recorder.emit(
        EventType.MEMORY_RETRIEVED,
        "memory.episodic",
        metadata={"result_count": 0},
    )
    gate.evaluate(RetrievalRequest(current_routine="task", working_pressure=0.1))
    recorder.emit(EventType.OBSERVATION_RECEIVED, "test")

    dataset = collect_gate_training_dataset((recorder.store.path,))
    first, second = [record for record in dataset.records if record.gate_id == "retrieval"]
    assert first.later_usefulness.state == UsefulnessState.NEGATIVE
    assert first.later_usefulness.score == 0.0
    assert second.later_usefulness.state == UsefulnessState.UNKNOWN


def test_dataset_round_trip_and_tamper_detection(tmp_path) -> None:
    recorder, recommendation, _ = _build_rich_gate_trace(tmp_path)
    dataset = collect_gate_training_dataset(
        (recorder.store.path,), recommendations=(recommendation,)
    )
    destination = tmp_path / "dataset"
    dataset.write(destination)
    loaded = load_gate_training_dataset(destination)
    assert loaded == dataset

    records_path = destination / "records.jsonl"
    rows = records_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(rows[0])
    payload["policy_version"] = "tampered-policy"
    rows[0] = json.dumps(payload)
    records_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(GateTrainingDataError, match="fingerprint"):
        load_gate_training_dataset(destination)


def test_duplicate_trace_and_orphan_recommendation_fail_closed(tmp_path) -> None:
    recorder, _, _ = _build_rich_gate_trace(tmp_path)
    with pytest.raises(GateTrainingDataError, match="duplicate or non-contiguous"):
        collect_gate_training_dataset((recorder.store.path, recorder.store.path))

    orphan = GateModelRecommendation(
        decision_event_id=EventId.new(),
        gate_id="retrieval",
        source="shadow-model",
        recommendation={"retrieve": True},
    )
    with pytest.raises(GateTrainingDataError, match="absent source trace"):
        collect_gate_training_dataset((recorder.store.path,), recommendations=(orphan,))
