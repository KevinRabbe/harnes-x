from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_x.benchmarks.runtime import BenchmarkRuntime
from harness_x.config import HarnessConfig, load_config
from harness_x.telemetry.self_schema import SelfSchemaBuilder
from harness_x.training import (
    AdapterPromotionPolicy,
    AdapterTrainingConfig,
    CurriculumGenerator,
    GeneralRegressionResult,
    SelfModelPrediction,
    build_training_cohort,
    compare_base_and_adapter,
    evaluate_self_model,
    format_self_model_example,
    load_prepared_training_bundle,
    load_training_cohort,
    parse_structured_prediction,
    prepare_training_bundle,
)


def _dataset(tmp_path: Path, name: str, config: HarnessConfig, *, capacity: int = 16):
    runtime = BenchmarkRuntime.create(
        tmp_path / name,
        config,
        name=name,
        working_capacity=capacity,
    )
    runtime.create_root_goal(f"grounded curriculum source {name}")
    schema = SelfSchemaBuilder(
        config=config,
        recorder=runtime.recorder,
        orchestrator=runtime.orchestrator,
        goals=runtime.goals,
        working=runtime.working,
        episodic=runtime.episodic,
        errors=runtime.errors,
        semantic=runtime.semantic,
        procedural=runtime.procedural,
        engine=runtime.engine,
        registry=runtime.registry,
        granted_permissions=runtime.bindings.tool_permissions,
        known_limitations=(f"fixture:{name}",),
    ).build()
    return CurriculumGenerator(config).generate(schema)


