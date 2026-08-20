"""Small replaceable peripheral-controller research surfaces."""

from .training_data import (
    COLLECTOR_VERSION,
    USEFULNESS_POLICY_VERSION,
    GateModelRecommendation,
    GateOutcomeSummary,
    GateTrainingDataCollector,
    GateTrainingDataError,
    GateTrainingDataset,
    GateTrainingDatasetManifest,
    GateTrainingRecord,
    GateUsefulnessLabel,
    SourceTraceDescriptor,
    UsefulnessState,
    collect_gate_training_dataset,
    load_gate_training_dataset,
    load_model_recommendations,
)

__all__ = [
    "COLLECTOR_VERSION",
    "USEFULNESS_POLICY_VERSION",
    "GateModelRecommendation",
    "GateOutcomeSummary",
    "GateTrainingDataCollector",
    "GateTrainingDataError",
    "GateTrainingDataset",
    "GateTrainingDatasetManifest",
    "GateTrainingRecord",
    "GateUsefulnessLabel",
    "SourceTraceDescriptor",
    "UsefulnessState",
    "collect_gate_training_dataset",
    "load_gate_training_dataset",
    "load_model_recommendations",
]
