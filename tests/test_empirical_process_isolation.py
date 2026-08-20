from __future__ import annotations

from pathlib import Path

import pytest

from harness_x.benchmarks.runtime import BenchmarkRuntime
from harness_x.config import load_config
from harness_x.telemetry.self_schema import SelfSchemaBuilder
from harness_x.training import (
    AdapterTrainingArtifact,
    AdapterTrainingConfig,
    CurriculumGenerator,
    TrainingBackend,
    build_training_cohort,
    load_prepared_training_bundle,
    prepare_training_bundle,
)
from harness_x.training import empirical_safe


def _prepared(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")
    runtime = BenchmarkRuntime.create(
        tmp_path / "runtime",
        config,
        name="isolated-empirical-source",
        working_capacity=16,
    )
    runtime.create_root_goal("grounded isolated empirical source")
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
        known_limitations=("fixture:isolated-empirical",),
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


def _write_training_source(
    prepared: Path,
    training_root: Path,
    *,
    backend: TrainingBackend = TrainingBackend.UNSLOTH,
    cohort_fingerprint: str | None = None,
    base_model_revision: str | None = None,
) -> AdapterTrainingArtifact:
    bundle = load_prepared_training_bundle(prepared)
    adapter = training_root / "adapter"
    adapter.mkdir(parents=True)
    (adapter / "adapter_config.json").write_text('{"fixture":true}\n', encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_bytes(b"fixture-adapter")
    artifact = AdapterTrainingArtifact(
        base_model=bundle.config.base_model,
        base_model_revision=(
            bundle.config.base_model_revision
            if base_model_revision is None
            else base_model_revision
        ),
        tokenizer_revision=bundle.config.tokenizer_revision,
        method=bundle.config.method,
        backend=backend,
        adapter_path="stale/original/location/adapter",
        training_examples=len(bundle.train_records),
        cohort_fingerprint=(
            cohort_fingerprint
            if cohort_fingerprint is not None
            else bundle.cohort_manifest.cohort_fingerprint
        ),
        wall_seconds=201.4,
        peak_gpu_memory_bytes=123456,
        train_result={"global_step": 18, "training_loss": 1.128},
    )
    training_root.mkdir(parents=True, exist_ok=True)
    (training_root / "adapter-artifact.json").write_text(
        artifact.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return artifact


def test_training_worker_uses_current_python_in_child_process(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], bool]] = []

    def fake_run(command, *, check):
        calls.append((list(command), check))

    monkeypatch.setattr(empirical_safe.subprocess, "run", fake_run)
    prepared = tmp_path / "prepared"
    staging = tmp_path / "staging"
    empirical_safe._run_training_worker(prepared, TrainingBackend.UNSLOTH, staging)

    assert len(calls) == 1
    command, check = calls[0]
    assert check is True
    assert command[:3] == [
        empirical_safe.sys.executable,
        "-m",
        "harness_x.training.empirical_worker",
    ]
    assert command[3:] == [
        str(prepared),
        "--backend",
        "unsloth",
        "--output",
        str(staging),
    ]


def test_resume_accepts_relocated_adapter_but_rebinds_path(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path / "case")
    source = tmp_path / "moved-attempt" / "training"
    _write_training_source(prepared, source)
    bundle = load_prepared_training_bundle(prepared)

    validated = empirical_safe.validate_training_source(
        bundle, TrainingBackend.UNSLOTH, source.parent
    )

    assert validated.training_root == source
    assert validated.adapter_directory == source / "adapter"
    assert validated.artifact.adapter_path == "stale/original/location/adapter"
    assert len(validated.artifact_sha256) == 64
    assert len(validated.adapter_tree_fingerprint) == 64


def test_resume_rejects_cohort_or_revision_mismatch(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path / "case")
    bundle = load_prepared_training_bundle(prepared)

    wrong_cohort = tmp_path / "wrong-cohort"
    _write_training_source(prepared, wrong_cohort, cohort_fingerprint="0" * 64)
    with pytest.raises(ValueError, match="cohort_fingerprint"):
        empirical_safe.validate_training_source(
            bundle, TrainingBackend.UNSLOTH, wrong_cohort
        )

    wrong_revision = tmp_path / "wrong-revision"
    _write_training_source(prepared, wrong_revision, base_model_revision="a" * 40)
    with pytest.raises(ValueError, match="base_model_revision"):
        empirical_safe.validate_training_source(
            bundle, TrainingBackend.UNSLOTH, wrong_revision
        )


def test_resume_handoff_copies_adapter_before_evaluation(monkeypatch, tmp_path: Path) -> None:
    prepared = _prepared(tmp_path / "case")
    source = tmp_path / "attempt-1" / "training"
    _write_training_source(prepared, source)
    output = tmp_path / "attempt-2"
    sentinel = object()

    def fake_original(
        prepared_directory,
        *,
        backend,
        output_directory,
        load_in_4bit,
        max_new_tokens,
        general_regression,
        reference,
    ):
        assert reference is False
        bundle = load_prepared_training_bundle(prepared_directory)
        trainer = empirical_safe._empirical.trainer_for_backend(backend)
        artifact = trainer.train(bundle, Path(output_directory) / "training")
        assert Path(artifact.adapter_path) == Path(output_directory) / "training" / "adapter"
        assert artifact.train_result["empirical_training_boundary"]["source_kind"] == (
            "resumed_existing_training"
        )
        return sentinel

    monkeypatch.setattr(
        empirical_safe._empirical,
        "run_empirical_adapter_experiment",
        fake_original,
    )

    result = empirical_safe.run_isolated_empirical_adapter_experiment(
        prepared,
        backend=TrainingBackend.UNSLOTH,
        output_directory=output,
        resume_training_directory=source.parent,
    )

    assert result is sentinel
    assert (output / "training" / "adapter" / "adapter_model.safetensors").exists()
    assert (source / "adapter" / "adapter_model.safetensors").exists()


def test_fresh_training_is_preserved_if_evaluation_fails(monkeypatch, tmp_path: Path) -> None:
    prepared = _prepared(tmp_path / "case")
    output = tmp_path / "experiment"

    def fake_worker(prepared_directory, backend, staging_directory):
        _write_training_source(Path(prepared_directory), Path(staging_directory))

    def failing_original(
        prepared_directory,
        *,
        backend,
        output_directory,
        load_in_4bit,
        max_new_tokens,
        general_regression,
        reference,
    ):
        bundle = load_prepared_training_bundle(prepared_directory)
        trainer = empirical_safe._empirical.trainer_for_backend(backend)
        trainer.train(bundle, Path(output_directory) / "training")
        raise RuntimeError("evaluation failed after training handoff")

    monkeypatch.setattr(empirical_safe, "_run_training_worker", fake_worker)
    monkeypatch.setattr(
        empirical_safe._empirical,
        "run_empirical_adapter_experiment",
        failing_original,
    )

    with pytest.raises(RuntimeError, match="evaluation failed"):
        empirical_safe.run_isolated_empirical_adapter_experiment(
            prepared,
            backend=TrainingBackend.UNSLOTH,
            output_directory=output,
        )

    # The expensive training is already copied into the final output before
    # evaluation, so the transient staging tree can be removed safely.
    assert (output / "training" / "adapter" / "adapter_model.safetensors").exists()
    assert not empirical_safe._staging_directory_for(output).exists()
