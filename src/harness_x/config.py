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


class HarnessConfig(BaseModel):
    system_version: SystemVersion
    trace_directory: Path = Path(".harness-x/traces")
    budget: ComputeBudget = Field(default_factory=ComputeBudget)
    gates: GateConfig = Field(default_factory=GateConfig)

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
