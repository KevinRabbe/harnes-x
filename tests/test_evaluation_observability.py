from __future__ import annotations

import json
from pathlib import Path

from harness_x.benchmarks.runtime import BenchmarkRuntime
from harness_x.config import load_config
from harness_x.telemetry.self_schema import SelfSchemaBuilder
from harness_x.training import (
    AdapterTrainingConfig,
    CurriculumGenerator,
    TrainingBackend,
    build_training_cohort,
    prepare_training_bundle,
)
from harness_x.training.context_compression import (
    ContextProfileEvaluation,
    compare_context_profiles,
)
from harness_x.training.evaluation import (
    FamilyEvaluation,
    SelfModelEvaluationReport,
    SelfModelPrediction,
)
from harness_x.training.evaluation_observability import (
    EvaluationCaseRecord,
    EvaluationObservabilityReport,
    JsonlEvaluationTraceRecorder,
    run_observed_empirical_adapter_experiment,
)
from harness_x.training.formatting import SelfModelContextProfile
from harness_x.training.models import (
    CurriculumFamily,
    DatasetSplit,
    LabelSource,
    ScenarioDefinition,
    build_example,
)


def _example():
    return build_example(
        definition=ScenarioDefinition(
            seed_id="observability_case",
            family=CurriculumFamily.STRUCTURAL,
            split=DatasetSplit.EVAL,
            task="Identify the authoritative owner.",
            architecture_family="architecture_fixture",
        ),
        system_version="fixture-v1",
        source_state_fingerprint="a" * 64,
        input_state={"surface": "task_lifecycle"},
        expected_decision={"owner": "orchestrator"},
        label_source=LabelSource.SYSTEM_RULE,
        generator_version="fixture-generator-v1",
    )


def _prepared(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")
    runtime = BenchmarkRuntime.create(
        tmp_path / "runtime",
        config,
        name="observability-source",
        working_capacity=16,
    )
    runtime.create_root_goal("grounded observability source")
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
        known_limitations=("fixture:observability",),
    ).build()
    dataset = CurriculumGenerator(config).generate(schema)
    cohort = build_training_cohort((dataset,))
    bundle = prepare_training_bundle(
        cohort,
        AdapterTrainingConfig(base_model="example/test-model", max_train_examples=12),
    )
    prepared = tmp_path / "prepared"
    cohort.write(prepared / "cohort")
    bundle.write(prepared)
    return prepared


def test_jsonl_trace_persists_raw_and_parsed_prediction_boundary(tmp_path: Path) -> None:
    example = _example()
    path = tmp_path / "trace.jsonl"
    recorder = JsonlEvaluationTraceRecorder(path, "adapter-standard-primary")
    prediction = SelfModelPrediction(
        decision={},
        raw_text="not-json",
        parse_error="invalid_json: Expecting value",
    )

    record = recorder.append(
        predictor_name="fixture-adapter",
        profile=SelfModelContextProfile.STANDARD,
        example=example,
        prediction=prediction,
    )

    loaded = EvaluationCaseRecord.model_validate_json(path.read_text(encoding="utf-8"))
    assert loaded == record
    assert loaded.scenario_id == example.scenario_id
    assert loaded.expected_decision == {"owner": "orchestrator"}
    assert loaded.raw_text == "not-json"
    assert loaded.parsed_decision == {}
    assert loaded.parse_error is not None
    assert loaded.exact_match is False
    assert loaded.field_matches == 0
    assert loaded.field_total == 1
    assert loaded.authority_violation is False
    assert recorder.record_count == 1
    assert recorder.parse_failure_count == 1


