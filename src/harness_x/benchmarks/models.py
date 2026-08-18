"""Structured outputs for scripted autonomy benchmarks."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ScenarioMetrics(BaseModel):
    """Trace-derived metrics plus scenario-specific correctness."""

    model_config = ConfigDict(frozen=True)

    scenario: str
    passed: bool
    goal_retained: bool
    state_correct: bool
    illegal_transitions: int = Field(ge=0)
    recoveries: int = Field(ge=0)
    maintenance_cycles: int = Field(ge=0)
    suspensions: int = Field(ge=0)
    checkpoints: int = Field(ge=0)
    max_working_pressure: float = Field(ge=0.0)
    retrieval_attempts: int = Field(ge=0)
    useful_retrievals: int = Field(ge=0)
    retrieval_usefulness: float = Field(ge=0.0, le=1.0)
    action_count: int = Field(ge=0)
    verification_failures: int = Field(ge=0)
    working_evictions: int = Field(ge=0)
    trace_events: int = Field(ge=0)
    authoritative_transitions: int = Field(ge=0)
    trace_complete: bool
    replay_valid: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    notes: tuple[str, ...] = ()


class BenchmarkReport(BaseModel):
    """Aggregate Milestone 8 benchmark result."""

    model_config = ConfigDict(frozen=True)

    suite_version: str = "scripted-autonomy-v1"
    passed: bool
    total_events: int = Field(ge=0)
    total_authoritative_transitions: int = Field(ge=0)
    total_actions: int = Field(ge=0)
    total_recoveries: int = Field(ge=0)
    scenarios: tuple[ScenarioMetrics, ...]
