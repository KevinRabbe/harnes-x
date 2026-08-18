"""Common machine-readable errors and operation results."""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


class ErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    CONFIGURATION = "configuration"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PERMISSION_DENIED = "permission_denied"
    INVARIANT_VIOLATION = "invariant_violation"
    INTERNAL = "internal"


class OperationError(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, object] = Field(default_factory=dict)


class Result(BaseModel, Generic[T]):
    """Explicit success/error result for boundaries where exceptions are undesirable."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    value: T | None = None
    error: OperationError | None = None

    @model_validator(mode="after")
    def exactly_one_branch(self) -> "Result[T]":
        if (self.value is None) == (self.error is None):
            raise ValueError("Result must contain exactly one of value or error")
        return self

    @classmethod
    def ok(cls, value: T) -> "Result[T]":
        return cls(value=value)

    @classmethod
    def fail(cls, code: ErrorCode, message: str, **details: object) -> "Result[T]":
        return cls(error=OperationError(code=code, message=message, details=details))


class HarnessError(RuntimeError):
    """Base exception for programmer/invariant failures."""


class TraceError(HarnessError):
    """Invalid trace operation or ordering."""


class TraceCorruptionError(TraceError):
    """Trace ledger contents fail structural or integrity validation."""


class ReplayError(HarnessError):
    """An event sequence cannot be replayed into valid authoritative state."""


class ReplayMismatchError(ReplayError):
    """Replay completed but does not match the recorded expected final state."""
