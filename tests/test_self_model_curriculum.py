from __future__ import annotations

import json
from pathlib import Path

from harness_x.benchmarks.runtime import BenchmarkRuntime
from harness_x.config import HarnessConfig, load_config
from harness_x.telemetry.self_schema import SelfSchemaBuilder
from harness_x.training import (
    CurriculumFamily,
    CurriculumGenerator,
    CurriculumManifest,
    DatasetSplit,
    FaultFamily,
    HELD_OUT_FAULT_FAMILIES,
    LabelSource,
    SelfModelExample,
)


def _source(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")
    runtime = BenchmarkRuntime.create(
        tmp_path / "source",
        config,
        name="self_model_curriculum_source",
        working_capacity=16,
    )
    runtime.create_root_goal("Generate grounded self-model curriculum")
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
        known_limitations=("curriculum source fixture",),
    ).build()
    return config, runtime, schema


def test_curriculum_is_deterministic_grounded_and_covers_all_families(tmp_path) -> None:
    config, runtime, schema = _source(tmp_path)
    before = runtime.recorder.store.events(trace_id=runtime.recorder.trace_id)

    first = CurriculumGenerator(config).generate(schema)
    second = CurriculumGenerator(config).generate(schema)

    after = runtime.recorder.store.events(trace_id=runtime.recorder.trace_id)
    assert first.manifest.dataset_fingerprint == second.manifest.dataset_fingerprint
    assert [item.scenario_fingerprint for item in first.examples] == [
        item.scenario_fingerprint for item in second.examples
    ]
    assert first.manifest.train_count > 0
    assert first.manifest.eval_count > 0
    assert {item.definition.family for item in first.examples} == set(CurriculumFamily)
    assert len({item.scenario_id for item in first.examples}) == len(first.examples)
    assert before == after


def test_structural_labels_come_from_live_rules_and_config(tmp_path) -> None:
    config, _, schema = _source(tmp_path)
    dataset = CurriculumGenerator(config).generate(schema)
    by_seed = {item.definition.seed_id: item for item in dataset.examples}

    assert by_seed["transition_ready_active"].expected_decision["legal"] is True
    assert by_seed["transition_complete_active"].expected_decision["legal"] is False
    assert by_seed["owner_task_lifecycle"].expected_decision == {
        "owner": "orchestrator"
    }
    assert by_seed["route_failure"].expected_decision == {
        "memory_class": config.gates.write.memory_class_by_kind["failure"]
    }
    assert by_seed["authority_suspected_cause"].expected_decision == {
        "classification": "inferred"
    }
    assert all(
        item.label_source == LabelSource.SYSTEM_RULE
        for item in dataset.examples
        if item.definition.family in {
            CurriculumFamily.STRUCTURAL,
            CurriculumFamily.OPERATIONAL,
        }
    )


def test_diagnostic_labels_are_known_fault_ground_truth_without_label_leakage(tmp_path) -> None:
    config, _, schema = _source(tmp_path)
    dataset = CurriculumGenerator(config).generate(schema)
    diagnostics = [
        item
        for item in dataset.examples
        if item.definition.family == CurriculumFamily.DIAGNOSTIC
    ]

    assert {item.definition.fault_family for item in diagnostics} == {
        item.value for item in FaultFamily
    }
    for item in diagnostics:
        assert item.label_source == LabelSource.INJECTED_FAULT
        assert item.expected_decision["observed_symptom"]
        assert item.expected_decision["likely_component"]
        assert item.expected_decision["evidence"]
        assert item.expected_decision["uncertainty"] in {"low", "medium", "high"}
        assert item.expected_decision["safe_next_experiment"]
        assert item.rationale_metadata["teacher_model_used"] is False
        # The visible state contains symptoms, not an explicit answer key.
        visible = json.dumps(item.input_state, sort_keys=True)
        assert '"fault_family"' not in visible
        assert '"likely_component"' not in visible


def test_eval_holds_out_entire_fault_families_and_seed_ids(tmp_path) -> None:
    config, _, schema = _source(tmp_path)
    dataset = CurriculumGenerator(config).generate(schema)

    train_seeds = set(dataset.manifest.train_seed_ids)
    eval_seeds = set(dataset.manifest.eval_seed_ids)
    assert train_seeds.isdisjoint(eval_seeds)

    train_faults = {
        item.definition.fault_family
        for item in dataset.train
        if item.definition.fault_family is not None
    }
    eval_faults = {
        item.definition.fault_family
        for item in dataset.eval
        if item.definition.fault_family is not None
    }
    held_out = {item.value for item in HELD_OUT_FAULT_FAMILIES}
    assert held_out.isdisjoint(train_faults)
    assert held_out <= eval_faults
    assert set(dataset.manifest.held_out_fault_families) == held_out


def test_operational_labels_track_config_thresholds(tmp_path) -> None:
    config, _, schema = _source(tmp_path)
    original = CurriculumGenerator(config).generate(schema)
    original_by_seed = {item.definition.seed_id: item for item in original.examples}

    changed: HarnessConfig = config.model_copy(deep=True)
    changed.gates.maintenance.working_pressure_trigger = 0.95
    regenerated = CurriculumGenerator(changed).generate(schema)
    changed_by_seed = {item.definition.seed_id: item for item in regenerated.examples}

    assert (
        original_by_seed["maintenance_pressure_at_threshold"].input_state[
            "working_pressure"
        ]
        != changed_by_seed["maintenance_pressure_at_threshold"].input_state[
            "working_pressure"
        ]
    )
    assert original.manifest.dataset_fingerprint != regenerated.manifest.dataset_fingerprint


def test_causal_labels_are_known_interventions_not_teacher_explanations(tmp_path) -> None:
    config, _, schema = _source(tmp_path)
    dataset = CurriculumGenerator(config).generate(schema)
    causal = [
        item
        for item in dataset.examples
        if item.definition.family == CurriculumFamily.CAUSAL_COUNTERFACTUAL
    ]

    assert causal
    for item in causal:
        assert item.label_source == LabelSource.KNOWN_INTERVENTION
        assert item.input_state["before"]
        assert item.input_state["intervention"]
        assert item.input_state["after"]
        assert item.expected_decision["likely_cause"]
        assert item.expected_decision["safe_follow_up_test"]
        assert item.rationale_metadata["teacher_model_used"] is False


def test_dataset_writes_separate_train_eval_jsonl_and_manifest(tmp_path) -> None:
    config, _, schema = _source(tmp_path)
    dataset = CurriculumGenerator(config).generate(schema)
    output = tmp_path / "dataset"
    dataset.write(output)

    train_lines = (output / "train.jsonl").read_text(encoding="utf-8").splitlines()
    eval_lines = (output / "eval.jsonl").read_text(encoding="utf-8").splitlines()
    manifest = CurriculumManifest.model_validate_json(
        (output / "manifest.json").read_text(encoding="utf-8")
    )

    assert len(train_lines) == dataset.manifest.train_count
    assert len(eval_lines) == dataset.manifest.eval_count
    assert all(
        SelfModelExample.model_validate_json(line).definition.split == DatasetSplit.TRAIN
        for line in train_lines
    )
    assert all(
        SelfModelExample.model_validate_json(line).definition.split == DatasetSplit.EVAL
        for line in eval_lines
    )
    assert manifest.dataset_fingerprint == dataset.manifest.dataset_fingerprint
