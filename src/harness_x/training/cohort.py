"""Milestone 13 cohort construction with fault/configuration holdouts."""

from __future__ import annotations

import hashlib
from collections import Counter
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
    """Combined training/evaluation examples with explicit architecture holdouts."""

    model_config = ConfigDict(frozen=True)

    train: tuple[SelfModelExample, ...]
    eval: tuple[SelfModelExample, ...]
    manifest: TrainingCohortManifest


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
                # Entire architecture configurations are unseen during training.
                eval_candidates.append(
                    item.model_copy(
                        update={
                            "definition": item.definition.model_copy(
                                update={"split": DatasetSplit.EVAL}
                            )
                        }
                    )
                )
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

    payload = {
        "train": [item.scenario_fingerprint for item in train],
        "eval": [item.scenario_fingerprint for item in evaluation],
        "held_out_architecture_families": sorted(holdouts),
    }
    fingerprint = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
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

    # Defensive check that each architecture-qualified seed appears only once per split.
    duplicates = [seed for seed, count in Counter((*train_seeds, *eval_seeds)).items() if count > 1]
    if duplicates:
        raise ValueError(f"duplicate architecture-qualified cohort seeds: {sorted(duplicates)!r}")

    return TrainingCohort(train=train, eval=evaluation, manifest=manifest)
