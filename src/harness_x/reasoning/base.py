"""Stable adapter-facing contracts for replaceable reasoning cores."""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReasoningCoreError(RuntimeError):
    """Normalized failure at the model/runtime boundary."""


class ReasoningCoreInfo(BaseModel):
    """Declared identity of one replaceable reasoning backend."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    transport: str = Field(min_length=1)
    model_inference: bool

    @field_validator("name", "version", "model", "transport")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reasoning-core identity fields cannot be blank")
        return value


class RawProposal(BaseModel):
    """Untrusted proposal payload returned by a core before Harness X assigns identity/provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class RawActionProposal(BaseModel):
    """Untrusted tool proposal returned by a core before the tool boundary sees it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class RawReasoningOutput(BaseModel):
    """Minimal schema a model/runtime is allowed to return.

    Candidate IDs, provenance, verification state, task ownership, and state mutation
    are deliberately absent. Those are assigned/validated by Harness X software.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["complete", "continue", "blocked"]
    proposals: tuple[RawProposal, ...] = ()
    actions: tuple[RawActionProposal, ...] = ()
    observations: tuple[str, ...] = ()
    requested_additional_steps: int = Field(default=0, ge=0, le=4096)


@runtime_checkable
class ReasoningCore(Protocol):
    """Replaceable generation backend.

    The backend receives only a bounded, already-constructed context and may return
    structured proposals. It has no direct access to memory, tools, or orchestrator
    owners.
    """

    @property
    def info(self) -> ReasoningCoreInfo: ...

    def generate(self, context: "ContextBuildResult") -> RawReasoningOutput: ...


# Avoid importing context_builder at module import time; the protocol annotation above
# is resolved by type checkers and keeps the runtime dependency graph acyclic.
