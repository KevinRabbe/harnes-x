"""Grounded curriculum, training, and evaluation for Harness X self-model adapters."""

from .adapter_training import (
    AdapterMethod,
    AdapterTrainingArtifact,
    AdapterTrainingConfig,
    HuggingFacePeftTrainer,
    PreparedTrainingBundle,
    prepare_training_bundle,
)
from .cohort import (
    TrainingCohort,
    TrainingCohortManifest,
    build_training_cohort,
    load_training_cohort,
)
from .curriculum import (
    AUTHORITY_OWNERS,
    GENERATOR_VERSION,
    HELD_OUT_FAULT_FAMILIES,
    CurriculumGenerationError,
    CurriculumGenerator,
    architecture_family,
)
from .dataset import load_curriculum
from .evaluation import (
    AdapterComparisonReport,
    AdapterPromotionPolicy,
    FamilyEvaluation,
    GeneralRegressionResult,
    SelfModelEvaluationReport,
    SelfModelPrediction,
    compare_base_and_adapter,
    evaluate_self_model,
)
from .fault_injection import FaultFamily, InjectedFaultCase, inject_fault
from .formatting import (
    FormattedSelfModelRecord,
    TrainingMessage,
    format_self_model_example,
)
from .models import (
    CurriculumDataset,
    CurriculumFamily,
    CurriculumManifest,
    DatasetSplit,
    LabelSource,
    ScenarioDefinition,
    SelfModelExample,
)
from .predictors import HuggingFaceSelfModelPredictor, parse_structured_prediction

__all__ = [
    "AUTHORITY_OWNERS",
    "GENERATOR_VERSION",
    "HELD_OUT_FAULT_FAMILIES",
    "AdapterComparisonReport",
    "AdapterMethod",
    "AdapterPromotionPolicy",
    "AdapterTrainingArtifact",
    "AdapterTrainingConfig",
    "CurriculumDataset",
    "CurriculumFamily",
    "CurriculumGenerationError",
    "CurriculumGenerator",
    "CurriculumManifest",
    "DatasetSplit",
    "FamilyEvaluation",
    "FaultFamily",
    "FormattedSelfModelRecord",
    "GeneralRegressionResult",
    "HuggingFacePeftTrainer",
    "HuggingFaceSelfModelPredictor",
    "InjectedFaultCase",
    "LabelSource",
    "PreparedTrainingBundle",
    "ScenarioDefinition",
    "SelfModelEvaluationReport",
    "SelfModelExample",
    "SelfModelPrediction",
    "TrainingCohort",
    "TrainingCohortManifest",
    "TrainingMessage",
    "architecture_family",
    "build_training_cohort",
    "compare_base_and_adapter",
    "evaluate_self_model",
    "format_self_model_example",
    "inject_fault",
    "load_curriculum",
    "load_training_cohort",
    "parse_structured_prediction",
    "prepare_training_bundle",
]
