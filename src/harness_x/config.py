"""Typed configuration loading for Harness X."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from .core.contracts import ComputeBudget
from .core.ids import SystemVersion


class RetrievalGateConfig(BaseModel):
    policy_version: str = "retrieval-v0"
    pressure_suppress_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    max_recent_retrievals: int = Field(default=3, ge=0)
    default_limit: int = Field(default=5, gt=0)
    always_retrieve_routines: tuple[str, ...] = ("research", "recovery", "debugging")


class WriteGateConfig(BaseModel):
    policy_version: str = "write-v0"
    default_memory_class: str = "working"
    memory_class_by_kind: dict[str, str] = Field(
        default_factory=lambda: {
            "goal": "goal",
            "constraint": "goal",
            "episode": "episodic",
            "outcome": "episodic",
            "failure": "error",
            "error": "error",
            "anomaly": "error",
            "observation": "working",
            "working": "working",
        }
    )


class FocusGateConfig(BaseModel):
    policy_version: str = "focus-v0"
    max_focus_items: int = Field(default=5, gt=0)
    pin_priority_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    max_auto_pins: int = Field(default=3, ge=0)


class ComputeGateConfig(BaseModel):
    policy_version: str = "compute-v0"


class MaintenanceGateConfig(BaseModel):
    policy_version: str = "maintenance-v0"
    working_pressure_trigger: float = Field(default=0.85, ge=0.0, le=1.0)
    unresolved_error_trigger: int = Field(default=3, ge=0)
    repeated_failure_trigger: int = Field(default=2, ge=0)


class GateConfig(BaseModel):
    retrieval: RetrievalGateConfig = Field(default_factory=RetrievalGateConfig)
    write: WriteGateConfig = Field(default_factory=WriteGateConfig)
    focus: FocusGateConfig = Field(default_factory=FocusGateConfig)
    compute: ComputeGateConfig = Field(default_factory=ComputeGateConfig)
    maintenance: MaintenanceGateConfig = Field(default_factory=MaintenanceGateConfig)


class ImprovementPromotionConfig(BaseModel):
    """Software-owned policy for the narrow Milestone 16 live-promotion authority."""

    policy_version: str = "live-promotion-v1"
    allow_auto_promotion: bool = False
    allowed_change_types: tuple[str, ...] = (
        "config_threshold",
        "retrieval_scoring_policy",
    )
    max_risk_level: str = "low"
    min_paired_runs: int = Field(default=3, ge=1)
    require_zero_regressions: bool = True
    require_zero_new_failure_modes: bool = True
    require_zero_budget_violations: bool = True
    require_baseline_untouched: bool = True
    require_teardown_verified: bool = True

    @field_validator("policy_version", "max_risk_level")
    @classmethod
    def normalize_promotion_text(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("promotion policy text fields cannot be blank")
        return normalized

    @field_validator("allowed_change_types")
    @classmethod
    def normalize_allowed_change_types(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().casefold() for value in values if value.strip())
        if not normalized:
            raise ValueError("at least one live-promotable change type is required")
        if len(normalized) != len(set(normalized)):
            raise ValueError("live-promotable change types cannot contain duplicates")
        return normalized


class ImprovementConfig(BaseModel):
    promotion: ImprovementPromotionConfig = Field(default_factory=ImprovementPromotionConfig)


class HarnessConfig(BaseModel):
    system_version: SystemVersion
    trace_directory: Path = Path(".harness-x/traces")
    budget: ComputeBudget = Field(default_factory=ComputeBudget)
    gates: GateConfig = Field(default_factory=GateConfig)
    improvement: ImprovementConfig = Field(default_factory=ImprovementConfig)

    @field_validator("system_version", mode="before")
    @classmethod
    def parse_system_version(cls, value: object) -> object:
        if isinstance(value, str):
            return {"value": value}
        return value


def load_config(path: str | Path) -> HarnessConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Harness X configuration must be a mapping")
    return HarnessConfig.model_validate(raw)
