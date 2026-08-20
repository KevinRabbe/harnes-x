from datetime import datetime, timezone

import pytest

from harness_x.config import load_config
from harness_x.controllers import (
    ComputeAuthorityAdjudicator,
    DeterministicDynamicComputeController,
    DynamicComputeAction,
    DynamicComputeRecommendation,
    DynamicComputeState,
    FrontierPolicy,
    LearnedComputeControllerArtifact,
    LearnedDynamicComputeController,
    build_reference_dynamic_compute_eval_cases,
    build_reference_dynamic_compute_training_examples,
    collect_gate_training_dataset,
    compare_dynamic_compute_controllers,
    load_learned_compute_controller,
    prepare_dynamic_compute_examples,
    run_reference_dynamic_compute_benchmark,
)
from harness_x.core import ComputeBudget, FixedClock, SystemVersion, TaskId, TraceId
from harness_x.core.events import EventType
from harness_x.gates import ComputeAction, ComputeGate, ComputeRequest
from harness_x.orchestrator import BudgetDelta, BudgetUsage
from harness_x.telemetry import TraceRecorder, TraceStore


def _recorder(tmp_path, name: str = "dynamic") -> TraceRecorder:
    return TraceRecorder(
        TraceStore(tmp_path / f"{name}.jsonl"),
        TraceId(value=f"trace_{name}"),
        TaskId(value=f"task_{name}"),
        SystemVersion(value="dynamic-v1"),
        FixedClock(datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)),
    )


def test_reference_learned_controller_improves_capability_cost_frontier() -> None:
    learned, report = run_reference_dynamic_compute_benchmark()

    assert learned.artifact.training_example_count == 21
    assert len(learned.artifact.profiles) == len(DynamicComputeAction)
    assert report.learned_frontier_improved is True
    assert report.rejection_reasons == ()
    assert report.utility_delta > 0.0
    assert report.net_value_delta >= report.policy.min_net_value_gain
    assert report.learned.exact_action_accuracy == pytest.approx(1.0)
    assert report.learned.mean_utility == pytest.approx(1.0)
    assert report.learned.calibration_brier == pytest.approx(0.0)
    assert report.baseline.exact_action_accuracy < report.learned.exact_action_accuracy


def test_learned_artifact_round_trips_and_detects_tampering(tmp_path) -> None:
    controller = LearnedDynamicComputeController.train(
        build_reference_dynamic_compute_training_examples()
    )
    path = tmp_path / "controller.json"
    controller.artifact.write(path)

    loaded = load_learned_compute_controller(path)
    state = DynamicComputeState(
        task_difficulty=0.94,
        uncertainty=0.70,
        progress=0.30,
        retrieval_usefulness=0.18,
        remaining_reasoning_ratio=0.80,
    )
    assert loaded.recommend(state).action == DynamicComputeAction.STRONGER_MODEL

    payload = controller.artifact.model_dump(mode="json")
    payload["training_example_count"] += 1
    with pytest.raises(ValueError, match="artifact fingerprint mismatch"):
        LearnedComputeControllerArtifact.model_validate(payload)


def test_trace_dataset_preparation_uses_only_grounded_observed_actions(tmp_path) -> None:
    recorder = _recorder(tmp_path, "trace_training")
    config = load_config("configs/default.yaml")
    gate = ComputeGate(recorder, config.gates.compute)
    gate.evaluate(
        ComputeRequest(
            budget=ComputeBudget(
                max_reasoning_steps=5,
                max_tool_actions=2,
                max_output_tokens=100,
            ),
            usage=BudgetUsage(reasoning_steps=1),
            requested=BudgetDelta(reasoning_steps=1),
        )
    )
    recorder.emit(
        EventType.REASONING_COMPLETED,
        "reasoning.test",
        metadata={"status": "succeeded"},
    )

    dataset = collect_gate_training_dataset([recorder.store.path])
    examples = prepare_dynamic_compute_examples(dataset)

    assert len(examples) == 1
    assert examples[0].target_action == DynamicComputeAction.REASON_AGAIN
    assert examples[0].label_source == "observed_gate_trajectory"
    assert examples[0].evidence_refs
    assert examples[0].state.recent_reasoning_calls == 1
    assert all(
        example.target_action
        in {
            DynamicComputeAction.STOP,
            DynamicComputeAction.REASON_AGAIN,
            DynamicComputeAction.EXTRA_RETRIEVAL,
            DynamicComputeAction.EXTRA_VERIFICATION,
        }
        for example in examples
    )


