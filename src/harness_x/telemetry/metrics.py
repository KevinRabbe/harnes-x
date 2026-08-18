"""Grounded rolling metrics derived from causal traces and authoritative state."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from harness_x.core.events import EventType, TraceEvent
from harness_x.memory import ErrorBuffer, ErrorStatus, SemanticMemory, WorkingState
from harness_x.orchestrator import OperatingMode


class RuntimeMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    trace_events: int = Field(ge=0)
    working_pressure: float = Field(ge=0.0, le=1.0)
    retrieval_attempts: int = Field(ge=0)
    useful_retrievals: int = Field(ge=0)
    retrieval_usefulness: float = Field(ge=0.0, le=1.0)
    routine_attempts: int = Field(ge=0)
    routine_successes: int = Field(ge=0)
    routine_success_rate: float = Field(ge=0.0, le=1.0)
    recovery_entries: int = Field(ge=0)
    recovery_successes: int = Field(ge=0)
    recovery_success_rate: float = Field(ge=0.0, le=1.0)
    verifier_checks: int = Field(ge=0)
    verifier_rejections: int = Field(ge=0)
    verifier_rejection_rate: float = Field(ge=0.0, le=1.0)
    unresolved_errors: int = Field(ge=0)
    oldest_unresolved_error_step_age: int = Field(ge=0)
    semantic_contradictions: int = Field(ge=0)
    maintenance_entries: int = Field(ge=0)
    tool_actions: int = Field(ge=0)


def _ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def derive_runtime_metrics(
    events: Iterable[TraceEvent],
    *,
    working: WorkingState,
    errors: ErrorBuffer,
    semantic: SemanticMemory,
) -> RuntimeMetrics:
    """Derive metrics without emitting new trace events or mutating owners."""
    materialized = list(events)
    retrievals = [
        event
        for event in materialized
        if event.event_type == EventType.MEMORY_RETRIEVED
        and event.component == "memory.episodic"
    ]
    useful_retrievals = sum(
        int(event.metadata.get("result_count", 0)) > 0 for event in retrievals
    )
    routine_finishes = [
        event for event in materialized if event.event_type == EventType.ROUTINE_FINISHED
    ]
    routine_successes = sum(
        event.metadata.get("status") == "succeeded" for event in routine_finishes
    )
    recovery_entries = sum(
        event.event_type == EventType.MODE_CHANGED
        and event.metadata.get("to") == OperatingMode.RECOVERY.value
        for event in materialized
    )
    recovery_successes = sum(
        event.event_type == EventType.MODE_CHANGED
        and event.metadata.get("from") == OperatingMode.RECOVERY.value
        and event.metadata.get("to") == OperatingMode.TASK_ACTIVE.value
        for event in materialized
    )
    verifier_events = [
        event
        for event in materialized
        if event.event_type == EventType.VERIFICATION_COMPLETED
    ]
    verifier_rejections = sum(
        event.metadata.get("accepted") is False for event in verifier_events
    )
    unresolved = tuple(
        record
        for record in errors.all()
        if record.status in {ErrorStatus.OPEN, ErrorStatus.INVESTIGATING}
    )
    current_step = materialized[-1].step if materialized else 0
    source_steps = {str(event.event_id): event.step for event in materialized}
    ages = [
        max(0, current_step - source_steps.get(str(record.source_event_id), current_step))
        for record in unresolved
    ]
    maintenance_entries = sum(
        event.event_type == EventType.MODE_CHANGED
        and event.metadata.get("to") == OperatingMode.MAINTENANCE.value
        for event in materialized
    )
    tool_actions = sum(
        event.event_type == EventType.ACTION_EXECUTED for event in materialized
    )
    contradiction_count = len(semantic.contradictions())
    return RuntimeMetrics(
        trace_events=len(materialized),
        working_pressure=min(1.0, working.pressure.pressure),
        retrieval_attempts=len(retrievals),
        useful_retrievals=useful_retrievals,
        retrieval_usefulness=_ratio(useful_retrievals, len(retrievals), empty=1.0),
        routine_attempts=len(routine_finishes),
        routine_successes=routine_successes,
        routine_success_rate=_ratio(routine_successes, len(routine_finishes), empty=1.0),
        recovery_entries=recovery_entries,
        recovery_successes=recovery_successes,
        recovery_success_rate=_ratio(recovery_successes, recovery_entries, empty=1.0),
        verifier_checks=len(verifier_events),
        verifier_rejections=verifier_rejections,
        verifier_rejection_rate=_ratio(verifier_rejections, len(verifier_events)),
        unresolved_errors=len(unresolved),
        oldest_unresolved_error_step_age=max(ages, default=0),
        semantic_contradictions=contradiction_count,
        maintenance_entries=maintenance_entries,
        tool_actions=tool_actions,
    )


class MetricsSample(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "metrics-sample-v1"
    system_version: str
    task_id: str
    trace_id: str
    step: int = Field(ge=0)
    timestamp: str
    metrics: RuntimeMetrics


class JsonlMetricsStore:
    """Append-only operator-visible samples; derived data never replaces causal traces."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, sample: MetricsSample) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(sample.model_dump_json() + "\n")

    def samples(self) -> tuple[MetricsSample, ...]:
        if not self.path.exists():
            return ()
        result: list[MetricsSample] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                result.append(MetricsSample.model_validate_json(line))
        return tuple(result)

    def latest(self) -> MetricsSample | None:
        items = self.samples()
        return items[-1] if items else None
