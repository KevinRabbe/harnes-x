"""First model-independent contracts for the Harness X architecture."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .ids import CandidateId, GoalId, RoutineId, TaskId
from .provenance import Provenance

JsonObject = dict[str, Any]


class ComputeBudget(BaseModel):
    """Externally enforced resource envelope for one unit of work."""

    model_config = ConfigDict(frozen=True)

    max_reasoning_steps: int = Field(default=32, ge=0)
    max_tool_actions: int = Field(default=16, ge=0)
    max_output_tokens: int = Field(default=8192, ge=0)


class Observation(BaseModel):
    """Something observed from an authoritative boundary."""

    task_id: TaskId
    kind: str
    content: JsonObject
    provenance: Provenance


class Proposal(BaseModel):
    """A suggested state transition; never authoritative merely because a model emitted it."""

    candidate_id: CandidateId
    task_id: TaskId
    summary: str
    payload: JsonObject = Field(default_factory=dict)
    provenance: Provenance


class ActionProposal(BaseModel):
    """A proposed environment/tool action awaiting validation and execution."""

    candidate_id: CandidateId
    task_id: TaskId
    tool_name: str
    arguments: JsonObject = Field(default_factory=dict)
    provenance: Provenance


class VerificationResult(BaseModel):
    """Independent acceptance/rejection result for generated or proposed state."""

    candidate_id: CandidateId
    accepted: bool
    checks: list[str] = Field(default_factory=list)
    reason: str | None = None
    provenance: Provenance


class ReasoningRequest(BaseModel):
    """Reserved model socket input; no model implementation is introduced in Milestone 0."""

    task_id: TaskId
    goal_id: GoalId
    routine_id: RoutineId
    active_state: JsonObject = Field(default_factory=dict)
    context: JsonObject = Field(default_factory=dict)
    budget: ComputeBudget = Field(default_factory=ComputeBudget)


class ReasoningResult(BaseModel):
    """Reserved model socket output composed only of proposals."""

    task_id: TaskId
    status: Literal["complete", "continue", "blocked"]
    proposals: list[Proposal] = Field(default_factory=list)
    actions: list[ActionProposal] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    requested_additional_steps: int = Field(default=0, ge=0)