def test_learned_recommendation_cannot_override_hard_compute_budget(tmp_path) -> None:
    recorder = _recorder(tmp_path, "authority")
    config = load_config("configs/default.yaml")
    gate = ComputeGate(recorder, config.gates.compute)
    recommendation = DynamicComputeRecommendation(
        action=DynamicComputeAction.STRONGER_MODEL,
        predicted_value=0.95,
        predicted_incremental_cost=0.8,
        confidence=0.9,
        controller_id="test-learned",
        controller_version="v1",
        evidence_basis="test",
    )

    decision = ComputeAuthorityAdjudicator().adjudicate(
        recommendation,
        compute_gate=gate,
        budget=ComputeBudget(
            max_reasoning_steps=1,
            max_tool_actions=1,
            max_output_tokens=100,
        ),
        usage=BudgetUsage(reasoning_steps=1),
    )

    assert decision.permitted is False
    assert decision.compute_gate_action == ComputeAction.SUSPEND
    assert decision.effective_action is None
    assert decision.requested_budget.reasoning_steps == 1
    event = recorder.store.events()[-1]
    assert event.event_type == EventType.GATE_DECISION
    assert event.component == "gate.compute"
    assert event.metadata["decision"]["action"] == "suspend"


def test_stop_recommendation_remains_subject_to_deterministic_compute_gate(tmp_path) -> None:
    recorder = _recorder(tmp_path, "stop_authority")
    gate = ComputeGate(recorder, load_config("configs/default.yaml").gates.compute)
    recommendation = DynamicComputeRecommendation(
        action=DynamicComputeAction.STOP,
        predicted_value=1.0,
        predicted_incremental_cost=0.0,
        confidence=1.0,
        controller_id="test-learned",
        controller_version="v1",
        evidence_basis="test",
    )
    decision = ComputeAuthorityAdjudicator().adjudicate(
        recommendation,
        compute_gate=gate,
        budget=ComputeBudget(),
        usage=BudgetUsage(),
    )

    assert decision.compute_gate_action == ComputeAction.STOP
    assert decision.effective_action == DynamicComputeAction.STOP
    assert decision.permitted is True


class _ConstantController:
    controller_version = "pathological-v1"

    def __init__(self, controller_id: str, action: DynamicComputeAction, predicted: float = 1.0):
        self.controller_id = controller_id
        self.action = action
        self.predicted = predicted

    def recommend(self, state: DynamicComputeState) -> DynamicComputeRecommendation:
        return DynamicComputeRecommendation(
            action=self.action,
            predicted_value=self.predicted,
            predicted_incremental_cost={
                DynamicComputeAction.STOP: 0.0,
                DynamicComputeAction.EXTRA_RETRIEVAL: 0.2,
                DynamicComputeAction.STRONGER_MODEL: 0.85,
                DynamicComputeAction.PARALLEL_CANDIDATES: 0.9,
            }.get(self.action, 0.45),
            confidence=1.0,
            controller_id=self.controller_id,
            controller_version=self.controller_version,
            evidence_basis="pathological_test_policy",
        )


@pytest.mark.parametrize(
    ("name", "action"),
    (
        ("always_stop", DynamicComputeAction.STOP),
        ("always_max_compute", DynamicComputeAction.PARALLEL_CANDIDATES),
        ("always_strongest_model", DynamicComputeAction.STRONGER_MODEL),
        ("retrieval_explosion", DynamicComputeAction.EXTRA_RETRIEVAL),
    ),
)
def test_pathological_compute_policies_fail_frontier_qualification(name, action) -> None:
    report = compare_dynamic_compute_controllers(
        DeterministicDynamicComputeController(),
        _ConstantController(name, action),
        build_reference_dynamic_compute_eval_cases(),
    )

    assert report.learned_frontier_improved is False
    assert report.rejection_reasons
    if action == DynamicComputeAction.STOP:
        assert report.learned.premature_stops > report.baseline.premature_stops
    if action == DynamicComputeAction.STRONGER_MODEL:
        assert report.learned.stronger_model_calls == report.learned.case_count
    if action == DynamicComputeAction.EXTRA_RETRIEVAL:
        assert report.learned.retrieval_calls == report.learned.case_count


def test_realized_trajectory_utility_controls_calibration_not_self_prediction() -> None:
    # The controller confidently predicts value=1 for another reasoning call. On the
    # held-out context-pressure case that action realizes only 0.58 utility, so the
    # evaluator scores calibration against the changed trajectory rather than trusting
    # the controller's own prediction.
    cases = tuple(
        case for case in build_reference_dynamic_compute_eval_cases()
        if case.scenario_family == "context"
    )
    controller = _ConstantController(
        "overconfident_reason",
        DynamicComputeAction.REASON_AGAIN,
        predicted=1.0,
    )
    report = compare_dynamic_compute_controllers(
        DeterministicDynamicComputeController(),
        controller,
        cases,
        policy=FrontierPolicy(min_net_value_gain=0.0, max_calibration_regression=0.0),
    )

    assert report.learned.calibration_brier == pytest.approx((1.0 - 0.58) ** 2)
    assert report.learned_frontier_improved is False
    assert "value_calibration_regressed" in report.rejection_reasons or "insufficient_capability_cost_frontier_gain" in report.rejection_reasons
