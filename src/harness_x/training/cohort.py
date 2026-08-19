"""Milestone 13 cohort construction with fault/configuration holdouts."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import CurriculumDataset, DatasetSplit, SelfModelExample, canonical_json


class TrainingCohortManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "self-model-training-cohort-v1"
    cohort_fingerprint: str = Field(min_length=64, max_length=64)
    train_count: int = Field(ge=0)
    eval_count: int = Field(ge=0)
    train_architecture_families: tuple[str, ...]
    eval_architecture_families: tuple[str, ...]
    held_out_architecture_families: tuple[str, ...]
    train_seed_ids: tuple[str, ...]
    eval_seed_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_isolation(self) -> "TrainingCohortManifest":
        held_out = set(self.held_out_architecture_families)
        leaked = held_out & set(self.train_architecture_families)
        if leaked:
            raise ValueError(
                f"held-out architecture families leaked into training: {sorted(leaked)!r}"
            )
        overlap = set(self.train_seed_ids) & set(self.eval_seed_ids)
        if overlap:
            raise ValueError(f"cohort seed leakage: {sorted(overlap)!r}")
        return self


class TrainingCohort(BaseModel):
    """Combined training/evaluation examples with explicit architecture holdouts.

    The source ``SelfModelExample`` objects remain byte-for-byte semantically intact.
    A cohort decides whether an example is used for training or evaluation without
    rewriting the signed Milestone 12 ``definition.split`` field.
    """

    model_config = ConfigDict(frozen=True)

    train: tuple[SelfModelExample, ...]
    eval: tuple[SelfModelExample, ...]
    manifest: TrainingCohortManifest

    def write(self, output_directory: str | Path) -> Path:
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        for filename, values in (
            ("train-examples.jsonl", self.train),
            ("eval-examples.jsonl", self.eval),
        ):
            (output / filename).write_text(
                "".join(item.model_dump_json() + "\n" for item in values),
                encoding="utf-8",
            )
        (output / "cohort-manifest.json").write_text(
            self.manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return output


def _dedupe(examples: Iterable[SelfModelExample]) -> tuple[SelfModelExample, ...]:
    by_fingerprint: dict[str, SelfModelExample] = {}
    for item in examples:
        prior = by_fingerprint.get(item.scenario_fingerprint)
        if prior is not None and prior != item:
            raise ValueError("same scenario fingerprint maps to different content")
        by_fingerprint[item.scenario_fingerprint] = item
    return tuple(
        sorted(
            by_fingerprint.values(),
            key=lambda item: (
                item.definition.architecture_family,
                item.definition.family.value,
                item.definition.seed_id,
                item.scenario_fingerprint,
            ),
        )
    )


def _cohort_fingerprint(
    train: tuple[SelfModelExample, ...],
    evaluation: tuple[SelfModelExample, ...],
    holdouts: Iterable[str],
) -> str:
    payload = {
        "train": [item.scenario_fingerprint for item in train],
        "eval": [item.scenario_fingerprint for item in evaluation],
        "held_out_architecture_families": sorted(holdouts),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_training_cohort(
    datasets: Iterable[CurriculumDataset],
    *,
    held_out_architecture_families: Iterable[str] = (),
) -> TrainingCohort:
    materialized = tuple(datasets)
    if not materialized:
        raise ValueError("at least one curriculum dataset is required")

    holdouts = frozenset(value.strip() for value in held_out_architecture_families if value.strip())
    train_candidates: list[SelfModelExample] = []
    eval_candidates: list[SelfModelExample] = []

    for dataset in materialized:
        for item in dataset.examples:
            architecture = item.definition.architecture_family
            if architecture in holdouts:
                eval_candidates.append(item)
            elif item.definition.split == DatasetSplit.TRAIN:
                train_candidates.append(item)
            else:
                eval_candidates.append(item)

    train = _dedupe(train_candidates)
    evaluation = _dedupe(eval_candidates)
    if not train:
        raise ValueError("training cohort contains no training examples")
    if not evaluation:
        raise ValueError("training cohort contains no evaluation examples")

    train_fingerprints = {item.scenario_fingerprint for item in train}
    eval_fingerprints = {item.scenario_fingerprint for item in evaluation}
    overlap = train_fingerprints & eval_fingerprints
    if overlap:
        raise ValueError(f"scenario leakage between train/eval: {sorted(overlap)!r}")

    train_seeds = tuple(
        f"{item.definition.architecture_family}:{item.definition.seed_id}" for item in train
    )
    eval_seeds = tuple(
        f"{item.definition.architecture_family}:{item.definition.seed_id}" for item in evaluation
    )
    if set(train_seeds) & set(eval_seeds):
        raise ValueError("architecture-qualified seed leakage between train/eval")

    fingerprint = _cohort_fingerprint(train, evaluation, holdouts)
    manifest = TrainingCohortManifest(
        cohort_fingerprint=fingerprint,
        train_count=len(train),
        eval_count=len(evaluation),
        train_architecture_families=tuple(
            sorted({item.definition.architecture_family for item in train})
        ),
        eval_architecture_families=tuple(
            sorted({item.definition.architecture_family for item in evaluation})
        ),
        held_out_architecture_families=tuple(sorted(holdouts)),
        train_seed_ids=train_seeds,
        eval_seed_ids=eval_seeds,
    )

    duplicates = [seed for seed, count in Counter((*train_seeds, *eval_seeds)).items() if count > 1]
    if duplicates:
        raise ValueError(f"duplicate architecture-qualified cohort seeds: {sorted(duplicates)!r}")

    return TrainingCohort(train=train, eval=evaluation, manifest=manifest)


def load_training_cohort(directory: str | Path) -> TrainingCohort:
    root = Path(directory)
    manifest = TrainingCohortManifest.model_validate_json(
        (root / "cohort-manifest.json").read_text(encoding="utf-8")
    )

    def read_jsonl(name: str) -> tuple[SelfModelExample, ...]:
        values: list[SelfModelExample] = []
        for line in (root / name).read_text(encoding="utf-8").splitlines():
            if line.strip():
                values.append(SelfModelExample.model_validate_json(line))
        return tuple(values)

    train = read_jsonl("train-examples.jsonl")
    evaluation = read_jsonl("eval-examples.jsonl")
    if len(train) != manifest.train_count or len(evaluation) != manifest.eval_count:
        raise ValueError("cohort example counts do not match manifest")
    if _cohort_fingerprint(
        train, evaluation, manifest.held_out_architecture_families
    ) != manifest.cohort_fingerprint:
        raise ValueError("cohort fingerprint does not match persisted examples")
    return TrainingCohort(train=train, eval=evaluation, manifest=manifest)