def _two_architectures(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    base = load_config(root / "configs" / "default.yaml")
    changed = base.model_copy(deep=True)
    changed.gates.maintenance.working_pressure_trigger = 0.93
    first = _dataset(tmp_path, "arch_a", base, capacity=16)
    second = _dataset(tmp_path, "arch_b", changed, capacity=24)
    first_arch = first.examples[0].definition.architecture_family
    second_arch = second.examples[0].definition.architecture_family
    assert first_arch != second_arch
    return base, first, second, first_arch, second_arch


class _FixturePredictor:
    def __init__(self, name: str, mode: str) -> None:
        self._name = name
        self.mode = mode

    @property
    def name(self) -> str:
        return self._name

    def predict(self, example):
        if self.mode == "perfect":
            return SelfModelPrediction(
                decision=dict(example.expected_decision), confidence=0.95
            )
        if self.mode == "weak":
            # Keep structural knowledge intact but fail most reasoning-heavy cases.
            if example.definition.family.value == "structural":
                return SelfModelPrediction(
                    decision=dict(example.expected_decision), confidence=0.8
                )
            return SelfModelPrediction(decision={"unknown": True}, confidence=0.4)
        if self.mode == "authority_violation":
            decision = dict(example.expected_decision)
            decision["memory_write"] = {"kind": "semantic", "value": "trust me"}
            return SelfModelPrediction(decision=decision, confidence=0.99)
        raise AssertionError(self.mode)


def test_formatter_is_deterministic_and_does_not_leak_target(tmp_path) -> None:
    _, first, _, _, _ = _two_architectures(tmp_path)
    example = first.train[0]
    one = format_self_model_example(example)
    two = format_self_model_example(example)

    assert one == two
    assert json.loads(one.target_json) == example.expected_decision
    assert one.messages[-1].role == "assistant"
    prompt_text = "\n".join(message.content for message in one.prompt_messages)
    assert example.source_state_fingerprint in prompt_text
    assert one.target_json not in prompt_text
    assert "expected_keys" in prompt_text


def test_cohort_holds_out_entire_architecture_without_rewriting_examples(tmp_path) -> None:
    _, first, second, first_arch, second_arch = _two_architectures(tmp_path)
    cohort = build_training_cohort(
        (first, second), held_out_architecture_families=(second_arch,)
    )

    assert set(cohort.manifest.train_architecture_families) == {first_arch}
    assert second_arch in cohort.manifest.eval_architecture_families
    assert cohort.manifest.held_out_architecture_families == (second_arch,)
    assert all(
        item.definition.architecture_family != second_arch for item in cohort.train
    )
    assert any(item.definition.architecture_family == second_arch for item in cohort.eval)

    # Source curriculum signatures remain valid even when a source TRAIN record is
    # reassigned to evaluation by the cohort.
    held_out_source_train = next(
        item
        for item in cohort.eval
        if item.definition.architecture_family == second_arch
        and item.definition.split.value == "train"
    )
    held_out_source_train.__class__.model_validate_json(
        held_out_source_train.model_dump_json()
    )


def test_cohort_and_prepared_bundle_round_trip_with_balanced_limit(tmp_path) -> None:
    _, first, second, _, second_arch = _two_architectures(tmp_path)
    cohort = build_training_cohort(
        (first, second), held_out_architecture_families=(second_arch,)
    )
    config = AdapterTrainingConfig(
        base_model="example/test-model", max_train_examples=7
    )
    bundle = prepare_training_bundle(cohort, config)

    assert len(bundle.train_records) == 7
    assert len({record.curriculum_family for record in bundle.train_records}) > 1
    assert len(bundle.eval_records) == cohort.manifest.eval_count

    output = tmp_path / "prepared"
    cohort.write(output / "cohort")
    bundle.write(output)
    loaded_cohort = load_training_cohort(output / "cohort")
    loaded_bundle = load_prepared_training_bundle(output)
    assert loaded_cohort.manifest == cohort.manifest
    assert loaded_bundle == bundle


def test_cohort_integrity_detects_tampering(tmp_path) -> None:
    _, first, second, _, second_arch = _two_architectures(tmp_path)
    cohort = build_training_cohort(
        (first, second), held_out_architecture_families=(second_arch,)
    )
    output = tmp_path / "cohort"
    cohort.write(output)

    eval_path = output / "eval-examples.jsonl"
    lines = eval_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["input_state"]["tampered"] = True
    lines[0] = json.dumps(payload)
    eval_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_training_cohort(output)


def test_base_vs_adapter_promotion_uses_same_held_out_cases(tmp_path) -> None:
    _, first, second, _, second_arch = _two_architectures(tmp_path)
    cohort = build_training_cohort(
        (first, second), held_out_architecture_families=(second_arch,)
    )
    baseline = evaluate_self_model(cohort.eval, _FixturePredictor("base", "weak"))
    adapter = evaluate_self_model(cohort.eval, _FixturePredictor("adapter", "perfect"))

    assert baseline.evaluation_fingerprint == adapter.evaluation_fingerprint
    assert second_arch in adapter.architecture_families
    assert adapter.exact_accuracy > baseline.exact_accuracy
    assert adapter.diagnostic_component_accuracy >= baseline.diagnostic_component_accuracy
    assert adapter.authority_violation_rate == 0.0
    assert adapter.parse_failure_rate == 0.0
    assert adapter.brier_score is not None

    comparison = compare_base_and_adapter(
        baseline,
        adapter,
        general_regression=GeneralRegressionResult(
            baseline_score=0.80,
            adapter_score=0.79,
            metric_name="fixture_general_capability",
        ),
    )
    assert comparison.promotion_allowed is True
    assert comparison.reasons == ()


def test_promotion_rejects_authority_violations_and_general_regression(tmp_path) -> None:
    _, first, second, _, second_arch = _two_architectures(tmp_path)
    cohort = build_training_cohort(
        (first, second), held_out_architecture_families=(second_arch,)
    )
    baseline = evaluate_self_model(cohort.eval, _FixturePredictor("base", "weak"))
    violating = evaluate_self_model(
        cohort.eval, _FixturePredictor("adapter", "authority_violation")
    )

    comparison = compare_base_and_adapter(
        baseline,
        violating,
        general_regression=GeneralRegressionResult(
            baseline_score=1.0,
            adapter_score=0.8,
        ),
    )
    assert comparison.promotion_allowed is False
    assert "authority_violation_rate_exceeded" in comparison.reasons
    assert "general_capability_regression" in comparison.reasons


def test_promotion_refuses_reports_from_different_evaluation_sets(tmp_path) -> None:
    _, first, second, _, second_arch = _two_architectures(tmp_path)
    cohort = build_training_cohort(
        (first, second), held_out_architecture_families=(second_arch,)
    )
    baseline = evaluate_self_model(cohort.eval, _FixturePredictor("base", "weak"))
    adapter = evaluate_self_model(
        cohort.eval[:-1], _FixturePredictor("adapter", "perfect")
    )
    with pytest.raises(ValueError, match="same cases"):
        compare_base_and_adapter(baseline, adapter)


def test_structured_prediction_parser_handles_direct_wrapper_and_errors() -> None:
    direct = parse_structured_prediction('{"owner":"orchestrator"}')
    assert direct.decision == {"owner": "orchestrator"}
    assert direct.parse_error is None

    wrapped = parse_structured_prediction(
        '```json\n{"decision":{"legal":true},"confidence":0.75}\n```'
    )
    assert wrapped.decision == {"legal": True}
    assert wrapped.confidence == 0.75

    invalid = parse_structured_prediction("not json")
    assert invalid.decision == {}
    assert invalid.parse_error is not None


def test_policy_can_require_larger_improvement(tmp_path) -> None:
    _, first, second, _, second_arch = _two_architectures(tmp_path)
    cohort = build_training_cohort(
        (first, second), held_out_architecture_families=(second_arch,)
    )
    baseline = evaluate_self_model(cohort.eval, _FixturePredictor("base", "weak"))
    adapter = evaluate_self_model(cohort.eval, _FixturePredictor("adapter", "perfect"))
    comparison = compare_base_and_adapter(
        baseline,
        adapter,
        policy=AdapterPromotionPolicy(min_exact_accuracy_delta=1.0),
    )
    assert comparison.promotion_allowed is False
    assert "insufficient_exact_accuracy_improvement" in comparison.reasons
