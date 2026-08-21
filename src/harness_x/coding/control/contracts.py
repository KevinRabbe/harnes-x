"""Typed contracts for software-owned coding control state."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.core import CodingPlanId, CommitmentId, TaskId


class CodingPhase(StrEnum):
    ORIENT = "orient"
    DIAGNOSE = "diagnose"
    PLAN = "plan"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    REVIEW = "review"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class CommitmentStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SATISFIED = "satisfied"
    BLOCKED = "blocked"
    INVALIDATED = "invalidated"


class HorizonMode(StrEnum):
    EXPLORE = "explore"
    NORMAL = "normal"
    CONVERGE = "converge"
    ENDGAME = "endgame"
    CLOSEOUT = "closeout"


class ActionClass(StrEnum):
    INSPECTION = "inspection"
    MUTATION = "mutation"
    EXECUTION = "execution"
    OTHER = "other"


class InterventionKind(StrEnum):
    NONE = "none"
    CHANGE_APPROACH = "change_approach"
    FORCE_IMPLEMENTATION = "force_implementation"
    REPLAN = "replan"
    FORCE_VERIFICATION = "force_verification"
    CLOSEOUT = "closeout"


class CodingCommitment(BaseModel):
    """One durable obligation that cannot disappear by model omission."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "coding-commitment-v1"
    commitment_id: CommitmentId
    objective: str = Field(min_length=1)
    target: str | None = None
    status: CommitmentStatus = CommitmentStatus.PROPOSED
    acceptance_requirements: tuple[str, ...] = ()
    depends_on: tuple[CommitmentId, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    created_step: int = Field(ge=0)
    updated_step: int = Field(ge=0)
    attempts: int = Field(default=0, ge=0)
    last_failure: str | None = None

    @field_validator("objective")
    @classmethod
    def normalize_objective(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("commitment objective cannot be blank")
        return value

    @field_validator("target", "last_failure")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("acceptance_requirements", "evidence_refs")
    @classmethod
    def normalize_text_tuple(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("commitment text values cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("commitment text values must be unique")
        return normalized

    @field_validator("depends_on")
    @classmethod
    def unique_dependencies(
        cls, values: tuple[CommitmentId, ...]
    ) -> tuple[CommitmentId, ...]:
        serialized = tuple(str(value) for value in values)
        if len(serialized) != len(set(serialized)):
            raise ValueError("commitment dependencies must be unique")
        return values


class CodingPlan(BaseModel):
    """Versioned execution plan projected from authoritative coding control state."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "coding-plan-v1"
    plan_id: CodingPlanId
    task_id: TaskId
    revision: int = Field(default=1, ge=1)
    task: str = Field(min_length=1)
    phase: CodingPhase = CodingPhase.ORIENT
    constraints: tuple[str, ...] = ()
    strategy: tuple[str, ...] = ()
    commitments: tuple[CodingCommitment, ...] = ()
    changed_files: tuple[str, ...] = ()
    known_failures: tuple[str, ...] = ()
    updated_step: int = Field(default=0, ge=0)

    @field_validator("task")
    @classmethod
    def normalize_task(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("coding plan task cannot be blank")
        return value

    @field_validator("constraints", "strategy", "changed_files", "known_failures")
    @classmethod
    def unique_text_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("coding plan text values cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("coding plan text values must be unique")
        return normalized

    @property
    def pending_commitments(self) -> tuple[CodingCommitment, ...]:
        return tuple(
            item
            for item in self.commitments
            if item.status in {
                CommitmentStatus.PROPOSED,
                CommitmentStatus.ACTIVE,
                CommitmentStatus.BLOCKED,
            }
        )


class ProgressSnapshot(BaseModel):
    """Deterministic progress counters derived from authoritative outcomes."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "coding-progress-v1"
    total_actions: int = Field(default=0, ge=0)
    inspection_actions: int = Field(default=0, ge=0)
    mutation_actions: int = Field(default=0, ge=0)
    execution_actions: int = Field(default=0, ge=0)
    failed_actions: int = Field(default=0, ge=0)
    duplicate_actions: int = Field(default=0, ge=0)
    repeat_streak: int = Field(default=0, ge=0)
    inspection_streak: int = Field(default=0, ge=0)
    no_progress_streak: int = Field(default=0, ge=0)
    new_evidence_count: int = Field(default=0, ge=0)
    verification_attempts: int = Field(default=0, ge=0)
    verification_passes: int = Field(default=0, ge=0)
    same_failure_count: int = Field(default=0, ge=0)
    last_action_fingerprint: str | None = None
    last_failure_signature: str | None = None
    changed_files: tuple[str, ...] = ()


class HorizonSnapshot(BaseModel):
    """Externally computed remaining-horizon posture for one coding task."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "coding-horizon-v1"
    mode: HorizonMode
    pressure: float = Field(ge=0.0, le=1.0)
    reasoning_used: int = Field(ge=0)
    reasoning_limit: int = Field(ge=0)
    tool_actions_used: int = Field(ge=0)
    tool_actions_limit: int = Field(ge=0)


class ControlIntervention(BaseModel):
    """Software-owned response to stalled or horizon-inappropriate behavior."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "coding-intervention-v1"
    kind: InterventionKind = InterventionKind.NONE
    reason: str = "none"
    directive: str = ""
    preferred_actions: tuple[str, ...] = ()


class CodingControlSnapshot(BaseModel):
    """Bounded authoritative control state supplied to the reasoning core."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "coding-control-snapshot-v1"
    plan: CodingPlan
    progress: ProgressSnapshot
    horizon: HorizonSnapshot
    intervention: ControlIntervention
