"""Grounded self-model curriculum and dataset contracts.

Milestone 12 training examples are labeled by deterministic Harness X rules,
known injected faults, or known interventions. They deliberately do not contain
teacher-model answers or private chain-of-thought.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CurriculumFamily(StrEnum):
    STRUCTURAL = "structural"
    OPERATIONAL = "operational"
    DIAGNOSTIC = "diagnostic"
    CAUSAL_COUNTERFACTUAL = "causal_counterfactual"


class DatasetSplit(StrEnum):
    TRAIN = "train"
    EVAL = "eval"


class LabelSource(StrEnum):
    SYSTEM_RULE = "system_rule"
    INJECTED_FAULT = "injected_fault"
    KNOWN_INTERVENTION = "known_intervention"


class ScenarioDefinition(BaseModel):
    """Versioned seed definition used to deterministically construct one example."""

    model_config = ConfigDict(frozen=True)

    seed_id: str = Field(min_length=1)
    family: CurriculumFamily
    split: DatasetSplit
    task: str = Field(min_length=1)
    fault_family: str | None = None
    architecture_family: str = Field(default="default", min_length=1)
    tags: tuple[str, ...] = ()


class SelfModelExample(BaseModel):
    """Portable training/evaluation record with an externally grounded label."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "self-model-example-v1"
    scenario_id: str = Field(min_length=16)
    scenario_fingerprint: str = Field(min_length=64, max_length=64)
    definition: ScenarioDefinition
    system_version: str = Field(min_length=1)
    source_state_fingerprint: str = Field(min_length=64, max_length=64)
    input_state: dict[str, Any]
    expected_decision: dict[str, Any]
    accepted_alternatives: tuple[dict[str, Any], ...] = ()
    rationale_metadata: dict[str, Any] = Field(default_factory=dict)
    label_source: LabelSource
    generator_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def verify_fingerprint(self) -> "SelfModelExample":
        expected = example_fingerprint(
            definition=self.definition,
            system_version=self.system_version,
            source_state_fingerprint=self.source_state_fingerprint,
            input_state=self.input_state,
            expected_decision=self.expected_decision,
            accepted_alternatives=self.accepted_alternatives,
            rationale_metadata=self.rationale_metadata,
            label_source=self.label_source,
            generator_version=self.generator_version,
        )
        if self.scenario_fingerprint != expected:
            raise ValueError("self-model example fingerprint does not match content")
        return self


class CurriculumManifest(BaseModel):
    """Machine-readable split/coverage proof for one generated dataset."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "self-model-curriculum-manifest-v1"
    generator_version: str
    system_version: str
    source_state_fingerprint: str = Field(min_length=64, max_length=64)
    dataset_fingerprint: str = Field(min_length=64, max_length=64)
    train_count: int = Field(ge=0)
    eval_count: int = Field(ge=0)
    train_seed_ids: tuple[str, ...]
    eval_seed_ids: tuple[str, ...]
    held_out_fault_families: tuple[str, ...]
    family_counts: dict[str, dict[str, int]]

    @model_validator(mode="after")
    def split_isolation(self) -> "CurriculumManifest":
        overlap = set(self.train_seed_ids) & set(self.eval_seed_ids)
        if overlap:
            raise ValueError(f"training/evaluation seed leakage: {sorted(overlap)!r}")
        return self


class CurriculumDataset(BaseModel):
    """Deterministically ordered self-model curriculum."""

    model_config = ConfigDict(frozen=True)

    examples: tuple[SelfModelExample, ...]
    manifest: CurriculumManifest

    @property
    def train(self) -> tuple[SelfModelExample, ...]:
        return tuple(
            item for item in self.examples
            if item.definition.split == DatasetSplit.TRAIN
        )

    @property
    def eval(self) -> tuple[SelfModelExample, ...]:
        return tuple(
            item for item in self.examples
            if item.definition.split == DatasetSplit.EVAL
        )

    def write(self, output_directory: str | Path) -> None:
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        for split, values in (("train", self.train), ("eval", self.eval)):
            path = output / f"{split}.jsonl"
            rows = "".join(
                item.model_dump_json(exclude_none=False) + "\n"
                for item in values
            )
            path.write_text(rows, encoding="utf-8")
        (output / "manifest.json").write_text(
            self.manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def example_fingerprint(
    *,
    definition: ScenarioDefinition,
    system_version: str,
    source_state_fingerprint: str,
    input_state: dict[str, Any],
    expected_decision: dict[str, Any],
    accepted_alternatives: tuple[dict[str, Any], ...],
    rationale_metadata: dict[str, Any],
    label_source: LabelSource,
    generator_version: str,
) -> str:
    payload = {
        "definition": definition.model_dump(mode="json"),
        "system_version": system_version,
        "source_state_fingerprint": source_state_fingerprint,
        "input_state": input_state,
        "expected_decision": expected_decision,
        "accepted_alternatives": list(accepted_alternatives),
        "rationale_metadata": rationale_metadata,
        "label_source": label_source.value,
        "generator_version": generator_version,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_example(
    *,
    definition: ScenarioDefinition,
    system_version: str,
    source_state_fingerprint: str,
    input_state: dict[str, Any],
    expected_decision: dict[str, Any],
    label_source: LabelSource,
    generator_version: str,
    accepted_alternatives: tuple[dict[str, Any], ...] = (),
    rationale_metadata: dict[str, Any] | None = None,
) -> SelfModelExample:
    rationale = rationale_metadata or {}
    fingerprint = example_fingerprint(
        definition=definition,
        system_version=system_version,
        source_state_fingerprint=source_state_fingerprint,
        input_state=input_state,
        expected_decision=expected_decision,
        accepted_alternatives=accepted_alternatives,
        rationale_metadata=rationale,
        label_source=label_source,
        generator_version=generator_version,
    )
    scenario_material = canonical_json(
        {
            "seed_id": definition.seed_id,
            "architecture_family": definition.architecture_family,
            "source_state_fingerprint": source_state_fingerprint,
        }
    ).encode("utf-8")
    scenario_id = f"selfmodel_{hashlib.sha256(scenario_material).hexdigest()[:24]}"
    return SelfModelExample(
        scenario_id=scenario_id,
        scenario_fingerprint=fingerprint,
        definition=definition,
        system_version=system_version,
        source_state_fingerprint=source_state_fingerprint,
        input_state=input_state,
        expected_decision=expected_decision,
        accepted_alternatives=accepted_alternatives,
        rationale_metadata=rationale,
        label_source=label_source,
        generator_version=generator_version,
    )
