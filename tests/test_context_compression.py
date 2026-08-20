from __future__ import annotations

import json
from pathlib import Path

from harness_x.benchmarks.runtime import BenchmarkRuntime
from harness_x.config import load_config
from harness_x.telemetry.self_schema import SelfSchemaBuilder
from harness_x.training import (
    DEFAULT_LORA_TARGET_MODULES,
    AdapterTrainingConfig,
    CurriculumGenerator,
    HuggingFacePeftTrainer,
    ReferenceContextCompressionPredictor,
    SelfModelContextProfile,
    TrainingBackend,
    UnslothPeftTrainer,
    evaluate_context_compression,
    format_self_model_example,
    trainer_for_backend,
)


def _dataset(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")
    runtime = BenchmarkRuntime.create(
        tmp_path / "compression-source",
        config,
        name="compression-source",
        working_capacity=16,
    )
    runtime.create_root_goal("context compression grounded source")
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
        known_limitations=("fixture:context-compression",),
    ).build()
    return CurriculumGenerator(config).generate(schema)


def _user_payload(record):
    return json.loads(record.prompt_messages[-1].content)


def test_context_profiles_preserve_live_state_and_only_remove_repeatable_explanation(tmp_path) -> None:
    dataset = _dataset(tmp_path)
    example = dataset.eval[0]

    rich = format_self_model_example(example, context_profile=SelfModelContextProfile.RICH)
    standard = format_self_model_example(
        example, context_profile=SelfModelContextProfile.STANDARD
    )
    minimal = format_self_model_example(
        example, context_profile=SelfModelContextProfile.MINIMAL
    )

    rich_payload = _user_payload(rich)
    standard_payload = _user_payload(standard)
    minimal_payload = _user_payload(minimal)

    for payload in (rich_payload, standard_payload, minimal_payload):
        assert payload["task"] == example.definition.task
        assert payload["system_version"] == example.system_version
        assert payload["source_state_fingerprint"] == example.source_state_fingerprint
        assert payload["input_state"] == example.input_state
        assert payload["output_requirement"]["expected_keys"] == sorted(
            example.expected_decision
        )

    assert "static_architecture_reference" in rich_payload
    assert "static_architecture_reference" not in standard_payload
    assert "static_architecture_reference" not in minimal_payload
    assert standard_payload["architecture_family"] == example.definition.architecture_family
    assert "architecture_family" not in minimal_payload
    assert "curriculum_family" not in minimal_payload

    target = rich.target_json
    for record in (rich, standard, minimal):
        prompt = "\n".join(item.content for item in record.prompt_messages)
        assert target not in prompt


def test_reference_compression_qualifies_standard_but_rejects_overcompression(tmp_path) -> None:
    dataset = _dataset(tmp_path)
    report = evaluate_context_compression(
        dataset.eval,
        base_predictor=ReferenceContextCompressionPredictor("base"),
        adapter_predictor=ReferenceContextCompressionPredictor("adapter"),
        evidence_kind="reference_simulator",
    )

    assert report.evidence_kind == "reference_simulator"
    assert report.base_rich.evaluation.exact_accuracy < 1.0
    assert report.adapter_rich.evaluation.exact_accuracy == 1.0
    assert report.adapter_standard.evaluation.exact_accuracy == 1.0
    assert report.standard_qualification.qualified is True
    assert report.standard_qualification.context_reduction_ratio > 0.0
    assert report.standard_qualification.token_reduction_ratio is not None
    assert report.standard_qualification.efficiency_gain_ratio_vs_adapter_rich > 0.0
    assert report.minimal_qualification.qualified is False
    assert "diagnostic_accuracy_regression" in report.minimal_qualification.reasons
    assert report.selected_profile == SelfModelContextProfile.STANDARD
    assert report.compression_qualified is True
    assert report.selected_exact_accuracy_delta_vs_base_rich > 0.0


def test_training_backends_are_interchangeable_without_importing_heavy_runtime() -> None:
    hf = trainer_for_backend(TrainingBackend.HUGGINGFACE_PEFT)
    unsloth = trainer_for_backend(TrainingBackend.UNSLOTH)

    assert isinstance(hf, HuggingFacePeftTrainer)
    assert isinstance(unsloth, UnslothPeftTrainer)
    assert hf.backend == TrainingBackend.HUGGINGFACE_PEFT
    assert unsloth.backend == TrainingBackend.UNSLOTH


def test_default_lora_targets_cover_attention_and_feed_forward_layers() -> None:
    config = AdapterTrainingConfig(base_model="example/model")
    assert config.target_modules == DEFAULT_LORA_TARGET_MODULES
    assert set(config.target_modules) == {
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    }