def test_reference_empirical_run_writes_signed_per_case_observability(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    output = tmp_path / "experiment"
    report = run_observed_empirical_adapter_experiment(
        prepared,
        backend=TrainingBackend.UNSLOTH,
        output_directory=output,
        reference=True,
    )

    observability_path = output / "evaluation-observability.json"
    observability = EvaluationObservabilityReport.model_validate_json(
        observability_path.read_text(encoding="utf-8")
    )
    assert observability.experiment_report_fingerprint == report.report_fingerprint
    assert observability.evaluation_fingerprint == report.base_standard.evaluation_fingerprint
    assert observability.trace_record_count == report.base_standard.sample_count * 6
    assert len(observability.trace_files) == 6
    assert (output / "evaluation-traces" / "base-standard-primary.jsonl").exists()
    assert (output / "evaluation-traces" / "adapter-minimal-context.jsonl").exists()

    # Trace digests are stable evidence, not merely file-presence metadata.
    listed = {item.path for item in observability.trace_files}
    assert listed == {
        "adapter-minimal-context.jsonl",
        "adapter-rich-context.jsonl",
        "adapter-standard-context.jsonl",
        "adapter-standard-primary.jsonl",
        "base-rich-context.jsonl",
        "base-standard-primary.jsonl",
    }


def _evaluation(*, diagnostic_component_accuracy: float) -> SelfModelEvaluationReport:
    return SelfModelEvaluationReport(
        predictor_name="fixture",
        evaluation_fingerprint="b" * 64,
        architecture_families=("architecture_fixture",),
        fault_families=("working_pressure",),
        sample_count=10,
        exact_matches=2,
        exact_accuracy=0.2,
        field_accuracy=0.2,
        diagnostic_component_accuracy=diagnostic_component_accuracy,
        safe_experiment_accuracy=0.0,
        uncertainty_label_accuracy=0.0,
        authority_violation_count=0,
        authority_violation_rate=0.0,
        parse_failure_count=0,
        parse_failure_rate=0.0,
        confidence_coverage=0.0,
        brier_score=None,
        per_family=(
            FamilyEvaluation(
                family=CurriculumFamily.DIAGNOSTIC.value,
                sample_count=5,
                exact_accuracy=0.0,
                field_accuracy=0.1,
            ),
            FamilyEvaluation(
                family=CurriculumFamily.STRUCTURAL.value,
                sample_count=5,
                exact_accuracy=0.4,
                field_accuracy=0.3,
            ),
        ),
    )


def _profile(
    profile: SelfModelContextProfile,
    *,
    diagnostic_component_accuracy: float,
    chars: float,
) -> ContextProfileEvaluation:
    evaluation = _evaluation(
        diagnostic_component_accuracy=diagnostic_component_accuracy
    )
    tokens = chars / 4.0
    return ContextProfileEvaluation(
        profile=profile,
        predictor_name="fixture",
        evaluation=evaluation,
        mean_prompt_chars=chars,
        mean_prompt_tokens=tokens,
        token_measurement_kind="fixture",
        exact_accuracy_per_1k_chars=evaluation.exact_accuracy * 1000.0 / chars,
        exact_accuracy_per_1k_tokens=evaluation.exact_accuracy * 1000.0 / tokens,
    )


def test_compression_rejects_diagnostic_component_regression_hidden_by_exact_floor() -> None:
    base_rich = _profile(
        SelfModelContextProfile.RICH,
        diagnostic_component_accuracy=0.0,
        chars=1000.0,
    )
    adapter_rich = _profile(
        SelfModelContextProfile.RICH,
        diagnostic_component_accuracy=0.8,
        chars=1000.0,
    )
    standard = _profile(
        SelfModelContextProfile.STANDARD,
        diagnostic_component_accuracy=0.7,
        chars=700.0,
    )
    minimal = _profile(
        SelfModelContextProfile.MINIMAL,
        diagnostic_component_accuracy=0.1,
        chars=600.0,
    )

    # Full diagnostic exact-match is deliberately identical (0.0) in rich and both
    # compressed profiles. The dedicated component metric must still protect the loss.
    assert adapter_rich.evaluation.per_family[0].exact_accuracy == 0.0
    assert standard.evaluation.per_family[0].exact_accuracy == 0.0
    assert minimal.evaluation.per_family[0].exact_accuracy == 0.0

    report = compare_context_profiles(
        base_rich=base_rich,
        adapter_rich=adapter_rich,
        adapter_standard=standard,
        adapter_minimal=minimal,
    )

    assert report.standard_qualification.diagnostic_accuracy_delta_vs_adapter_rich == -0.1
    assert "diagnostic_accuracy_regression" in report.standard_qualification.reasons
    assert report.minimal_qualification.diagnostic_accuracy_delta_vs_adapter_rich == -0.7
    assert "diagnostic_accuracy_regression" in report.minimal_qualification.reasons
    assert report.compression_qualified is False
