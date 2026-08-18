"""Grounded training-data generation for Harness X self-model adapters."""

from .curriculum import (
    AUTHORITY_OWNERS,
    GENERATOR_VERSION,
    HELD_OUT_FAULT_FAMILIES,
    CurriculumGenerationError,
    CurriculumGenerator,
    architecture_family,
)
from .fault_injection import FaultFamily, InjectedFaultCase, inject_fault
from .models import (
    CurriculumDataset,
    CurriculumFamily,
    CurriculumManifest,
    DatasetSplit,
    LabelSource,
    ScenarioDefinition,
    SelfModelExample,
)

__all__ = [
    "AUTHORITY_OWNERS",
    "GENERATOR_VERSION",
    "HELD_OUT_FAULT_FAMILIES",
    "CurriculumDataset",
    "CurriculumFamily",
    "CurriculumGenerationError",
    "CurriculumGenerator",
    "CurriculumManifest",
    "DatasetSplit",
    "FaultFamily",
    "InjectedFaultCase",
    "LabelSource",
    "ScenarioDefinition",
    "SelfModelExample",
    "architecture_family",
    "inject_fault",
]
