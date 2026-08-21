"""Typed identifiers for authoritative Harness X state."""

from __future__ import annotations

from typing import ClassVar, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, field_validator

TIdentifier = TypeVar("TIdentifier", bound="Identifier")


class Identifier(BaseModel):
    """Immutable, serializable, prefixed identifier."""

    model_config = ConfigDict(frozen=True)

    prefix: ClassVar[str] = "id"
    value: str

    @field_validator("value")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        expected = f"{cls.prefix}_"
        if not value.startswith(expected):
            raise ValueError(f"{cls.__name__} must start with {expected!r}")
        if len(value) <= len(expected):
            raise ValueError(f"{cls.__name__} cannot have an empty payload")
        return value

    @classmethod
    def new(cls: type[TIdentifier]) -> TIdentifier:
        return cls(value=f"{cls.prefix}_{uuid4().hex}")

    def __str__(self) -> str:
        return self.value


class TaskId(Identifier):
    prefix = "task"


class GoalId(Identifier):
    prefix = "goal"


class MemoryId(Identifier):
    prefix = "mem"


class RoutineId(Identifier):
    prefix = "routine"


class TraceId(Identifier):
    prefix = "trace"


class CandidateId(Identifier):
    prefix = "candidate"


class EventId(Identifier):
    prefix = "event"


class CodingPlanId(Identifier):
    """Stable identity for a versioned coding plan artifact."""

    prefix = "codingplan"


class CommitmentId(Identifier):
    """Stable identity for one durable coding obligation."""

    prefix = "commitment"


class SystemVersion(BaseModel):
    """Version of the complete running Harness X system, not only the model."""

    model_config = ConfigDict(frozen=True)

    value: str

    @field_validator("value")
    @classmethod
    def non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("SystemVersion cannot be empty")
        return value

    def __str__(self) -> str:
        return self.value
