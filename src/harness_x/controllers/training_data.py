"""Grounded Milestone 17 training records for learned peripheral controllers.

The collector is deliberately trace-derived and side-effect free. Deterministic gate
outputs remain the supervised baseline. Outcome/usefulness labels are added only when
later causal trace evidence supports them; missing counterfactual evidence remains
explicitly UNKNOWN rather than being converted into a synthetic reward.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from harness_x.core.events import EventType, TraceEvent
from harness_x.core.ids import EventId, SystemVersion, TaskId, TraceId
from harness_x.telemetry import TraceStore


COLLECTOR_VERSION = "gate-training-data-v1"
USEFULNESS_POLICY_VERSION = "gate-usefulness-v1"
_STRICT_FROZEN = ConfigDict(frozen=True, extra="forbid")


class GateTrainingDataError(ValueError):
    """Raised when trace-derived controller data cannot be trusted."""


class UsefulnessState(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"


class GateModelRecommendation(BaseModel):
    """Optional shadow recommendation explicitly bound to one gate event."""

    model_config = _STRICT_FROZEN

    schema_version: str = "gate-model-recommendation-v1"
    decision_event_id: EventId
    gate_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    model_version: str | None = None
    recommendation: dict[str, JsonValue]
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class GateOutcomeSummary(BaseModel):
    """Measured trajectory facts after one deterministic gate decision."""

    model_config = _STRICT_FROZEN

    start_step: int = Field(ge=1)
    end_step: int = Field(ge=1)
    subsequent_event_count: int = Field(ge=0)
    mode_changes: tuple[str, ...] = ()
    reasoning_completions: int = Field(default=0, ge=0)
    tool_actions: int = Field(default=0, ge=0)
    verification_accepts: int = Field(default=0, ge=0)
    verification_rejections: int = Field(default=0, ge=0)
    errors_recorded: int = Field(default=0, ge=0)
    memory_writes: int = Field(default=0, ge=0)
    memory_evictions: int = Field(default=0, ge=0)
    retrieval_attempts: int = Field(default=0, ge=0)
    useful_retrievals: int = Field(default=0, ge=0)
    routine_successes: int = Field(default=0, ge=0)
    routine_failures: int = Field(default=0, ge=0)
    followup_gate_event_id: EventId | None = None
    followup_gate_input_state: dict[str, JsonValue] | None = None
    evidence_event_ids: tuple[EventId, ...] = ()

    @model_validator(mode="after")
    def ordered_window(self) -> "GateOutcomeSummary":
        if self.end_step < self.start_step:
            raise ValueError("gate outcome end_step cannot precede start_step")
        return self


class GateUsefulnessLabel(BaseModel):
    """Evidence-backed usefulness label; UNKNOWN means no causal claim is made."""

    model_config = _STRICT_FROZEN

    state: UsefulnessState
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    evidence_event_ids: tuple[EventId, ...] = ()
    policy_version: str = USEFULNESS_POLICY_VERSION

    @model_validator(mode="after")
    def score_matches_state(self) -> "GateUsefulnessLabel":
        if self.state == UsefulnessState.UNKNOWN and self.score is not None:
            raise ValueError("unknown usefulness cannot carry a numeric score")
        if self.state != UsefulnessState.UNKNOWN and self.score is None:
            raise ValueError("known usefulness requires a numeric score")
        if self.state == UsefulnessState.UNKNOWN and self.evidence_event_ids:
            raise ValueError("unknown usefulness cannot claim supporting evidence")
        return self


class GateTrainingRecord(BaseModel):
    """One immutable gate decision joined with observed downstream evidence."""

    model_config = _STRICT_FROZEN

    schema_version: str = "gate-training-record-v1"
    collector_version: str = COLLECTOR_VERSION
    record_id: str = Field(pattern=r"^gate_record_[0-9a-f]{32}$")
    record_fingerprint: str = Field(min_length=64, max_length=64)
    source_trace_fingerprint: str = Field(min_length=64, max_length=64)
    system_version: SystemVersion
    trace_id: TraceId
    task_id: TaskId
    decision_event_id: EventId
    decision_step: int = Field(ge=1)
    gate_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    input_fingerprint: str = Field(min_length=64, max_length=64)
    state_features: dict[str, JsonValue]
    policy_decision: dict[str, JsonValue]
    policy_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    immediate_cost: float | None = Field(default=None, ge=0.0)
    model_recommendation: GateModelRecommendation | None = None
    actual_outcome: GateOutcomeSummary
    later_usefulness: GateUsefulnessLabel

    @model_validator(mode="after")
    def validate_identity(self) -> "GateTrainingRecord":
        if self.model_recommendation is not None:
            if self.model_recommendation.decision_event_id != self.decision_event_id:
                raise ValueError("model recommendation is bound to another gate event")
            if self.model_recommendation.gate_id != self.gate_id:
                raise ValueError("model recommendation gate_id mismatch")
        expected = _fingerprint(
            self.model_dump(mode="json", exclude={"record_fingerprint"})
        )
        if expected != self.record_fingerprint:
            raise ValueError("gate training record fingerprint mismatch")
        return self


class SourceTraceDescriptor(BaseModel):
    model_config = _STRICT_FROZEN

    trace_id: TraceId
    system_version: SystemVersion
    event_count: int = Field(ge=1)
    trace_fingerprint: str = Field(min_length=64, max_length=64)


class GateTrainingDatasetManifest(BaseModel):
    model_config = _STRICT_FROZEN

    schema_version: str = "gate-training-dataset-manifest-v1"
    collector_version: str = COLLECTOR_VERSION
    usefulness_policy_version: str = USEFULNESS_POLICY_VERSION
    outcome_horizon_steps: int = Field(ge=1)
    record_count: int = Field(ge=0)
    source_traces: tuple[SourceTraceDescriptor, ...]
    records_by_gate: dict[str, int]
    usefulness_counts: dict[str, int]
    model_recommendation_count: int = Field(ge=0)
    dataset_fingerprint: str = Field(min_length=64, max_length=64)


class GateTrainingDataset(BaseModel):
    model_config = _STRICT_FROZEN

    manifest: GateTrainingDatasetManifest
    records: tuple[GateTrainingRecord, ...]

    @model_validator(mode="after")
    def validate_manifest(self) -> "GateTrainingDataset":
        _validate_manifest(self.manifest, self.records)
        return self

    def write(self, output_directory: str | Path) -> None:
        root = Path(output_directory)
        if root.exists() and any(root.iterdir()):
            raise GateTrainingDataError("gate training-data output directory must be empty")
        root.mkdir(parents=True, exist_ok=True)
        records_path = root / "records.jsonl"
        with records_path.open("w", encoding="utf-8", newline="\n") as handle:
            for record in self.records:
                handle.write(record.model_dump_json() + "\n")
        (root / "manifest.json").write_text(
            self.manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _trace_fingerprint(events: Sequence[TraceEvent]) -> str:
    return _fingerprint([event.model_dump(mode="json") for event in events])


def _json_dict(value: object, *, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise GateTrainingDataError(f"gate event metadata.{field} must be an object")
    # Event metadata is JSON-serializable by contract. Round-tripping gives Pydantic's
    # JsonValue type a precise, immutable input surface and rejects Python-only objects.
    try:
        result = json.loads(_canonical(value))
    except (TypeError, ValueError) as exc:
        raise GateTrainingDataError(
            f"gate event metadata.{field} is not JSON-compatible"
        ) from exc
    if not isinstance(result, dict):
        raise GateTrainingDataError(f"gate event metadata.{field} must remain an object")
    return result


def _gate_metadata(event: TraceEvent) -> tuple[str, str, str, dict[str, JsonValue], dict[str, JsonValue], float | None, float | None]:
    if event.event_type != EventType.GATE_DECISION:
        raise GateTrainingDataError("training record source is not a gate decision")
    metadata = event.metadata
    gate_id = metadata.get("gate_id")
    policy_version = metadata.get("policy_version")
    input_fingerprint = metadata.get("input_fingerprint")
    if not isinstance(gate_id, str) or not gate_id.strip():
        raise GateTrainingDataError("gate decision lacks gate_id")
    if event.component != f"gate.{gate_id}":
        raise GateTrainingDataError("gate decision component/gate_id mismatch")
    if not isinstance(policy_version, str) or not policy_version.strip():
        raise GateTrainingDataError("gate decision lacks policy_version")
    if not isinstance(input_fingerprint, str) or len(input_fingerprint) != 64:
        raise GateTrainingDataError("gate decision lacks a SHA-256 input fingerprint")
    state = _json_dict(metadata.get("input_state"), field="input_state")
    decision = _json_dict(metadata.get("decision"), field="decision")
    confidence = metadata.get("confidence")
    cost = metadata.get("cost")
    if confidence is not None and not isinstance(confidence, (int, float)):
        raise GateTrainingDataError("gate decision confidence must be numeric or null")
    if cost is not None and not isinstance(cost, (int, float)):
        raise GateTrainingDataError("gate decision cost must be numeric or null")
    return (
        gate_id,
        policy_version,
        input_fingerprint,
        state,
        decision,
        float(confidence) if confidence is not None else None,
        float(cost) if cost is not None else None,
    )


def _summarize_outcome(
    decision: TraceEvent,
    window: Sequence[TraceEvent],
    followup_gate: TraceEvent | None,
) -> GateOutcomeSummary:
    mode_changes = tuple(
        str(event.metadata.get("to"))
        for event in window
        if event.event_type == EventType.MODE_CHANGED and event.metadata.get("to") is not None
    )
    verification_events = [
        event for event in window if event.event_type == EventType.VERIFICATION_COMPLETED
    ]
    retrievals = [
        event for event in window if event.event_type == EventType.MEMORY_RETRIEVED
    ]
    useful_retrievals = sum(
        int(event.metadata.get("result_count", 0)) > 0 for event in retrievals
    )
    routine_finishes = [
        event for event in window if event.event_type == EventType.ROUTINE_FINISHED
    ]
    measured_types = {
        EventType.MODE_CHANGED,
        EventType.REASONING_COMPLETED,
        EventType.ACTION_EXECUTED,
        EventType.VERIFICATION_COMPLETED,
        EventType.ERROR_RECORDED,
        EventType.MEMORY_WRITTEN,
        EventType.MEMORY_EVICTED,
        EventType.MEMORY_RETRIEVED,
        EventType.ROUTINE_FINISHED,
    }
    evidence = tuple(
        event.event_id for event in window if event.event_type in measured_types
    )
    followup_input: dict[str, JsonValue] | None = None
    if followup_gate is not None:
        followup_input = _json_dict(
            followup_gate.metadata.get("input_state"), field="input_state"
        )
    end_step = window[-1].step if window else decision.step
    return GateOutcomeSummary(
        start_step=decision.step + 1,
        end_step=end_step,
        subsequent_event_count=len(window),
        mode_changes=mode_changes,
        reasoning_completions=sum(
            event.event_type == EventType.REASONING_COMPLETED for event in window
        ),
        tool_actions=sum(event.event_type == EventType.ACTION_EXECUTED for event in window),
        verification_accepts=sum(
            event.metadata.get("accepted") is True for event in verification_events
        ),
        verification_rejections=sum(
            event.metadata.get("accepted") is False for event in verification_events
        ),
        errors_recorded=sum(event.event_type == EventType.ERROR_RECORDED for event in window),
        memory_writes=sum(event.event_type == EventType.MEMORY_WRITTEN for event in window),
        memory_evictions=sum(event.event_type == EventType.MEMORY_EVICTED for event in window),
        retrieval_attempts=len(retrievals),
        useful_retrievals=useful_retrievals,
        routine_successes=sum(
            event.metadata.get("status") == "succeeded" for event in routine_finishes
        ),
        routine_failures=sum(
            event.metadata.get("status") in {"failed", "blocked"} for event in routine_finishes
        ),
        followup_gate_event_id=followup_gate.event_id if followup_gate is not None else None,
        followup_gate_input_state=followup_input,
        evidence_event_ids=evidence,
    )


def _unknown(rationale: str) -> GateUsefulnessLabel:
    return GateUsefulnessLabel(
        state=UsefulnessState.UNKNOWN,
        rationale=rationale,
        confidence=1.0,
    )


def _positive(
    score: float,
    rationale: str,
    evidence: Iterable[TraceEvent],
    *,
    confidence: float = 1.0,
) -> GateUsefulnessLabel:
    return GateUsefulnessLabel(
        state=UsefulnessState.POSITIVE,
        score=score,
        confidence=confidence,
        rationale=rationale,
        evidence_event_ids=tuple(event.event_id for event in evidence),
    )


def _negative(
    rationale: str,
    evidence: Iterable[TraceEvent],
    *,
    confidence: float = 1.0,
) -> GateUsefulnessLabel:
    return GateUsefulnessLabel(
        state=UsefulnessState.NEGATIVE,
        score=0.0,
        confidence=confidence,
        rationale=rationale,
        evidence_event_ids=tuple(event.event_id for event in evidence),
    )


def _label_usefulness(
    gate_id: str,
    state: Mapping[str, JsonValue],
    decision: Mapping[str, JsonValue],
    window: Sequence[TraceEvent],
    followup_gate: TraceEvent | None,
) -> GateUsefulnessLabel:
    """Derive conservative observational labels from direct trace evidence.

    UNKNOWN is intentional. These labels describe observed usefulness, not an
    unobservable counterfactual claim that the deterministic action was optimal.
    """

    if gate_id == "retrieval":
        if decision.get("retrieve") is not True:
            return _unknown("retrieval_not_requested_counterfactual_unknown")
        retrievals = [
            event
            for event in window
            if event.event_type == EventType.MEMORY_RETRIEVED
            and event.component == "memory.episodic"
        ]
        if not retrievals:
            return _unknown("retrieval_execution_not_observed")
        useful = [event for event in retrievals if int(event.metadata.get("result_count", 0)) > 0]
        if useful:
            return _positive(
                len(useful) / len(retrievals),
                "retrieval_returned_evidence",
                useful,
            )
        return _negative("retrieval_returned_zero_results", retrievals)

    if gate_id == "write":
        if decision.get("write") is not True:
            return _unknown("write_not_requested_counterfactual_unknown")
        target = decision.get("memory_class")
        writes = [
            event
            for event in window
            if event.event_type == EventType.MEMORY_WRITTEN
            and event.metadata.get("memory_class") == target
        ]
        if not writes:
            return _unknown("write_execution_not_observed")
        written_ids = {ref for event in writes for ref in event.output_refs if ref.startswith("mem_")}
        reused = [
            event
            for event in window
            if event.event_type == EventType.MEMORY_RETRIEVED
            and any(ref in written_ids for ref in event.input_refs)
        ]
        if reused:
            return _positive(1.0, "written_memory_later_retrieved", reused, confidence=0.95)
        return _unknown("write_observed_but_later_reuse_not_observed")

    if gate_id == "focus":
        focused = {
            str(item) for item in decision.get("focus_order", []) if isinstance(item, str)
        }
        if not focused:
            return _unknown("focus_selection_empty")
        reused_events = [
            event
            for event in window
            if event.event_type != EventType.MEMORY_EVICTED
            and any(ref in focused for ref in event.input_refs)
        ]
        reused_ids = {
            ref for event in reused_events for ref in event.input_refs if ref in focused
        }
        if reused_ids:
            return _positive(
                len(reused_ids) / len(focused),
                "focused_state_reused_downstream",
                reused_events,
                confidence=0.9,
            )
        return _unknown("focused_state_later_use_not_observed")

    if gate_id == "compute":
        action = decision.get("action")
        if action == "allow":
            useful = [
                event
                for event in window
                if event.event_type in {EventType.REASONING_COMPLETED, EventType.ACTION_EXECUTED}
                or (
                    event.event_type == EventType.ROUTINE_FINISHED
                    and event.metadata.get("status") == "succeeded"
                )
                or (
                    event.event_type == EventType.VERIFICATION_COMPLETED
                    and event.metadata.get("accepted") is True
                )
            ]
            if useful:
                return _positive(
                    1.0,
                    "allowed_compute_produced_downstream_progress",
                    useful,
                    confidence=0.85,
                )
            return _unknown("allowed_compute_progress_not_observed_before_boundary")
        if action == "suspend":
            suspended = [
                event
                for event in window
                if event.event_type == EventType.MODE_CHANGED
                and event.metadata.get("to") == "suspended"
            ]
            if suspended:
                return _positive(1.0, "compute_suspend_was_enforced", suspended)
            return _unknown("compute_suspend_effect_not_observed")
        if action == "stop" and state.get("completion_condition_met") is True:
            completed = [
                event
                for event in window
                if event.event_type == EventType.MODE_CHANGED
                and event.metadata.get("to") == "complete"
            ]
            if completed:
                return _positive(1.0, "completion_stop_reached_complete_mode", completed)
        return _unknown("compute_stop_later_usefulness_unknown")

    if gate_id == "maintenance":
        if decision.get("trigger") is not True:
            return _unknown("maintenance_not_triggered_counterfactual_unknown")
        entered = [
            event
            for event in window
            if event.event_type == EventType.MODE_CHANGED
            and event.metadata.get("to") == "maintenance"
        ]
        evictions = [
            event for event in window if event.event_type == EventType.MEMORY_EVICTED
        ]
        pressure_improved = False
        followup_evidence: list[TraceEvent] = []
        if followup_gate is not None:
            followup_state = followup_gate.metadata.get("input_state")
            before_pressure = state.get("working_pressure")
            after_pressure = (
                followup_state.get("working_pressure")
                if isinstance(followup_state, dict)
                else None
            )
            if isinstance(before_pressure, (int, float)) and isinstance(after_pressure, (int, float)):
                pressure_improved = float(after_pressure) < float(before_pressure)
                if pressure_improved:
                    followup_evidence.append(followup_gate)
        evidence = [*entered, *evictions, *followup_evidence]
        if entered and (evictions or pressure_improved):
            return _positive(
                1.0,
                "maintenance_trigger_produced_observed_relief",
                evidence,
                confidence=0.95,
            )
        return _unknown("maintenance_effect_not_directly_observed")

    return _unknown("no_usefulness_policy_for_gate")


def _build_record(
    event: TraceEvent,
    *,
    trace_fingerprint: str,
    window: Sequence[TraceEvent],
    followup_gate: TraceEvent | None,
    recommendation: GateModelRecommendation | None,
) -> GateTrainingRecord:
    (
        gate_id,
        policy_version,
        input_fingerprint,
        state,
        decision,
        confidence,
        cost,
    ) = _gate_metadata(event)
    outcome = _summarize_outcome(event, window, followup_gate)
    usefulness = _label_usefulness(gate_id, state, decision, window, followup_gate)
    record_id = f"gate_record_{hashlib.sha256((trace_fingerprint + str(event.event_id)).encode('utf-8')).hexdigest()[:32]}"
    payload = {
        "schema_version": "gate-training-record-v1",
        "collector_version": COLLECTOR_VERSION,
        "record_id": record_id,
        "source_trace_fingerprint": trace_fingerprint,
        "system_version": event.system_version.model_dump(mode="json"),
        "trace_id": event.trace_id.model_dump(mode="json"),
        "task_id": event.task_id.model_dump(mode="json"),
        "decision_event_id": event.event_id.model_dump(mode="json"),
        "decision_step": event.step,
        "gate_id": gate_id,
        "policy_version": policy_version,
        "input_fingerprint": input_fingerprint,
        "state_features": state,
        "policy_decision": decision,
        "policy_confidence": confidence,
        "immediate_cost": cost,
        "model_recommendation": (
            recommendation.model_dump(mode="json") if recommendation is not None else None
        ),
        "actual_outcome": outcome.model_dump(mode="json"),
        "later_usefulness": usefulness.model_dump(mode="json"),
    }
    payload["record_fingerprint"] = _fingerprint(payload)
    return GateTrainingRecord.model_validate(payload)


class GateTrainingDataCollector:
    """Join deterministic gate events with bounded downstream trace evidence."""

    def __init__(self, *, outcome_horizon_steps: int = 32) -> None:
        if outcome_horizon_steps < 1:
            raise ValueError("outcome_horizon_steps must be positive")
        self.outcome_horizon_steps = outcome_horizon_steps

    def collect_trace(
        self,
        events: Sequence[TraceEvent],
        *,
        recommendations: Mapping[str, GateModelRecommendation] | None = None,
    ) -> tuple[GateTrainingRecord, ...]:
        if not events:
            raise GateTrainingDataError("cannot collect gate data from an empty trace")
        ordered = sorted(events, key=lambda event: event.step)
        trace_ids = {str(event.trace_id) for event in ordered}
        if len(trace_ids) != 1:
            raise GateTrainingDataError("collect_trace requires exactly one trace")
        versions = {str(event.system_version) for event in ordered}
        if len(versions) != 1:
            raise GateTrainingDataError("one trace cannot mix whole-system versions")
        for expected, event in enumerate(ordered, start=1):
            if event.step != expected:
                raise GateTrainingDataError("gate training source trace steps are not contiguous")

        trace_fp = _trace_fingerprint(ordered)
        gate_events = [event for event in ordered if event.event_type == EventType.GATE_DECISION]
        recommendation_map = dict(recommendations or {})
        known_gate_events = {str(event.event_id): event for event in gate_events}
        orphaned = sorted(set(recommendation_map) - set(known_gate_events))
        if orphaned:
            raise GateTrainingDataError("model recommendation references a non-gate event")

        records: list[GateTrainingRecord] = []
        for event in gate_events:
            gate_id, *_ = _gate_metadata(event)
            next_same = next(
                (
                    candidate
                    for candidate in gate_events
                    if candidate.step > event.step
                    and candidate.metadata.get("gate_id") == gate_id
                ),
                None,
            )
            horizon_end = event.step + self.outcome_horizon_steps
            window_end = min(
                horizon_end,
                (next_same.step - 1) if next_same is not None else ordered[-1].step,
            )
            window = [
                candidate
                for candidate in ordered
                if event.step < candidate.step <= window_end
            ]
            followup = (
                next_same
                if next_same is not None and next_same.step <= horizon_end + 1
                else None
            )
            recommendation = recommendation_map.get(str(event.event_id))
            if recommendation is not None and recommendation.gate_id != gate_id:
                raise GateTrainingDataError("model recommendation gate_id mismatch")
            records.append(
                _build_record(
                    event,
                    trace_fingerprint=trace_fp,
                    window=window,
                    followup_gate=followup,
                    recommendation=recommendation,
                )
            )
        return tuple(records)


def _dataset_fingerprint(
    *,
    outcome_horizon_steps: int,
    sources: Sequence[SourceTraceDescriptor],
    records: Sequence[GateTrainingRecord],
) -> str:
    return _fingerprint(
        {
            "collector_version": COLLECTOR_VERSION,
            "usefulness_policy_version": USEFULNESS_POLICY_VERSION,
            "outcome_horizon_steps": outcome_horizon_steps,
            "source_traces": [item.model_dump(mode="json") for item in sources],
            "record_fingerprints": [record.record_fingerprint for record in records],
        }
    )


def _validate_manifest(
    manifest: GateTrainingDatasetManifest,
    records: Sequence[GateTrainingRecord],
) -> None:
    if manifest.record_count != len(records):
        raise ValueError("gate training manifest record count mismatch")
    by_gate = dict(Counter(record.gate_id for record in records))
    if manifest.records_by_gate != by_gate:
        raise ValueError("gate training manifest gate counts mismatch")
    usefulness = dict(Counter(record.later_usefulness.state.value for record in records))
    if manifest.usefulness_counts != usefulness:
        raise ValueError("gate training manifest usefulness counts mismatch")
    recommendation_count = sum(record.model_recommendation is not None for record in records)
    if manifest.model_recommendation_count != recommendation_count:
        raise ValueError("gate training manifest recommendation count mismatch")
    expected = _dataset_fingerprint(
        outcome_horizon_steps=manifest.outcome_horizon_steps,
        sources=manifest.source_traces,
        records=records,
    )
    if manifest.dataset_fingerprint != expected:
        raise ValueError("gate training dataset fingerprint mismatch")


def collect_gate_training_dataset(
    trace_paths: Sequence[str | Path],
    *,
    recommendations: Sequence[GateModelRecommendation] = (),
    outcome_horizon_steps: int = 32,
) -> GateTrainingDataset:
    """Collect one deterministic dataset from verified trace ledger files."""

    if not trace_paths:
        raise GateTrainingDataError("at least one trace ledger is required")
    collector = GateTrainingDataCollector(outcome_horizon_steps=outcome_horizon_steps)
    recommendation_by_event: dict[str, GateModelRecommendation] = {}
    for recommendation in recommendations:
        key = str(recommendation.decision_event_id)
        if key in recommendation_by_event:
            raise GateTrainingDataError("duplicate model recommendation for one gate event")
        recommendation_by_event[key] = recommendation

    grouped: dict[str, list[TraceEvent]] = {}
    for raw_path in trace_paths:
        path = Path(raw_path)
        if not path.is_file():
            raise GateTrainingDataError(f"trace ledger does not exist: {path}")
        for event in TraceStore(path).events():
            grouped.setdefault(str(event.trace_id), []).append(event)

    sources: list[SourceTraceDescriptor] = []
    records: list[GateTrainingRecord] = []
    consumed_recommendations: set[str] = set()
    for trace_key in sorted(grouped):
        events = sorted(grouped[trace_key], key=lambda event: event.step)
        # Duplicate trace IDs across input ledgers would produce duplicate steps and must
        # fail rather than silently overweight one trajectory.
        if [event.step for event in events] != list(range(1, len(events) + 1)):
            raise GateTrainingDataError(
                f"duplicate or non-contiguous source trace detected: {trace_key}"
            )
        trace_fp = _trace_fingerprint(events)
        sources.append(
            SourceTraceDescriptor(
                trace_id=events[0].trace_id,
                system_version=events[0].system_version,
                event_count=len(events),
                trace_fingerprint=trace_fp,
            )
        )
        event_ids = {str(event.event_id) for event in events}
        per_trace_recommendations = {
            key: value
            for key, value in recommendation_by_event.items()
            if key in event_ids
        }
        consumed_recommendations.update(per_trace_recommendations)
        records.extend(
            collector.collect_trace(
                events,
                recommendations=per_trace_recommendations,
            )
        )

    orphaned = sorted(set(recommendation_by_event) - consumed_recommendations)
    if orphaned:
        raise GateTrainingDataError("model recommendation references an absent source trace")

    ordered_records = tuple(
        sorted(
            records,
            key=lambda record: (
                str(record.system_version),
                str(record.trace_id),
                record.decision_step,
                record.gate_id,
            ),
        )
    )
    ordered_sources = tuple(
        sorted(sources, key=lambda item: (str(item.system_version), str(item.trace_id)))
    )
    records_by_gate = dict(Counter(record.gate_id for record in ordered_records))
    usefulness_counts = dict(
        Counter(record.later_usefulness.state.value for record in ordered_records)
    )
    dataset_fp = _dataset_fingerprint(
        outcome_horizon_steps=outcome_horizon_steps,
        sources=ordered_sources,
        records=ordered_records,
    )
    manifest = GateTrainingDatasetManifest(
        outcome_horizon_steps=outcome_horizon_steps,
        record_count=len(ordered_records),
        source_traces=ordered_sources,
        records_by_gate=records_by_gate,
        usefulness_counts=usefulness_counts,
        model_recommendation_count=sum(
            record.model_recommendation is not None for record in ordered_records
        ),
        dataset_fingerprint=dataset_fp,
    )
    return GateTrainingDataset(manifest=manifest, records=ordered_records)


def load_model_recommendations(path: str | Path) -> tuple[GateModelRecommendation, ...]:
    source = Path(path)
    if not source.is_file():
        raise GateTrainingDataError(f"model-recommendation JSONL does not exist: {source}")
    result: list[GateModelRecommendation] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            result.append(GateModelRecommendation.model_validate_json(line))
        except ValueError as exc:
            raise GateTrainingDataError(
                f"invalid model recommendation at line {line_number}: {exc}"
            ) from exc
    return tuple(result)


def load_gate_training_dataset(path: str | Path) -> GateTrainingDataset:
    root = Path(path)
    manifest_path = root / "manifest.json"
    records_path = root / "records.jsonl"
    if not manifest_path.is_file() or not records_path.is_file():
        raise GateTrainingDataError("gate training dataset requires manifest.json and records.jsonl")
    manifest = GateTrainingDatasetManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    records: list[GateTrainingRecord] = []
    for line_number, line in enumerate(records_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(GateTrainingRecord.model_validate_json(line))
        except ValueError as exc:
            raise GateTrainingDataError(
                f"invalid gate training record at line {line_number}: {exc}"
            ) from exc
    try:
        return GateTrainingDataset(manifest=manifest, records=tuple(records))
    except ValueError as exc:
        raise GateTrainingDataError(f"gate training dataset integrity failure: {exc}") from exc
