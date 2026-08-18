"""Shared contracts for explicit Harness X memory classes."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MemoryClass(StrEnum):
    GOAL = "goal"
    WORKING = "working"
    EPISODIC = "episodic"
    ERROR = "error"


class MemoryPressure(BaseModel):
    """Inspectable capacity state for a bounded memory surface."""

    model_config = ConfigDict(frozen=True)

    capacity_units: int = Field(gt=0)
    used_units: int = Field(ge=0)
    pressure: float = Field(ge=0.0)

    @classmethod
    def from_usage(cls, capacity_units: int, used_units: int) -> "MemoryPressure":
        return cls(
            capacity_units=capacity_units,
            used_units=used_units,
            pressure=used_units / capacity_units,
        )
