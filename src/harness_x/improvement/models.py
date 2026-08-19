"""First-class bounded improvement-candidate contracts.

Milestone 14 introduced immutable proposals and static sandbox qualification. Milestone
16 extends the lifecycle with evidence-backed promotion while keeping live application
inside a separate promotion authority.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from harness_x.core.ids import CandidateId, SystemVersion


_STRICT_FROZEN = ConfigDict(frozen=True, extra="forbid")


class CandidateCreator(StrEnum):
    HUMAN = "human"
    SYSTEM = "system"


class ImprovementChangeType(StrEnum):
    CONFIG_THRESHOLD = "config_threshold"
    RETRIEVAL_SCORING_POLICY = "retrieval_scoring_policy"
    ROUTINE_ORDERING = "routine_ordering"
    CONTEXT_BUILDER_POLICY = "context_builder_policy"
    VERIFICATION_FREQUENCY = "verification_frequency"
    MEMORY_RETENTION_COMPACTION = "memory_retention_compaction"
    TOOL = "tool"
    CODE = "code"
    ADAPTER = "adapter"


class CandidateRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CandidateStatus(StrEnum):
    PROPOSED = "proposed"
    SANDBOX_ELIGIBLE = "sandbox_eligible"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


class ChangeOperation(StrEnum):
    SET = "set"
    REORDER = "reorder"
    REPLACE_POLICY = "replace_policy"


class ChangePatch(BaseModel):
    """A declarative JSON change; never executable source code."""

    model_config = _STRICT_FROZEN

    path: str = Field(min_length=1)
    operation: ChangeOperation
    before: JsonValue
    after: JsonValue

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("patch path cannot be blank")
        lowered = value.lower()
        if value.startswith(("/", "\\")) or ".." in value.split("."):
            raise ValueError("patch path must be a logical Harness X namespace")
        if any(token in lowered for token in ("src/", "src\\", ".py", "__code__")):
            raise ValueError("source-code paths are not valid bounded patches")
        return value

    @model_validator(mode="after")
    def changed_value(self) -> "ChangePatch":
        if canonical_json(self.before) == canonical_json(self.after):
            raise ValueError("patch before/after values must differ")
        if self.operation == ChangeOperation.REORDER:
            if not isinstance(self.before, list) or not isinstance(self.after, list):
                raise ValueError("reorder patches require list before/after values")
            if sorted(map(canonical_json, self.before)) != sorted(map(canonical_json, self.after)):
                raise ValueError("reorder patches may only reorder existing members")
        return self


class MetricPrediction(BaseModel):
    model_config = _STRICT_FROZEN

    metric: str = Field(min_length=1)
    expected_delta: float
    minimum_acceptable_delta: float | None = None
    rationale: str = Field(min_length=1)


class ImprovementHypothesis(BaseModel):
    model_config = _STRICT_FROZEN

    statement: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)
    falsification_condition: str = Field(min_length=1)


class ImprovementResourceBudget(BaseModel):
    model_config = _STRICT_FROZEN

    benchmark_runs: int = Field(default=3, ge=1)
    max_wall_time_seconds: int = Field(default=900, ge=1)
    max_reasoning_steps: int = Field(default=1000, ge=0)
    max_tool_actions: int = Field(default=100, ge=0)


class RollbackPlan(BaseModel):
    model_config = _STRICT_FROZEN

    strategy: str = Field(min_length=1)
    restore_baseline_version: SystemVersion
    verification_tests: tuple[str, ...] = Field(min_length=1)
    automatic: bool = True


class ImprovementProposal(BaseModel):
    """Immutable proposal definition whose fingerprint never changes with status."""

    model_config = _STRICT_FROZEN

    created_by: CandidateCreator
    creator_id: str = Field(min_length=1)
    baseline_version: SystemVersion
    change_type: ImprovementChangeType
    scope: tuple[str, ...] = Field(min_length=1, max_length=10)
    patches: tuple[ChangePatch, ...] = Field(min_length=1, max_length=10)
    hypothesis: ImprovementHypothesis
    predicted_metrics: tuple[MetricPrediction, ...] = Field(min_length=1, max_length=12)
    required_tests: tuple[str, ...] = Field(min_length=1, max_length=20)
    resource_budget: ImprovementResourceBudget
    risk_level: CandidateRiskLevel
    rollback: RollbackPlan
    supersedes: CandidateId | None = None
    evidence_refs: tuple[str, ...] = ()

    @field_validator("creator_id")
    @classmethod
    def normalize_creator(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("creator_id cannot be blank")
        return value

    @field_validator("scope", "required_tests", "evidence_refs")
    @classmethod
    def normalize_strings(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values if value.strip())
        if not normalized and values:
            raise ValueError("collection cannot contain only blank values")
        if len(normalized) != len(set(normalized)):
            raise ValueError("collection cannot contain duplicates")
        return normalized

    @model_validator(mode="after")
    def scope_covers_patches(self) -> "ImprovementProposal":
        if not self.scope or not self.required_tests:
            raise ValueError("scope and required tests cannot be empty")
        if any(
            not any(patch.path.startswith(scope) for scope in self.scope)
            for patch in self.patches
        ):
            raise ValueError("every patch must fall inside declared scope")
        metrics = [item.metric for item in self.predicted_metrics]
        if len(metrics) != len(set(metrics)):
            raise ValueError("predicted metric names must be unique")
        return self

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class CandidateQualification(BaseModel):
    model_config = _STRICT_FROZEN

    eligible: bool
    reasons: tuple[str, ...]
    policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def reasons_match_result(self) -> "CandidateQualification":
        if self.eligible and self.reasons:
            raise ValueError("eligible qualification cannot contain rejection reasons")
        if not self.eligible and not self.reasons:
            raise ValueError("ineligible qualification requires at least one reason")
        return self


class ImprovementCandidate(BaseModel):
    """Immutable revision of one improvement candidate."""

    model_config = _STRICT_FROZEN

    schema_version: str = "improvement-candidate-v1"
    candidate_id: CandidateId
    revision: int = Field(ge=1)
    proposal: ImprovementProposal
    proposal_fingerprint: str = Field(min_length=64, max_length=64)
    status: CandidateStatus = CandidateStatus.PROPOSED
    qualification: CandidateQualification | None = None
    status_reason: str | None = None
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_revision(self) -> "ImprovementCandidate":
        if self.proposal_fingerprint != self.proposal.fingerprint:
            raise ValueError("proposal fingerprint does not match proposal content")
        if self.status == CandidateStatus.PROPOSED and self.qualification is not None:
            raise ValueError("proposed candidate cannot already have qualification")
        if self.status in {CandidateStatus.SANDBOX_ELIGIBLE, CandidateStatus.PROMOTED}:
            if self.qualification is None or not self.qualification.eligible:
                raise ValueError(
                    "sandbox-eligible/promoted candidate requires positive qualification"
                )
        if self.status == CandidateStatus.PROMOTED and not self.evidence_refs:
            raise ValueError("promoted candidate requires empirical promotion evidence")
        if self.status == CandidateStatus.REJECTED:
            if self.qualification is None or self.qualification.eligible:
                raise ValueError("rejected candidate requires failed qualification")
        if self.status == CandidateStatus.INVALIDATED and not self.evidence_refs:
            raise ValueError("invalidated candidate requires evidence references")
        return self


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
