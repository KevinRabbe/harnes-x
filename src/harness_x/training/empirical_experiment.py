"""Milestone 20 reproducible local self-model training experiment.

This module closes the gap between having training/evaluation components and producing
one auditable local evidence bundle.  It intentionally separates a *valid experiment*
from a *winning adapter*: negative empirical results are first-class evidence.
"""

from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import json
import platform
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .adapter_training import (
    AdapterTrainingArtifact,
    PreparedTrainingBundle,
    TrainingBackend,
    load_prepared_training_bundle,
    trainer_for_backend,
)
from .cohort import load_training_cohort
from .context_compression import (
    ContextCompressionReport,
    ReferenceContextCompressionPredictor,
    compare_context_profiles,
    evaluate_context_profile,
)
from .evaluation import (
    AdapterComparisonReport,
    GeneralRegressionResult,
    SelfModelEvaluationReport,
    compare_base_and_adapter,
    evaluate_self_model,
)
from .formatting import SelfModelContextProfile
from .models import canonical_json
from .predictors import HuggingFaceSelfModelPredictor


_SHA40 = re.compile(r"^[0-9a-fA-F]{40}$")
_CAPTURED_PACKAGES = (
    "harness-x",
    "torch",
    "transformers",
    "datasets",
    "accelerate",
    "peft",
    "trl",
    "bitsandbytes",
    "unsloth",
    "unsloth-zoo",
)


