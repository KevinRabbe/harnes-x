from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness_x.benchmarks.runtime import BenchmarkRuntime
from harness_x.config import load_config
from harness_x.telemetry.self_schema import SelfSchemaBuilder
from harness_x.training import (
    AdapterTrainingConfig,
    CurriculumGenerator,
    EmpiricalAdapterExperimentReport,
    TrainingBackend,
    build_training_cohort,
    identify_model,
    prepare_training_bundle,
    run_empirical_adapter_experiment,
)
from harness_x.training.empirical_cli import main as empirical_main


def _prepared(tmp_path: Path, *, model: str = "example/test-model") -> Path:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")
    runtime = BenchmarkRuntime.create(
        tmp_path / "runtime",
        config,
        name="empirical-source",
        working_capacity=16,
    )
    runtime.create_root_goal("grounded empirical self-model source")
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
        known_limitations=("fixture:empirical",),
    ).build()
    dataset = CurriculumGenerator(config).generate(schema)
    cohort = build_training_cohort((dataset,))
    bundle = prepare_training_bundle(
        cohort,
        AdapterTrainingConfig(base_model=model, max_train_examples=12),
    )
    prepared = tmp_path / "prepared"
    cohort.write(prepared / "cohort")
    bundle.write(prepared)
    return prepared


def test_reference_empirical_run_produces_signed_complete_evidence(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    output = tmp_path / "experiment"
    report = run_empirical_adapter_experiment(
        prepared,
        backend=TrainingBackend.UNSLOTH,
        output_directory=output,
        reference=True,
    )

    assert report.evidence_kind == "reference_simulator"
    assert report.experiment_valid is True
    assert report.self_model_qualified is True
    assert report.context_compression_qualified is True
    assert report.promotion_ready is False
    assert "reference_simulator_not_empirical_evidence" in report.promotion_blockers
    assert "general_regression_not_evaluated" in report.promotion_blockers
    assert report.model_identity.exact is False
    assert report.base_standard.evaluation_fingerprint == report.adapter_standard.evaluation_fingerprint
    assert report.context_compression.evaluation_fingerprint == report.base_standard.evaluation_fingerprint
    assert any(item.path == "training-plan.json" for item in report.input_files)
    assert any(item.path == "REFERENCE_ONLY.txt" for item in report.adapter_files)

    manifest = output / "experiment-manifest.json"
    loaded = EmpiricalAdapterExperimentReport.model_validate_json(
        manifest.read_text(encoding="utf-8")
    )
    assert loaded == report
    assert (output / "environment.json").exists()
    assert (output / "adapter-comparison.json").exists()
    assert (output / "context-compression-report.json").exists()


def test_empirical_remote_model_requires_exact_commit_revision(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    from harness_x.training import load_prepared_training_bundle

    bundle = load_prepared_training_bundle(prepared)
    with pytest.raises(ValueError, match="40-character commit SHA"):
        identify_model(bundle)

    pinned = bundle.model_copy(
        update={
            "config": bundle.config.model_copy(
                update={"base_model_revision": "a" * 40, "tokenizer_revision": "b" * 40}
            )
        }
    )
    identity = identify_model(pinned)
    assert identity.exact is True
    assert identity.source_kind == "remote_revision"
    assert identity.base_model_revision == "a" * 40
    assert identity.tokenizer_revision == "b" * 40


def test_empirical_local_model_identity_hashes_model_tree(tmp_path: Path) -> None:
    model_dir = tmp_path / "local-model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text('{"model_type":"fixture"}\n', encoding="utf-8")
    (model_dir / "weights.bin").write_bytes(b"abc")
    prepared = _prepared(tmp_path / "case", model=str(model_dir))
    from harness_x.training import load_prepared_training_bundle

    first = identify_model(load_prepared_training_bundle(prepared))
    assert first.source_kind == "local_path"
    assert first.exact is True
    assert first.local_tree_fingerprint is not None

    (model_dir / "weights.bin").write_bytes(b"changed")
    second = identify_model(load_prepared_training_bundle(prepared))
    assert second.local_tree_fingerprint != first.local_tree_fingerprint
    assert second.identity_fingerprint != first.identity_fingerprint


def test_empirical_report_detects_tampering(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    output = tmp_path / "experiment"
    run_empirical_adapter_experiment(
        prepared,
        backend=TrainingBackend.HUGGINGFACE_PEFT,
        output_directory=output,
        reference=True,
    )
    path = output / "experiment-manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["experiment_valid"] = False
    with pytest.raises(ValueError, match="fingerprint"):
        EmpiricalAdapterExperimentReport.model_validate(payload)


def test_empirical_run_refuses_nonempty_output(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    output = tmp_path / "experiment"
    output.mkdir()
    (output / "stale.txt").write_text("old", encoding="utf-8")
    with pytest.raises(ValueError, match="must be empty"):
        run_empirical_adapter_experiment(
            prepared,
            backend=TrainingBackend.UNSLOTH,
            output_directory=output,
            reference=True,
        )


def test_empirical_cli_reference_mode_is_end_to_end(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    output = tmp_path / "cli-experiment"
    code = empirical_main(
        [
            str(prepared),
            "--backend",
            "unsloth",
            "--reference",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    payload = json.loads((output / "experiment-manifest.json").read_text(encoding="utf-8"))
    assert payload["experiment_valid"] is True
    assert payload["promotion_ready"] is False


def test_installed_empirical_cli_help() -> None:
    completed = subprocess.run(
        ["harness-x-empirical-adapter", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    normalized = " ".join(completed.stdout.split())
    assert "signed held-out/context compression evidence bundle" in normalized
