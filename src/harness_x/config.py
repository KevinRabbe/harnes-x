"""Typed configuration loading for Harness X."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from .core.contracts import ComputeBudget
from .core.ids import SystemVersion


class HarnessConfig(BaseModel):
    system_version: SystemVersion
    trace_directory: Path = Path(".harness-x/traces")
    budget: ComputeBudget = Field(default_factory=ComputeBudget)

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