class FileDigest(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


class ModelIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_kind: str
    base_model: str
    base_model_revision: str | None = None
    tokenizer_revision: str | None = None
    local_tree_fingerprint: str | None = None
    exact: bool
    identity_fingerprint: str = Field(min_length=64, max_length=64)


class EnvironmentSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    python_version: str
    platform: str
    machine: str
    processor: str
    package_versions: dict[str, str]
    torch_available: bool
    cuda_available: bool
    cuda_runtime_version: str | None = None
    gpu_name: str | None = None
    gpu_total_memory_bytes: int | None = Field(default=None, ge=0)
    gpu_compute_capability: str | None = None


class EmpiricalAdapterExperimentReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "self-model-empirical-experiment-v1"
    evidence_kind: str
    backend: TrainingBackend
    cohort_fingerprint: str = Field(min_length=64, max_length=64)
    model_identity: ModelIdentity
    environment: EnvironmentSnapshot
    input_files: tuple[FileDigest, ...]
    adapter_files: tuple[FileDigest, ...]
    training: AdapterTrainingArtifact
    base_standard: SelfModelEvaluationReport
    adapter_standard: SelfModelEvaluationReport
    adapter_comparison: AdapterComparisonReport
    context_compression: ContextCompressionReport
    general_regression_evaluated: bool
    experiment_valid: bool
    self_model_qualified: bool
    context_compression_qualified: bool
    promotion_ready: bool
    promotion_blockers: tuple[str, ...]
    report_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_fingerprint(self) -> "EmpiricalAdapterExperimentReport":
        payload = self.model_dump(mode="json", exclude={"report_fingerprint"})
        expected = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        if self.report_fingerprint != expected:
            raise ValueError("empirical experiment report fingerprint does not match content")
        return self

    def write(self, output_directory: str | Path) -> Path:
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        (output / "environment.json").write_text(
            self.environment.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (output / "base-standard-evaluation.json").write_text(
            self.base_standard.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (output / "adapter-standard-evaluation.json").write_text(
            self.adapter_standard.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (output / "adapter-comparison.json").write_text(
            self.adapter_comparison.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (output / "context-compression-report.json").write_text(
            self.context_compression.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        path = output / "experiment-manifest.json"
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def digest_tree(root: str | Path) -> tuple[FileDigest, ...]:
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(root)
    files: list[FileDigest] = []
    if root.is_file():
        candidates = (root,)
        base = root.parent
    else:
        candidates = tuple(
            path
            for path in sorted(root.rglob("*"), key=lambda item: item.as_posix())
            if path.is_file() and ".git" not in path.parts
        )
        base = root
    for path in candidates:
        files.append(
            FileDigest(
                path=path.relative_to(base).as_posix(),
                size_bytes=path.stat().st_size,
                sha256=_sha256_file(path),
            )
        )
    if not files:
        raise ValueError(f"artifact tree contains no files: {root}")
    return tuple(files)


def _tree_fingerprint(files: tuple[FileDigest, ...]) -> str:
    return hashlib.sha256(
        canonical_json([item.model_dump(mode="json") for item in files]).encode("utf-8")
    ).hexdigest()


def identify_model(bundle: PreparedTrainingBundle) -> ModelIdentity:
    config = bundle.config
    local = Path(config.base_model)
    if local.exists():
        files = digest_tree(local)
        local_fingerprint = _tree_fingerprint(files)
        payload = {
            "source_kind": "local_path",
            "base_model": str(local.resolve()),
            "local_tree_fingerprint": local_fingerprint,
            "tokenizer_revision": config.tokenizer_revision,
        }
        identity = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        return ModelIdentity(
            source_kind="local_path",
            base_model=config.base_model,
            base_model_revision=config.base_model_revision,
            tokenizer_revision=config.tokenizer_revision,
            local_tree_fingerprint=local_fingerprint,
            exact=True,
            identity_fingerprint=identity,
        )

    revision = config.base_model_revision
    tokenizer_revision = config.tokenizer_revision or revision
    if revision is None or not _SHA40.fullmatch(revision):
        raise ValueError(
            "empirical remote-model runs require base_model_revision to be an exact "
            "40-character commit SHA, not an unpinned branch/tag"
        )
    if tokenizer_revision is None or not _SHA40.fullmatch(tokenizer_revision):
        raise ValueError(
            "empirical remote-model runs require tokenizer_revision (or the inherited "
            "base revision) to be an exact 40-character commit SHA"
        )
    payload = {
        "source_kind": "remote_revision",
        "base_model": config.base_model,
        "base_model_revision": revision.lower(),
        "tokenizer_revision": tokenizer_revision.lower(),
    }
    identity = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return ModelIdentity(
        source_kind="remote_revision",
        base_model=config.base_model,
        base_model_revision=revision,
        tokenizer_revision=tokenizer_revision,
        exact=True,
        identity_fingerprint=identity,
    )


def capture_environment() -> EnvironmentSnapshot:
    versions: dict[str, str] = {}
    for package in _CAPTURED_PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue

    torch_available = False
    cuda_available = False
    cuda_runtime = None
    gpu_name = None
    gpu_memory = None
    capability = None
    try:
        import torch

        torch_available = True
        cuda_available = bool(torch.cuda.is_available())
        cuda_runtime = str(torch.version.cuda) if torch.version.cuda is not None else None
        if cuda_available:
            index = torch.cuda.current_device()
            properties = torch.cuda.get_device_properties(index)
            gpu_name = str(properties.name)
            gpu_memory = int(properties.total_memory)
            major, minor = torch.cuda.get_device_capability(index)
            capability = f"{major}.{minor}"
    except ImportError:
        pass

    return EnvironmentSnapshot(
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        machine=platform.machine(),
        processor=platform.processor(),
        package_versions=versions,
        torch_available=torch_available,
        cuda_available=cuda_available,
        cuda_runtime_version=cuda_runtime,
        gpu_name=gpu_name,
        gpu_total_memory_bytes=gpu_memory,
        gpu_compute_capability=capability,
    )


def _release_predictor(predictor: Any) -> None:
    close = getattr(predictor, "close", None)
    if callable(close):
        close()
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


class _StandardProfilePredictor:
    def __init__(self, source: Any) -> None:
        self.source = source

    @property
    def name(self) -> str:
        return f"{self.source.name}:standard"

    def predict(self, example: Any) -> Any:
        return self.source.predict_with_profile(example, SelfModelContextProfile.STANDARD)


def _reference_training_artifact(
    bundle: PreparedTrainingBundle,
    backend: TrainingBackend,
    output: Path,
) -> AdapterTrainingArtifact:
    adapter = output / "training" / "adapter"
    adapter.mkdir(parents=True, exist_ok=True)
    (adapter / "REFERENCE_ONLY.txt").write_text(
        "Reference simulator artifact. No model weights were trained.\n", encoding="utf-8"
    )
    artifact = AdapterTrainingArtifact(
        base_model=bundle.config.base_model,
        base_model_revision=bundle.config.base_model_revision,
        tokenizer_revision=bundle.config.tokenizer_revision,
        method=bundle.config.method,
        backend=backend,
        adapter_path=str(adapter),
        training_examples=len(bundle.train_records),
        cohort_fingerprint=bundle.cohort_manifest.cohort_fingerprint,
        wall_seconds=0.0,
        peak_gpu_memory_bytes=None,
        train_result={"reference_simulator": True},
    )
    (output / "training" / "adapter-artifact.json").write_text(
        artifact.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return artifact


def run_empirical_adapter_experiment(
    prepared_directory: str | Path,
    *,
    backend: TrainingBackend | str,
    output_directory: str | Path,
    load_in_4bit: bool = True,
    max_new_tokens: int = 512,
    general_regression: GeneralRegressionResult | None = None,
    reference: bool = False,
) -> EmpiricalAdapterExperimentReport:
    prepared = Path(prepared_directory)
    output = Path(output_directory)
    if output.exists() and any(output.iterdir()):
        raise ValueError("empirical experiment output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)

    bundle = load_prepared_training_bundle(prepared)
    cohort = load_training_cohort(prepared / "cohort")
    if cohort.manifest.cohort_fingerprint != bundle.cohort_manifest.cohort_fingerprint:
        raise ValueError("prepared bundle and cohort fingerprints do not match")
    backend = TrainingBackend(backend)
    input_files = digest_tree(prepared)
    environment = capture_environment()

    if reference:
        model_identity_payload = {
            "source_kind": "reference_simulator",
            "base_model": bundle.config.base_model,
            "cohort": bundle.cohort_manifest.cohort_fingerprint,
        }
        model_identity = ModelIdentity(
            source_kind="reference_simulator",
            base_model=bundle.config.base_model,
            base_model_revision=bundle.config.base_model_revision,
            tokenizer_revision=bundle.config.tokenizer_revision,
            exact=False,
            identity_fingerprint=hashlib.sha256(
                canonical_json(model_identity_payload).encode("utf-8")
            ).hexdigest(),
        )
        training = _reference_training_artifact(bundle, backend, output)
        base_predictor = ReferenceContextCompressionPredictor("base")
        adapter_predictor = ReferenceContextCompressionPredictor("adapter")
        base_standard = evaluate_self_model(
            cohort.eval, _StandardProfilePredictor(base_predictor)
        )
        adapter_standard = evaluate_self_model(
            cohort.eval, _StandardProfilePredictor(adapter_predictor)
        )
        base_rich = evaluate_context_profile(
            cohort.eval, base_predictor, SelfModelContextProfile.RICH
        )
        adapter_rich = evaluate_context_profile(
            cohort.eval, adapter_predictor, SelfModelContextProfile.RICH
        )
        adapter_standard_profile = evaluate_context_profile(
            cohort.eval, adapter_predictor, SelfModelContextProfile.STANDARD
        )
        adapter_minimal = evaluate_context_profile(
            cohort.eval, adapter_predictor, SelfModelContextProfile.MINIMAL
        )
        evidence_kind = "reference_simulator"
    else:
        model_identity = identify_model(bundle)
        training = trainer_for_backend(backend).train(bundle, output / "training")
        base_predictor = HuggingFaceSelfModelPredictor(
            base_model=bundle.config.base_model,
            base_model_revision=bundle.config.base_model_revision,
            tokenizer_revision=bundle.config.tokenizer_revision,
            load_in_4bit=load_in_4bit,
            max_new_tokens=max_new_tokens,
        )
        base_standard = evaluate_self_model(cohort.eval, base_predictor)
        base_rich = evaluate_context_profile(
            cohort.eval, base_predictor, SelfModelContextProfile.RICH
        )
        _release_predictor(base_predictor)

        adapter_predictor = HuggingFaceSelfModelPredictor(
            base_model=bundle.config.base_model,
            base_model_revision=bundle.config.base_model_revision,
            tokenizer_revision=bundle.config.tokenizer_revision,
            adapter_path=training.adapter_path,
            load_in_4bit=load_in_4bit,
            max_new_tokens=max_new_tokens,
        )
        adapter_standard = evaluate_self_model(cohort.eval, adapter_predictor)
        adapter_rich = evaluate_context_profile(
            cohort.eval, adapter_predictor, SelfModelContextProfile.RICH
        )
        adapter_standard_profile = evaluate_context_profile(
            cohort.eval, adapter_predictor, SelfModelContextProfile.STANDARD
        )
        adapter_minimal = evaluate_context_profile(
            cohort.eval, adapter_predictor, SelfModelContextProfile.MINIMAL
        )
        _release_predictor(adapter_predictor)
        evidence_kind = "empirical_model"

    comparison = compare_base_and_adapter(
        base_standard,
        adapter_standard,
        general_regression=general_regression,
    )
    compression = compare_context_profiles(
        base_rich=base_rich,
        adapter_rich=adapter_rich,
        adapter_standard=adapter_standard_profile,
        adapter_minimal=adapter_minimal,
        evidence_kind=evidence_kind,
    )
    adapter_files = digest_tree(training.adapter_path)

    blockers: list[str] = []
    if reference:
        blockers.append("reference_simulator_not_empirical_evidence")
    if not comparison.promotion_allowed:
        blockers.append("self_model_adapter_not_qualified")
    if not compression.compression_qualified:
        blockers.append("context_compression_not_qualified")
    if general_regression is None:
        blockers.append("general_regression_not_evaluated")

    experiment_valid = (
        training.cohort_fingerprint == bundle.cohort_manifest.cohort_fingerprint
        and base_standard.evaluation_fingerprint == adapter_standard.evaluation_fingerprint
        and compression.evaluation_fingerprint == base_standard.evaluation_fingerprint
        and bool(adapter_files)
    )
    promotion_ready = experiment_valid and not blockers
    payload: dict[str, Any] = {
        "schema_version": "self-model-empirical-experiment-v1",
        "evidence_kind": evidence_kind,
        "backend": backend.value,
        "cohort_fingerprint": bundle.cohort_manifest.cohort_fingerprint,
        "model_identity": model_identity.model_dump(mode="json"),
        "environment": environment.model_dump(mode="json"),
        "input_files": [item.model_dump(mode="json") for item in input_files],
        "adapter_files": [item.model_dump(mode="json") for item in adapter_files],
        "training": training.model_dump(mode="json"),
        "base_standard": base_standard.model_dump(mode="json"),
        "adapter_standard": adapter_standard.model_dump(mode="json"),
        "adapter_comparison": comparison.model_dump(mode="json"),
        "context_compression": compression.model_dump(mode="json"),
        "general_regression_evaluated": general_regression is not None,
        "experiment_valid": experiment_valid,
        "self_model_qualified": comparison.promotion_allowed,
        "context_compression_qualified": compression.compression_qualified,
        "promotion_ready": promotion_ready,
        "promotion_blockers": blockers,
    }
    fingerprint = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    report = EmpiricalAdapterExperimentReport.model_validate(
        {**payload, "report_fingerprint": fingerprint}
    )
    report.write(output)
    return report
