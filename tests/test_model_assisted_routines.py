from __future__ import annotations

from pathlib import Path

import pytest

from harness_x.benchmarks import ReferenceAssistedCore, run_model_assisted_benchmark
from harness_x.benchmarks.runtime import BenchmarkRuntime
from harness_x.config import load_config
from harness_x.core.events import EventType
from harness_x.reasoning import (
    RawProposal,
    RawReasoningOutput,
    ReasoningCoreError,
    ReasoningCoreInfo,
    ReasoningService,
)
from harness_x.routines import (
    AssistedDecisionRequest,
    DecisionFamily,
    RecommendationSource,
    RoutineError,
    register_model_assisted_routines,
)


class StaticCore:
    def __init__(self, output: RawReasoningOutput) -> None:
        self.output = output
        self._info = ReasoningCoreInfo(
            name="static_test_core",
            version="static-test-v1",
            model="static-test-model",
            transport="in_process_test",
            model_inference=True,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        del context
        return self.output


class FailingCore:
    def __init__(self) -> None:
        self._info = ReasoningCoreInfo(
            name="failing_test_core",
            version="failing-test-v1",
            model="failing-test-model",
            transport="in_process_test",
            model_inference=True,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        del context
        raise ReasoningCoreError("synthetic model runtime failure")


def _config():
    root = Path(__file__).resolve().parents[1]
    return load_config(root / "configs" / "default.yaml")


def _planning_runtime(tmp_path, core):
    runtime = BenchmarkRuntime.create(
        tmp_path,
        _config(),
        name="assisted_planning_unit",
        working_capacity=24,
    )
    goal_id = runtime.create_root_goal("Compare planning recommendation")
    runtime.bindings.reasoning_service = ReasoningService(runtime.recorder, core)
    register_model_assisted_routines(runtime.engine)
    return runtime, goal_id


def _planning_request(runtime, goal_id, *, reference=True):
    return AssistedDecisionRequest(
        task_id=runtime.recorder.task_id,
        goal_id=goal_id,
        family=DecisionFamily.PLANNING,
        instruction="Order build, test, and deploy safely.",
        problem={"candidate_steps": ["deploy", "test", "build"]},
        evaluation_reference=(
            {"steps": ["build", "test", "deploy"]} if reference else None
        ),
    )


def test_reference_core_improves_all_seven_families_without_gaining_authority(tmp_path) -> None:
    report = run_model_assisted_benchmark(
        tmp_path,
        _config(),
        core=ReferenceAssistedCore(),
    )

    assert report.passed
    assert report.architecture_valid
    assert report.model_qualified
    assert len(report.scenarios) == 7
    assert report.model_improved_count == 7
    assert report.model_selected_count == 7
    assert report.baseline_retained_count == 0
    assert report.model_policy_violation_count == 0
    assert report.average_assisted_score == pytest.approx(1.0)
    assert report.average_assisted_score > report.average_baseline_score
    assert all(item.state_authority_preserved for item in report.scenarios)
    assert all(item.reasoning_budget_consumed for item in report.scenarios)
    assert all(item.replay_valid for item in report.scenarios)
    assert all(item.selected_source == RecommendationSource.MODEL for item in report.scenarios)


def test_tie_keeps_deterministic_baseline(tmp_path) -> None:
    core = StaticCore(
        RawReasoningOutput(
            status="continue",
            proposals=(
                RawProposal(
                    summary="same as baseline",
                    payload={"steps": ["deploy", "test", "build"]},
                ),
            ),
        )
    )
    runtime, goal_id = _planning_runtime(tmp_path, core)
    request = AssistedDecisionRequest(
        task_id=runtime.recorder.task_id,
        goal_id=goal_id,
        family=DecisionFamily.PLANNING,
        instruction="Keep the supplied order.",
        problem={"candidate_steps": ["deploy", "test", "build"]},
        evaluation_reference={"steps": ["deploy", "test", "build"]},
    )

    execution = runtime.engine.execute("planning_proposal", request)

    assert execution.result.data["selected_source"] == RecommendationSource.BASELINE.value
    assert execution.result.data["promotion_reason"] == "assisted_did_not_beat_baseline"
    assert execution.result.data["baseline_evaluation"]["score"] == pytest.approx(1.0)
    assert execution.result.data["assisted_evaluation"]["score"] == pytest.approx(1.0)


def test_authority_shaped_model_payload_is_scored_zero_and_rejected(tmp_path) -> None:
    core = StaticCore(
        RawReasoningOutput(
            status="continue",
            proposals=(
                RawProposal(
                    summary="tries to smuggle a memory mutation",
                    payload={
                        "steps": ["build", "test", "deploy"],
                        "memory_write": {"kind": "semantic", "value": "trust me"},
                    },
                ),
            ),
        )
    )
    runtime, goal_id = _planning_runtime(tmp_path, core)
    before_mode = runtime.orchestrator.session.mode
    before_memories = (
        len(runtime.working.items()),
        len(runtime.episodic.all()),
        len(runtime.errors.all()),
    )

    execution = runtime.engine.execute(
        "planning_proposal",
        _planning_request(runtime, goal_id),
    )

    data = execution.result.data
    assert data["selected_source"] == RecommendationSource.BASELINE.value
    assert data["promotion_reason"] == "assisted_invariant_violation"
    assert data["assisted_evaluation"]["score"] == pytest.approx(0.0)
    assert "$.memory_write" in data["assisted_evaluation"]["invariant_violations"]
    assert runtime.orchestrator.session.mode == before_mode
    assert (
        len(runtime.working.items()),
        len(runtime.episodic.all()),
        len(runtime.errors.all()),
    ) == before_memories
    assert runtime.orchestrator.session.usage.tool_actions == 0


def test_reasoning_failure_falls_back_without_becoming_authority_violation(tmp_path) -> None:
    runtime, goal_id = _planning_runtime(tmp_path, FailingCore())
    before_steps = runtime.orchestrator.session.usage.reasoning_steps

    execution = runtime.engine.execute(
        "planning_proposal",
        _planning_request(runtime, goal_id),
    )

    data = execution.result.data
    assert data["selected_source"] == RecommendationSource.BASELINE.value
    assert data["promotion_reason"] == "reasoning_core_failed_baseline_retained"
    assert "synthetic model runtime failure" in data["model_error"]
    assert data["assisted_evaluation"]["invariant_violations"] == []
    assert runtime.orchestrator.session.usage.reasoning_steps == before_steps + 1


def test_without_external_reference_model_stays_shadow_only(tmp_path) -> None:
    core = StaticCore(
        RawReasoningOutput(
            status="continue",
            proposals=(
                RawProposal(
                    summary="plausible but unevaluated",
                    payload={"steps": ["build", "test", "deploy"]},
                ),
            ),
        )
    )
    runtime, goal_id = _planning_runtime(tmp_path, core)

    execution = runtime.engine.execute(
        "planning_proposal",
        _planning_request(runtime, goal_id, reference=False),
    )

    data = execution.result.data
    assert data["selected_source"] == RecommendationSource.BASELINE.value
    assert data["promotion_reason"] == "shadow_only_without_reference"
    assert data["baseline_evaluation"]["score"] is None
    assert data["assisted_evaluation"]["score"] is None


def test_model_assisted_routine_requires_explicit_reasoning_service(tmp_path) -> None:
    runtime = BenchmarkRuntime.create(
        tmp_path,
        _config(),
        name="assisted_missing_core",
        working_capacity=24,
    )
    goal_id = runtime.create_root_goal("Missing reasoning service must fail closed")
    register_model_assisted_routines(runtime.engine)

    with pytest.raises(RoutineError, match="requires a configured ReasoningService"):
        runtime.engine.execute(
            "planning_proposal",
            _planning_request(runtime, goal_id),
        )


def test_comparison_event_is_traceable_and_records_no_authoritative_mutation(tmp_path) -> None:
    core = StaticCore(
        RawReasoningOutput(
            status="continue",
            proposals=(
                RawProposal(
                    summary="correct planning recommendation",
                    payload={"steps": ["build", "test", "deploy"]},
                ),
            ),
        )
    )
    runtime, goal_id = _planning_runtime(tmp_path, core)
    runtime.engine.execute("planning_proposal", _planning_request(runtime, goal_id))

    events = runtime.recorder.store.events(trace_id=runtime.recorder.trace_id)
    compared = [
        event for event in events if event.event_type == EventType.ASSISTED_DECISION_COMPARED
    ]
    assert len(compared) == 1
    assert compared[0].metadata["selected_source"] == RecommendationSource.MODEL.value
    assert compared[0].metadata["authoritative_mutation"] is False
    assert not any(event.event_type == EventType.ACTION_EXECUTED for event in events)
