"""Process-isolated training boundary for empirical adapter experiments.

Unsloth patches Transformers model classes in-process. A fresh Hugging Face model
loaded after Unsloth training can therefore inherit patched forwards without the
instance attributes that FastLanguageModel installs. Real empirical experiments run
training in a child interpreter and hand only validated PEFT artifacts back to the
clean parent process before held-out evaluation.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import empirical_experiment as _empirical
from .adapter_training import (
    AdapterTrainingArtifact,
    PreparedTrainingBundle,
    TrainingBackend,
    load_prepared_training_bundle,
)
from .empirical_experiment import EmpiricalAdapterExperimentReport, digest_tree
from .evaluation import GeneralRegressionResult
from .models import canonical_json


@dataclass(frozen=True)
class ValidatedTrainingSource:
    training_root: Path
    adapter_directory: Path
    artifact: AdapterTrainingArtifact
    artifact_sha256: str
    adapter_tree_fingerprint: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _adapter_tree_fingerprint(adapter_directory: Path) -> str:
    files = digest_tree(adapter_directory)
    payload = [item.model_dump(mode="json") for item in files]
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _resolve_training_root(directory: str | Path) -> Path:
    root = Path(directory)
    direct = root / "adapter-artifact.json"
    nested = root / "training" / "adapter-artifact.json"
    if direct.is_file():
        return root
    if nested.is_file():
        return root / "training"
    raise ValueError(
        "resume/staged training directory must contain adapter-artifact.json either "
        "directly or under training/"
    )


def validate_training_source(
    bundle: PreparedTrainingBundle,
    backend: TrainingBackend | str,
    directory: str | Path,
) -> ValidatedTrainingSource:
    """Validate an existing training artifact against the exact prepared bundle.

    The recorded adapter path is deliberately not trusted. Expensive empirical
    outputs may be moved after a failed evaluation, so the adapter is rebound to the
    validated directory containing the artifact.
    """

    backend = TrainingBackend(backend)
    training_root = _resolve_training_root(directory)
    artifact_path = training_root / "adapter-artifact.json"
    artifact = AdapterTrainingArtifact.model_validate_json(
        artifact_path.read_text(encoding="utf-8")
    )

    expected_tokenizer_revision = bundle.config.tokenizer_revision
    mismatches: list[str] = []
    checks = (
        ("base_model", artifact.base_model, bundle.config.base_model),
        (
            "base_model_revision",
            artifact.base_model_revision,
            bundle.config.base_model_revision,
        ),
        (
            "tokenizer_revision",
            artifact.tokenizer_revision,
            expected_tokenizer_revision,
        ),
        ("method", artifact.method, bundle.config.method),
        ("backend", artifact.backend, backend),
        (
            "training_examples",
            artifact.training_examples,
            len(bundle.train_records),
        ),
        (
            "cohort_fingerprint",
            artifact.cohort_fingerprint,
            bundle.cohort_manifest.cohort_fingerprint,
        ),
    )
    for field, actual, expected in checks:
        if actual != expected:
            mismatches.append(f"{field}: {actual!r} != {expected!r}")
    if mismatches:
        raise ValueError(
            "training artifact does not match the effective prepared bundle: "
            + "; ".join(mismatches)
        )

    adapter_directory = training_root / "adapter"
    if not adapter_directory.is_dir():
        raise ValueError("validated training artifact is missing its adapter directory")
    adapter_tree_fingerprint = _adapter_tree_fingerprint(adapter_directory)

    return ValidatedTrainingSource(
        training_root=training_root,
        adapter_directory=adapter_directory,
        artifact=artifact,
        artifact_sha256=_sha256_file(artifact_path),
        adapter_tree_fingerprint=adapter_tree_fingerprint,
    )


class _ValidatedArtifactTrainer:
    """Replay one already-trained adapter into M20 without importing its backend."""

    def __init__(
        self,
        source: ValidatedTrainingSource,
        *,
        backend: TrainingBackend,
        source_kind: str,
    ) -> None:
        self.source = source
        self.backend = backend
        self.source_kind = source_kind
        self.copied = False

    def train(
        self,
        bundle: PreparedTrainingBundle,
        output_directory: str | Path,
    ) -> AdapterTrainingArtifact:
        validated = validate_training_source(bundle, self.backend, self.source.training_root)
        output = Path(output_directory)
        if output.exists() and any(output.iterdir()):
            raise ValueError("empirical training handoff output must be empty")
        output.mkdir(parents=True, exist_ok=True)
        destination_adapter = output / "adapter"
        shutil.copytree(validated.adapter_directory, destination_adapter)

        train_result = dict(validated.artifact.train_result)
        train_result["empirical_training_boundary"] = {
            "source_kind": self.source_kind,
            "process_isolation": self.source_kind == "isolated_subprocess",
            "source_artifact_sha256": validated.artifact_sha256,
            "source_adapter_tree_fingerprint": validated.adapter_tree_fingerprint,
        }
        rebound = validated.artifact.model_copy(
            update={
                "adapter_path": str(destination_adapter),
                "train_result": train_result,
            }
        )
        (output / "adapter-artifact.json").write_text(
            rebound.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        self.copied = True
        return rebound


@contextmanager
def _install_training_handoff(
    trainer: _ValidatedArtifactTrainer,
) -> Iterator[None]:
    """Temporarily inject the validated handoff into the unchanged M20 evaluator."""

    original = _empirical.trainer_for_backend

    def factory(requested: TrainingBackend | str) -> _ValidatedArtifactTrainer:
        requested_backend = TrainingBackend(requested)
        if requested_backend != trainer.backend:
            raise ValueError(
                f"training handoff backend mismatch: {requested_backend.value} != "
                f"{trainer.backend.value}"
            )
        return trainer

    _empirical.trainer_for_backend = factory
    try:
        yield
    finally:
        _empirical.trainer_for_backend = original


def _run_training_worker(
    prepared_directory: Path,
    backend: TrainingBackend,
    staging_directory: Path,
) -> None:
    command = [
        sys.executable,
        "-m",
        "harness_x.training.empirical_worker",
        str(prepared_directory),
        "--backend",
        backend.value,
        "--output",
        str(staging_directory),
    ]
    subprocess.run(command, check=True)


def _staging_directory_for(output: Path) -> Path:
    return output.with_name(f"{output.name}.training-staging")


def run_isolated_empirical_adapter_experiment(
    prepared_directory: str | Path,
    *,
    backend: TrainingBackend | str,
    output_directory: str | Path,
    load_in_4bit: bool = True,
    max_new_tokens: int = 512,
    general_regression: GeneralRegressionResult | None = None,
    reference: bool = False,
    resume_training_directory: str | Path | None = None,
) -> EmpiricalAdapterExperimentReport:
    """Run M20 while keeping training-framework patches outside the evaluator.

    Reference mode remains in-process because it imports no model backend. Real runs
    either train in a child interpreter or validate and reuse a previously completed
    training artifact. Evaluation always executes in the clean parent interpreter.
    """

    prepared = Path(prepared_directory)
    output = Path(output_directory)
    backend = TrainingBackend(backend)

    if reference:
        if resume_training_directory is not None:
            raise ValueError("reference mode cannot resume a real training artifact")
        return _empirical.run_empirical_adapter_experiment(
            prepared,
            backend=backend,
            output_directory=output,
            load_in_4bit=load_in_4bit,
            max_new_tokens=max_new_tokens,
            general_regression=general_regression,
            reference=True,
        )

    if output.exists() and any(output.iterdir()):
        raise ValueError("empirical experiment output directory must be empty")

    bundle = load_prepared_training_bundle(prepared)
    created_staging: Path | None = None
    if resume_training_directory is not None:
        source = validate_training_source(bundle, backend, resume_training_directory)
        source_kind = "resumed_existing_training"
    else:
        staging = _staging_directory_for(output)
        if staging.exists() and any(staging.iterdir()):
            raise ValueError(
                f"training staging directory is not empty: {staging}; resume it with "
                "--resume-training or move/remove it explicitly"
            )
        if staging.exists():
            staging.rmdir()
        _run_training_worker(prepared, backend, staging)
        created_staging = staging
        source = validate_training_source(bundle, backend, staging)
        source_kind = "isolated_subprocess"

    handoff = _ValidatedArtifactTrainer(
        source,
        backend=backend,
        source_kind=source_kind,
    )
    try:
        with _install_training_handoff(handoff):
            return _empirical.run_empirical_adapter_experiment(
                prepared,
                backend=backend,
                output_directory=output,
                load_in_4bit=load_in_4bit,
                max_new_tokens=max_new_tokens,
                general_regression=general_regression,
                reference=False,
            )
    finally:
        # Once the adapter has been copied into the final evidence tree, the
        # subprocess staging tree is redundant. Before that point it is preserved so
        # a costly successful training run is never discarded by a later failure.
        if created_staging is not None and handoff.copied and created_staging.exists():
            shutil.rmtree(created_staging)
