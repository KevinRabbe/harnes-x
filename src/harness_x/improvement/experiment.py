"""Milestone 15 empirical experiment contracts.

These models describe isolated baseline/candidate trials and comparison evidence. They
never mutate or promote the authoritative Harness X system.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness_x.core.ids import CandidateId, SystemVersion


_STRICT_FROZEN = ConfigDict(frozen=True, extra="forbid")


class ExperimentVariant(StrEnum):
    BASELINE = "baseline"
    CANDIDATE = "candidate"


class ExperimentDisposition(StrEnum):
    PROMOTION_RECOMMENDED = "promotion_recommended"
    REJECTION_RECOMMENDED = "rejection_recommended"
    INCONCLUSIVE = "inconclusive"


class ArtifactDigest(BaseModel):
    model_config = _STRICT_FROZEN

    relative_path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)


class ExperimentRunResult(BaseModel):
    """One trusted benchmark run against one immutable sandbox snapshot."""

    model_config = _STRICT_FROZEN

    suite_name: str = Field(min_length=1)
    suite_version: str = Field(min_length=1)
    variant: ExperimentVariant
    seed: int
    source_system_version: SystemVersion
    variant_version: SystemVersion
    snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    passed: bool
    metrics: dict[str, float]
    invariants: dict[str, bool]
    reasoning_steps: int = Field(default=0, ge=0)
    tool_actions: int = Field(default=0, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)
    artifacts: tuple[ArtifactDigest, ...] = ()
    notes: tuple[str, ...] = ()


class MetricComparison(BaseModel):
    model_config = _STRICT_FROZEN

    metric: str = Field(min_length=1)
    baseline_mean: float
    candidate_mean: float
    delta: float
    baseline_variance: float = Field(ge=0.0)
    candidate_variance: float = Field(ge=0.0)
    expected_delta: float | None = None
    minimum_acceptable_delta: float | None = None
    target_met: bool | None = None


class ResourceComparison(BaseModel):
    model_config = _STRICT_FROZEN

    baseline_reasoning_steps: int = Field(ge=0)
    candidate_reasoning_steps: int = Field(ge=0)
    reasoning_step_delta: int
    baseline_tool_actions: int = Field(ge=0)
    candidate_tool_actions: int = Field(ge=0)
    tool_action_delta: int
    baseline_wall_time_seconds: float = Field(ge=0.0)
    candidate_wall_time_seconds: float = Field(ge=0.0)
    wall_time_delta_seconds: float


class SandboxExperimentReport(BaseModel):
    """Empirical evidence produced by Milestone 15.

    `disposition` is a recommendation only. No field in this report authorizes a live
    system mutation or candidate promotion.
    """

    model_config = _STRICT_FROZEN

    schema_version: str = "improvement-sandbox-report-v1"
    candidate_id: CandidateId
    proposal_fingerprint: str = Field(min_length=64, max_length=64)
    baseline_version: SystemVersion
    baseline_snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    candidate_snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    suite_name: str = Field(min_length=1)
    suite_version: str = Field(min_length=1)
    seeds: tuple[int, ...] = Field(min_length=1)
    baseline_runs: tuple[ExperimentRunResult, ...] = Field(min_length=1)
    candidate_runs: tuple[ExperimentRunResult, ...] = Field(min_length=1)
    metric_comparisons: tuple[MetricComparison, ...]
    resource_comparison: ResourceComparison
    new_failure_modes: tuple[str, ...] = ()
    regressions: tuple[str, ...] = ()
    budget_violations: tuple[str, ...] = ()
    baseline_untouched: bool
    teardown_verified: bool
    experiment_valid: bool
    disposition: ExperimentDisposition
    reasons: tuple[str, ...]
    evidence_directory: str

    @model_validator(mode="after")
    def validate_pairing(self) -> "SandboxExperimentReport":
        if len(self.baseline_runs) != len(self.candidate_runs):
            raise ValueError("baseline and candidate run counts must match")
        if len(self.seeds) != len(self.baseline_runs):
            raise ValueError("seed count must match paired run count")
        for seed, baseline, candidate in zip(
            self.seeds, self.baseline_runs, self.candidate_runs, strict=True
        ):
            if baseline.seed != seed or candidate.seed != seed:
                raise ValueError("paired runs must use identical declared seeds")
            if baseline.variant != ExperimentVariant.BASELINE:
                raise ValueError("baseline_runs contains a non-baseline result")
            if candidate.variant != ExperimentVariant.CANDIDATE:
                raise ValueError("candidate_runs contains a non-candidate result")
            if baseline.suite_name != self.suite_name or candidate.suite_name != self.suite_name:
                raise ValueError("run suite name does not match report")
            if baseline.suite_version != self.suite_version or candidate.suite_version != self.suite_version:
                raise ValueError("run suite version does not match report")
        if self.experiment_valid and self.disposition == ExperimentDisposition.INCONCLUSIVE:
            raise ValueError("valid experiment cannot be marked inconclusive")
        if not self.experiment_valid and self.disposition != ExperimentDisposition.INCONCLUSIVE:
            raise ValueError("invalid experiment must be inconclusive")
        return self
