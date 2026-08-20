"""Milestone 18 learned dynamic-compute experiments with deterministic authority.

The learned controller in this module is deliberately peripheral. It predicts a
compute-allocation recommendation, but it cannot consume budget, mutate task state,
select a live model, perform retrieval, or execute verification. A separate
``ComputeAuthorityAdjudicator`` rechecks any non-stop recommendation through the
existing deterministic ``ComputeGate`` before the owning runtime may act on it.

The first learned implementation is intentionally small and dependency-free: a
nearest-centroid controller trained from grounded examples. The architecture is more
important than the model class; future neural controllers can implement the same
protocol and compete against the same deterministic baseline and benchmark contract.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from enum import StrEnum
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness_x.core.contracts import ComputeBudget
from harness_x.gates import ComputeAction, ComputeGate, ComputeRequest
from harness_x.orchestrator import BudgetDelta, BudgetUsage

from .training_data import GateTrainingDataset, GateTrainingRecord, UsefulnessState


_STRICT_FROZEN = ConfigDict(frozen=True, extra="forbid")
MODEL_VERSION = "centroid-dynamic-compute-v1"
DETERMINISTIC_VERSION = "deterministic-dynamic-compute-v1"
BENCHMARK_VERSION = "dynamic-compute-benchmark-v1"


class DynamicComputeError(ValueError):
    """Raised when learned-controller evidence or artifacts are not trustworthy."""


class DynamicComputeAction(StrEnum):
    """Recommendation vocabulary planned by the Harness X implementation plan."""

    STOP = "stop"
    REASON_AGAIN = "another_reasoning_call"
    EXPAND_CONTEXT = "larger_context"
    STRONGER_MODEL = "stronger_model"
    EXTRA_RETRIEVAL = "extra_retrieval"
    EXTRA_VERIFICATION = "extra_verification"
    PARALLEL_CANDIDATES = "parallel_candidate_generation"


# A real trace can only supervise actions that actually have grounded evidence. The
# interface supports all planned actions, while preparation from Milestone 17 traces
# remains conservative about what those traces can teach.
TRACE_GROUNDED_ACTIONS = frozenset(
    {
        DynamicComputeAction.STOP,
        DynamicComputeAction.REASON_AGAIN,
        DynamicComputeAction.EXTRA_RETRIEVAL,
        DynamicComputeAction.EXTRA_VERIFICATION,
    }
)


class DynamicComputeState(BaseModel):
    """Bounded pre-decision features visible to a compute controller.

    No downstream outcome is present here. Richer fields can be supplied by a runtime
    or simulator; trace-derived examples use zero/default values for signals that were
    not recorded by the older deterministic ComputeGate input contract.
    """

    model_config = _STRICT_FROZEN

    task_difficulty: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    retrieval_usefulness: float = Field(default=0.0, ge=0.0, le=1.0)
    verifier_rejection_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    context_pressure: float = Field(default=0.0, ge=0.0, le=1.0)
    candidate_disagreement: float = Field(default=0.0, ge=0.0, le=1.0)
    remaining_reasoning_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    remaining_tool_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    requested_reasoning_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    current_model_tier: int = Field(default=0, ge=0, le=8)
    recent_reasoning_calls: int = Field(default=0, ge=0)
    recent_retrievals: int = Field(default=0, ge=0)
    recent_verifications: int = Field(default=0, ge=0)
    explicit_stop: bool = False
    completion_condition_met: bool = False


FEATURE_NAMES = (
    "task_difficulty",
    "uncertainty",
    "progress",
    "retrieval_usefulness",
    "verifier_rejection_rate",
    "context_pressure",
    "candidate_disagreement",
    "remaining_reasoning_ratio",
    "remaining_tool_ratio",
    "requested_reasoning_ratio",
    "current_model_tier",
    "recent_reasoning_calls",
    "recent_retrievals",
    "recent_verifications",
    "explicit_stop",
    "completion_condition_met",
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


def _feature_vector(state: DynamicComputeState) -> tuple[float, ...]:
    raw = state.model_dump(mode="python")
    vector: list[float] = []
    for name in FEATURE_NAMES:
        value = raw[name]
        if isinstance(value, bool):
            vector.append(1.0 if value else 0.0)
        else:
            vector.append(float(value))
    return tuple(vector)


class DynamicComputeRecommendation(BaseModel):
    model_config = _STRICT_FROZEN

    action: DynamicComputeAction
    predicted_value: float = Field(ge=0.0, le=1.0)
    predicted_incremental_cost: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    controller_id: str = Field(min_length=1)
    controller_version: str = Field(min_length=1)
    evidence_basis: str = Field(min_length=1)


class DynamicComputeController(Protocol):
    @property
    def controller_id(self) -> str: ...

    @property
    def controller_version(self) -> str: ...

    def recommend(self, state: DynamicComputeState) -> DynamicComputeRecommendation: ...


class DeterministicDynamicComputeController:
    """Conservative permanent baseline for Milestone 18 comparisons.

    It intentionally uses only actions that correspond closely to already exercised
    deterministic Harness X control paths. New expensive actions are not adopted just
    because the learned controller can name them.
    """

    controller_id = "deterministic_dynamic_compute"
    controller_version = DETERMINISTIC_VERSION

    def recommend(self, state: DynamicComputeState) -> DynamicComputeRecommendation:
        if (
            state.explicit_stop
            or state.completion_condition_met
            or state.remaining_reasoning_ratio <= 0.0
        ):
            action = DynamicComputeAction.STOP
            value = 1.0
            cost = 0.0
            basis = "hard_stop_or_completion"
        elif state.verifier_rejection_rate >= 0.60 and state.recent_verifications < 2:
            action = DynamicComputeAction.EXTRA_VERIFICATION
            value = min(1.0, 0.55 + 0.4 * state.verifier_rejection_rate)
            cost = 0.25
            basis = "verification_rejection"
        elif (
            state.uncertainty >= 0.65
            and state.retrieval_usefulness >= 0.50
            and state.recent_retrievals < 2
        ):
            action = DynamicComputeAction.EXTRA_RETRIEVAL
            value = min(1.0, 0.45 + 0.45 * state.retrieval_usefulness)
            cost = 0.20
            basis = "uncertainty_with_useful_retrieval"
        elif state.progress >= 0.88 and state.uncertainty <= 0.25:
            action = DynamicComputeAction.STOP
            value = 0.90
            cost = 0.0
            basis = "high_progress_low_uncertainty"
        else:
            action = DynamicComputeAction.REASON_AGAIN
            value = min(1.0, 0.45 + 0.35 * state.task_difficulty + 0.20 * state.uncertainty)
            cost = 0.45
            basis = "conservative_continue"
        return DynamicComputeRecommendation(
            action=action,
            predicted_value=value,
            predicted_incremental_cost=cost,
            confidence=1.0,
            controller_id=self.controller_id,
            controller_version=self.controller_version,
            evidence_basis=basis,
        )


class DynamicComputeTrainingExample(BaseModel):
    model_config = _STRICT_FROZEN

    schema_version: str = "dynamic-compute-training-example-v1"
    example_id: str = Field(min_length=1)
    scenario_family: str = Field(min_length=1)
    state: DynamicComputeState
    target_action: DynamicComputeAction
    observed_value: float = Field(ge=0.0, le=1.0)
    observed_incremental_cost: float = Field(ge=0.0)
    label_source: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()


class FeatureScaler(BaseModel):
    model_config = _STRICT_FROZEN

    minimums: tuple[float, ...]
    maximums: tuple[float, ...]

    @model_validator(mode="after")
    def correct_width(self) -> "FeatureScaler":
        if len(self.minimums) != len(FEATURE_NAMES) or len(self.maximums) != len(FEATURE_NAMES):
            raise ValueError("dynamic compute feature scaler width mismatch")
        if any(high < low for low, high in zip(self.minimums, self.maximums)):
            raise ValueError("dynamic compute feature scaler has inverted range")
        return self

    def transform(self, values: Sequence[float]) -> tuple[float, ...]:
        if len(values) != len(FEATURE_NAMES):
            raise DynamicComputeError("dynamic compute feature vector width mismatch")
        normalized: list[float] = []
        for value, low, high in zip(values, self.minimums, self.maximums):
            span = high - low
            normalized.append(0.0 if span <= 1e-12 else (float(value) - low) / span)
        return tuple(normalized)


class ActionCentroid(BaseModel):
    model_config = _STRICT_FROZEN

    action: DynamicComputeAction
    example_count: int = Field(ge=1)
    centroid: tuple[float, ...]
    mean_value: float = Field(ge=0.0, le=1.0)
    mean_incremental_cost: float = Field(ge=0.0)

    @model_validator(mode="after")
    def correct_width(self) -> "ActionCentroid":
        if len(self.centroid) != len(FEATURE_NAMES):
            raise ValueError("dynamic compute centroid width mismatch")
        return self


class LearnedComputeControllerArtifact(BaseModel):
    model_config = _STRICT_FROZEN

    schema_version: str = "learned-compute-controller-artifact-v1"
    model_version: str = MODEL_VERSION
    feature_names: tuple[str, ...] = FEATURE_NAMES
    training_example_count: int = Field(ge=1)
    training_fingerprint: str = Field(min_length=64, max_length=64)
    scaler: FeatureScaler
    profiles: tuple[ActionCentroid, ...]
    artifact_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_artifact(self) -> "LearnedComputeControllerArtifact":
        if self.feature_names != FEATURE_NAMES:
            raise ValueError("learned controller feature schema mismatch")
        if len({profile.action for profile in self.profiles}) != len(self.profiles):
            raise ValueError("learned controller contains duplicate action profiles")
        if not self.profiles:
            raise ValueError("learned controller requires at least one action profile")
        expected = _fingerprint(
            self.model_dump(mode="json", exclude={"artifact_fingerprint"})
        )
        if expected != self.artifact_fingerprint:
            raise ValueError("learned controller artifact fingerprint mismatch")
        return self

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")


class LearnedDynamicComputeController:
    """Small nearest-centroid controller learned from grounded examples."""

    controller_id = "learned_dynamic_compute"

    def __init__(self, artifact: LearnedComputeControllerArtifact) -> None:
        self.artifact = artifact
        self.controller_version = artifact.model_version

    @classmethod
    def train(
        cls,
        examples: Sequence[DynamicComputeTrainingExample],
    ) -> "LearnedDynamicComputeController":
        if not examples:
            raise DynamicComputeError("cannot train dynamic compute controller without examples")
        ids = [example.example_id for example in examples]
        if len(ids) != len(set(ids)):
            raise DynamicComputeError("duplicate dynamic compute training example IDs")

        vectors = [_feature_vector(example.state) for example in examples]
        minimums = tuple(min(vector[index] for vector in vectors) for index in range(len(FEATURE_NAMES)))
        maximums = tuple(max(vector[index] for vector in vectors) for index in range(len(FEATURE_NAMES)))
        scaler = FeatureScaler(minimums=minimums, maximums=maximums)
        grouped: dict[DynamicComputeAction, list[tuple[DynamicComputeTrainingExample, tuple[float, ...]]]] = defaultdict(list)
        for example, vector in zip(examples, vectors):
            grouped[example.target_action].append((example, scaler.transform(vector)))

        profiles: list[ActionCentroid] = []
        for action in sorted(grouped, key=lambda item: item.value):
            items = grouped[action]
            centroid = tuple(
                sum(vector[index] for _, vector in items) / len(items)
                for index in range(len(FEATURE_NAMES))
            )
            profiles.append(
                ActionCentroid(
                    action=action,
                    example_count=len(items),
                    centroid=centroid,
                    mean_value=sum(example.observed_value for example, _ in items) / len(items),
                    mean_incremental_cost=(
                        sum(example.observed_incremental_cost for example, _ in items) / len(items)
                    ),
                )
            )

        training_payload = [example.model_dump(mode="json") for example in examples]
        artifact_payload = {
            "schema_version": "learned-compute-controller-artifact-v1",
            "model_version": MODEL_VERSION,
            "feature_names": FEATURE_NAMES,
            "training_example_count": len(examples),
            "training_fingerprint": _fingerprint(training_payload),
            "scaler": scaler.model_dump(mode="json"),
            "profiles": [profile.model_dump(mode="json") for profile in profiles],
        }
        artifact_payload["artifact_fingerprint"] = _fingerprint(artifact_payload)
        artifact = LearnedComputeControllerArtifact.model_validate(artifact_payload)
        return cls(artifact)

    def recommend(self, state: DynamicComputeState) -> DynamicComputeRecommendation:
        if state.explicit_stop or state.completion_condition_met or state.remaining_reasoning_ratio <= 0.0:
            return DynamicComputeRecommendation(
                action=DynamicComputeAction.STOP,
                predicted_value=1.0,
                predicted_incremental_cost=0.0,
                confidence=1.0,
                controller_id=self.controller_id,
                controller_version=self.controller_version,
                evidence_basis=f"hard_stop:{self.artifact.artifact_fingerprint[:12]}",
            )

        vector = self.artifact.scaler.transform(_feature_vector(state))
        ranked: list[tuple[float, float, str, ActionCentroid]] = []
        for profile in self.artifact.profiles:
            # Euclidean distance over normalized state. Tie-break towards higher learned
            # value, then cheaper action, then stable action name.
            distance = math.sqrt(
                sum((value - center) ** 2 for value, center in zip(vector, profile.centroid))
            )
            ranked.append((distance, -profile.mean_value + 0.05 * profile.mean_incremental_cost, profile.action.value, profile))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        distance, _, _, selected = ranked[0]
        confidence = 1.0 / (1.0 + distance)
        return DynamicComputeRecommendation(
            action=selected.action,
            predicted_value=selected.mean_value,
            predicted_incremental_cost=selected.mean_incremental_cost,
            confidence=confidence,
            controller_id=self.controller_id,
            controller_version=self.controller_version,
            evidence_basis=f"nearest_centroid:{self.artifact.artifact_fingerprint[:12]}",
        )


def load_learned_compute_controller(path: str | Path) -> LearnedDynamicComputeController:
    source = Path(path)
    if not source.is_file():
        raise DynamicComputeError(f"learned compute controller artifact does not exist: {source}")
    try:
        artifact = LearnedComputeControllerArtifact.model_validate_json(
            source.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise DynamicComputeError(f"invalid learned compute controller artifact: {exc}") from exc
    return LearnedDynamicComputeController(artifact)


def _number(mapping: Mapping[str, object], key: str, default: float = 0.0) -> float:
    value = mapping.get(key, default)
    return float(value) if isinstance(value, (int, float)) else default


def _nested_dict(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = mapping.get(key)
    return value if isinstance(value, Mapping) else {}


def _safe_ratio(numerator: float, denominator: float, *, default: float = 0.0) -> float:
    if denominator <= 0:
        return default
    return max(0.0, min(1.0, numerator / denominator))


def _state_from_compute_record(record: GateTrainingRecord) -> DynamicComputeState:
    state = record.state_features
    budget = _nested_dict(state, "budget")
    usage = _nested_dict(state, "usage")
    requested = _nested_dict(state, "requested")
    max_reasoning = _number(budget, "max_reasoning_steps")
    max_tools = _number(budget, "max_tool_actions")
    used_reasoning = _number(usage, "reasoning_steps")
    used_tools = _number(usage, "tool_actions")
    requested_reasoning = _number(requested, "reasoning_steps")
    progress = _safe_ratio(used_reasoning, max_reasoning, default=0.0)
    return DynamicComputeState(
        progress=progress,
        remaining_reasoning_ratio=(
            _safe_ratio(max(0.0, max_reasoning - used_reasoning), max_reasoning, default=0.0)
            if max_reasoning > 0
            else 0.0
        ),
        remaining_tool_ratio=(
            _safe_ratio(max(0.0, max_tools - used_tools), max_tools, default=1.0)
            if max_tools > 0
            else 1.0
        ),
        requested_reasoning_ratio=(
            _safe_ratio(requested_reasoning, max_reasoning, default=0.0)
            if max_reasoning > 0
            else 0.0
        ),
        recent_reasoning_calls=int(max(0.0, used_reasoning)),
        explicit_stop=bool(state.get("explicit_stop", False)),
        completion_condition_met=bool(state.get("completion_condition_met", False)),
    )


def _observed_trace_action(record: GateTrainingRecord) -> DynamicComputeAction | None:
    action = record.policy_decision.get("action")
    if action in {"stop", "suspend"}:
        return DynamicComputeAction.STOP
    if action != "allow":
        return None
    outcome = record.actual_outcome
    # These are observational labels, not claims that the observed action was globally
    # optimal. Prefer the most discriminating directly measured downstream behavior.
    if outcome.verification_rejections > 0:
        return DynamicComputeAction.EXTRA_VERIFICATION
    if outcome.useful_retrievals > 0:
        return DynamicComputeAction.EXTRA_RETRIEVAL
    if outcome.reasoning_completions > 0 or outcome.routine_successes > 0:
        return DynamicComputeAction.REASON_AGAIN
    return None


def prepare_dynamic_compute_examples(
    dataset: GateTrainingDataset,
) -> tuple[DynamicComputeTrainingExample, ...]:
    """Convert evidence-bearing Milestone 17 compute records into training examples.

    Records with unknown usefulness or no grounded observed action are deliberately
    omitted. The resulting model therefore never receives invented labels for
    stronger-model/context/parallel actions that older traces did not exercise.
    """

    examples: list[DynamicComputeTrainingExample] = []
    for record in dataset.records:
        if record.gate_id != "compute":
            continue
        if record.later_usefulness.state == UsefulnessState.UNKNOWN:
            continue
        target = _observed_trace_action(record)
        if target is None or target not in TRACE_GROUNDED_ACTIONS:
            continue
        score = record.later_usefulness.score
        if score is None:
            continue
        outcome = record.actual_outcome
        cost = (
            float(record.immediate_cost or 0.0)
            + 0.45 * outcome.reasoning_completions
            + 0.20 * outcome.retrieval_attempts
            + 0.25 * (outcome.verification_accepts + outcome.verification_rejections)
            + 0.60 * outcome.tool_actions
        )
        examples.append(
            DynamicComputeTrainingExample(
                example_id=f"trace_{record.record_id}",
                scenario_family=f"trace:{record.policy_version}",
                state=_state_from_compute_record(record),
                target_action=target,
                observed_value=score,
                observed_incremental_cost=cost,
                label_source="observed_gate_trajectory",
                evidence_refs=(
                    str(record.decision_event_id),
                    *tuple(str(item) for item in record.later_usefulness.evidence_event_ids),
                ),
            )
        )
    return tuple(examples)


class AuthorizedDynamicComputeDecision(BaseModel):
    model_config = _STRICT_FROZEN

    recommendation: DynamicComputeRecommendation
    requested_budget: BudgetDelta
    compute_gate_action: ComputeAction
    effective_action: DynamicComputeAction | None
    permitted: bool
    authority_reason: str = Field(min_length=1)


class ComputeAuthorityAdjudicator:
    """Recheck learned recommendations through the existing deterministic gate."""

    @staticmethod
    def requested_budget(action: DynamicComputeAction) -> BudgetDelta:
        if action == DynamicComputeAction.STOP:
            return BudgetDelta()
        if action == DynamicComputeAction.PARALLEL_CANDIDATES:
            return BudgetDelta(reasoning_steps=2)
        # All other active recommendations require at least one externally accounted
        # reasoning allocation. This is intentionally conservative for the first cut.
        return BudgetDelta(reasoning_steps=1)

    def adjudicate(
        self,
        recommendation: DynamicComputeRecommendation,
        *,
        compute_gate: ComputeGate,
        budget: ComputeBudget,
        usage: BudgetUsage,
    ) -> AuthorizedDynamicComputeDecision:
        requested = self.requested_budget(recommendation.action)
        gate_decision = compute_gate.evaluate(
            ComputeRequest(
                budget=budget,
                usage=usage,
                requested=requested,
                explicit_stop=recommendation.action == DynamicComputeAction.STOP,
            )
        )
        action = ComputeAction(str(gate_decision.decision.get("action")))
        if action == ComputeAction.ALLOW:
            return AuthorizedDynamicComputeDecision(
                recommendation=recommendation,
                requested_budget=requested,
                compute_gate_action=action,
                effective_action=recommendation.action,
                permitted=True,
                authority_reason="deterministic_compute_gate_allowed",
            )
        if action == ComputeAction.STOP:
            return AuthorizedDynamicComputeDecision(
                recommendation=recommendation,
                requested_budget=requested,
                compute_gate_action=action,
                effective_action=DynamicComputeAction.STOP,
                permitted=recommendation.action == DynamicComputeAction.STOP,
                authority_reason="deterministic_compute_gate_stopped",
            )
        return AuthorizedDynamicComputeDecision(
            recommendation=recommendation,
            requested_budget=requested,
            compute_gate_action=action,
            effective_action=None,
            permitted=False,
            authority_reason="deterministic_compute_gate_suspended",
        )


class DynamicComputeBenchmarkCase(BaseModel):
    model_config = _STRICT_FROZEN

    case_id: str = Field(min_length=1)
    scenario_family: str = Field(min_length=1)
    state: DynamicComputeState
    expected_action: DynamicComputeAction
    utility_by_action: dict[DynamicComputeAction, float]
    cost_by_action: dict[DynamicComputeAction, float]

    @model_validator(mode="after")
    def complete_expected_action(self) -> "DynamicComputeBenchmarkCase":
        if self.expected_action not in self.utility_by_action:
            raise ValueError("benchmark expected action missing utility")
        if self.expected_action not in self.cost_by_action:
            raise ValueError("benchmark expected action missing cost")
        for value in self.utility_by_action.values():
            if value < 0.0 or value > 1.0:
                raise ValueError("benchmark utilities must be within [0, 1]")
        if any(value < 0.0 for value in self.cost_by_action.values()):
            raise ValueError("benchmark costs cannot be negative")
        return self


class ControllerEvaluation(BaseModel):
    model_config = _STRICT_FROZEN

    controller_id: str
    controller_version: str
    case_count: int = Field(ge=1)
    mean_utility: float = Field(ge=0.0, le=1.0)
    mean_cost: float = Field(ge=0.0)
    mean_net_value: float
    exact_action_accuracy: float = Field(ge=0.0, le=1.0)
    calibration_brier: float = Field(ge=0.0)
    premature_stops: int = Field(ge=0)
    unnecessary_extra_compute: int = Field(ge=0)
    stronger_model_calls: int = Field(ge=0)
    retrieval_calls: int = Field(ge=0)
    action_counts: dict[str, int]


class FrontierPolicy(BaseModel):
    model_config = _STRICT_FROZEN

    policy_version: str = "dynamic-compute-frontier-v1"
    cost_weight: float = Field(default=0.25, ge=0.0)
    min_net_value_gain: float = Field(default=0.04, ge=0.0)
    max_premature_stop_increase: int = Field(default=0, ge=0)
    max_calibration_regression: float = Field(default=0.05, ge=0.0)
    require_nonnegative_utility_delta: bool = True


class DynamicComputeComparisonReport(BaseModel):
    model_config = _STRICT_FROZEN

    schema_version: str = "dynamic-compute-comparison-report-v1"
    benchmark_version: str = BENCHMARK_VERSION
    benchmark_fingerprint: str = Field(min_length=64, max_length=64)
    baseline: ControllerEvaluation
    learned: ControllerEvaluation
    policy: FrontierPolicy
    utility_delta: float
    cost_delta: float
    net_value_delta: float
    learned_frontier_improved: bool
    rejection_reasons: tuple[str, ...] = ()


def evaluate_dynamic_compute_controller(
    controller: DynamicComputeController,
    cases: Sequence[DynamicComputeBenchmarkCase],
    *,
    cost_weight: float = 0.25,
) -> ControllerEvaluation:
    if not cases:
        raise DynamicComputeError("dynamic compute benchmark requires cases")
    utility_total = 0.0
    cost_total = 0.0
    net_total = 0.0
    exact = 0
    brier_total = 0.0
    premature = 0
    unnecessary = 0
    actions: Counter[str] = Counter()

    for case in cases:
        recommendation = controller.recommend(case.state)
        action = recommendation.action
        utility = case.utility_by_action.get(action, 0.0)
        cost = case.cost_by_action.get(action, recommendation.predicted_incremental_cost)
        utility_total += utility
        cost_total += cost
        net_total += utility - cost_weight * cost
        exact += action == case.expected_action
        brier_total += (recommendation.predicted_value - utility) ** 2
        actions[action.value] += 1
        if action == DynamicComputeAction.STOP and case.expected_action != DynamicComputeAction.STOP:
            premature += 1
        if action != DynamicComputeAction.STOP and case.expected_action == DynamicComputeAction.STOP:
            unnecessary += 1

    count = len(cases)
    return ControllerEvaluation(
        controller_id=controller.controller_id,
        controller_version=controller.controller_version,
        case_count=count,
        mean_utility=utility_total / count,
        mean_cost=cost_total / count,
        mean_net_value=net_total / count,
        exact_action_accuracy=exact / count,
        calibration_brier=brier_total / count,
        premature_stops=premature,
        unnecessary_extra_compute=unnecessary,
        stronger_model_calls=actions[DynamicComputeAction.STRONGER_MODEL.value],
        retrieval_calls=actions[DynamicComputeAction.EXTRA_RETRIEVAL.value],
        action_counts=dict(actions),
    )


def compare_dynamic_compute_controllers(
    baseline_controller: DynamicComputeController,
    learned_controller: DynamicComputeController,
    cases: Sequence[DynamicComputeBenchmarkCase],
    *,
    policy: FrontierPolicy | None = None,
) -> DynamicComputeComparisonReport:
    effective = policy or FrontierPolicy()
    baseline = evaluate_dynamic_compute_controller(
        baseline_controller, cases, cost_weight=effective.cost_weight
    )
    learned = evaluate_dynamic_compute_controller(
        learned_controller, cases, cost_weight=effective.cost_weight
    )
    utility_delta = learned.mean_utility - baseline.mean_utility
    cost_delta = learned.mean_cost - baseline.mean_cost
    net_delta = learned.mean_net_value - baseline.mean_net_value
    reasons: list[str] = []
    if effective.require_nonnegative_utility_delta and utility_delta < -1e-12:
        reasons.append("learned_utility_regressed")
    if net_delta < effective.min_net_value_gain:
        reasons.append("insufficient_capability_cost_frontier_gain")
    if learned.premature_stops > baseline.premature_stops + effective.max_premature_stop_increase:
        reasons.append("premature_stopping_regressed")
    if learned.calibration_brier > baseline.calibration_brier + effective.max_calibration_regression:
        reasons.append("value_calibration_regressed")
    benchmark_fp = _fingerprint([case.model_dump(mode="json") for case in cases])
    return DynamicComputeComparisonReport(
        benchmark_fingerprint=benchmark_fp,
        baseline=baseline,
        learned=learned,
        policy=effective,
        utility_delta=utility_delta,
        cost_delta=cost_delta,
        net_value_delta=net_delta,
        learned_frontier_improved=not reasons,
        rejection_reasons=tuple(reasons),
    )


def _costs(expected: DynamicComputeAction) -> dict[DynamicComputeAction, float]:
    # Standardized experiment cost units, not dollars/tokens. Real adapters can replace
    # these with measured inference telemetry while preserving the report contract.
    result = {
        DynamicComputeAction.STOP: 0.0,
        DynamicComputeAction.REASON_AGAIN: 0.45,
        DynamicComputeAction.EXPAND_CONTEXT: 0.30,
        DynamicComputeAction.STRONGER_MODEL: 0.85,
        DynamicComputeAction.EXTRA_RETRIEVAL: 0.20,
        DynamicComputeAction.EXTRA_VERIFICATION: 0.25,
        DynamicComputeAction.PARALLEL_CANDIDATES: 0.90,
    }
    # Keep expected action present even if future enum changes.
    result.setdefault(expected, 0.5)
    return result


def _utilities(expected: DynamicComputeAction, *, baseline_alternative: DynamicComputeAction | None = None) -> dict[DynamicComputeAction, float]:
    values = {action: 0.20 for action in DynamicComputeAction}
    values[expected] = 1.0
    if baseline_alternative is not None and baseline_alternative != expected:
        values[baseline_alternative] = 0.58
    if expected != DynamicComputeAction.STOP:
        values[DynamicComputeAction.STOP] = 0.05
    return values


def build_reference_dynamic_compute_training_examples() -> tuple[DynamicComputeTrainingExample, ...]:
    """Deterministic simulator-derived examples for CI/controller mechanics.

    These are not claimed to be production training data. They provide explicit
    simulator ground truth for all planned actions so the learned-controller and
    benchmark plumbing can be qualified without a GPU or an LLM.
    """

    prototypes = (
        ("easy_stop", DynamicComputeAction.STOP, DynamicComputeState(task_difficulty=0.15, uncertainty=0.08, progress=0.96, remaining_reasoning_ratio=0.70)),
        ("reason_hard", DynamicComputeAction.REASON_AGAIN, DynamicComputeState(task_difficulty=0.78, uncertainty=0.52, progress=0.30, remaining_reasoning_ratio=0.75, recent_reasoning_calls=1)),
        ("retrieve", DynamicComputeAction.EXTRA_RETRIEVAL, DynamicComputeState(task_difficulty=0.55, uncertainty=0.90, progress=0.38, retrieval_usefulness=0.88, remaining_reasoning_ratio=0.72, recent_retrievals=0)),
        ("verify", DynamicComputeAction.EXTRA_VERIFICATION, DynamicComputeState(task_difficulty=0.62, uncertainty=0.50, progress=0.68, verifier_rejection_rate=0.86, remaining_reasoning_ratio=0.64, recent_verifications=0)),
        ("context", DynamicComputeAction.EXPAND_CONTEXT, DynamicComputeState(task_difficulty=0.72, uncertainty=0.45, progress=0.48, context_pressure=0.94, remaining_reasoning_ratio=0.70)),
        ("stronger", DynamicComputeAction.STRONGER_MODEL, DynamicComputeState(task_difficulty=0.96, uncertainty=0.72, progress=0.28, retrieval_usefulness=0.20, current_model_tier=0, remaining_reasoning_ratio=0.82)),
        ("parallel", DynamicComputeAction.PARALLEL_CANDIDATES, DynamicComputeState(task_difficulty=0.86, uncertainty=0.58, progress=0.44, candidate_disagreement=0.94, remaining_reasoning_ratio=0.86)),
    )
    examples: list[DynamicComputeTrainingExample] = []
    for family, action, state in prototypes:
        for index, jitter in enumerate((-0.03, 0.0, 0.03)):
            payload = state.model_dump(mode="python")
            # Jitter only continuous task signals, preserving valid ranges and hard flags.
            for key in (
                "task_difficulty",
                "uncertainty",
                "progress",
                "retrieval_usefulness",
                "verifier_rejection_rate",
                "context_pressure",
                "candidate_disagreement",
            ):
                payload[key] = max(0.0, min(1.0, float(payload[key]) + jitter))
            sample_state = DynamicComputeState.model_validate(payload)
            cost = _costs(action)[action]
            examples.append(
                DynamicComputeTrainingExample(
                    example_id=f"reference_{family}_{index}",
                    scenario_family=family,
                    state=sample_state,
                    target_action=action,
                    observed_value=1.0,
                    observed_incremental_cost=cost,
                    label_source="deterministic_reference_simulator",
                    evidence_refs=(f"simulator:{family}:{index}",),
                )
            )
    return tuple(examples)


def build_reference_dynamic_compute_eval_cases() -> tuple[DynamicComputeBenchmarkCase, ...]:
    cases = (
        ("stop_heldout", "easy_stop", DynamicComputeAction.STOP, DynamicComputeAction.REASON_AGAIN, DynamicComputeState(task_difficulty=0.20, uncertainty=0.10, progress=0.94, remaining_reasoning_ratio=0.60)),
        ("reason_heldout", "reason_hard", DynamicComputeAction.REASON_AGAIN, None, DynamicComputeState(task_difficulty=0.82, uncertainty=0.50, progress=0.34, remaining_reasoning_ratio=0.72, recent_reasoning_calls=1)),
        ("retrieve_heldout", "retrieve", DynamicComputeAction.EXTRA_RETRIEVAL, None, DynamicComputeState(task_difficulty=0.58, uncertainty=0.87, progress=0.42, retrieval_usefulness=0.84, remaining_reasoning_ratio=0.68, recent_retrievals=0)),
        ("verify_heldout", "verify", DynamicComputeAction.EXTRA_VERIFICATION, None, DynamicComputeState(task_difficulty=0.66, uncertainty=0.47, progress=0.70, verifier_rejection_rate=0.82, remaining_reasoning_ratio=0.60, recent_verifications=0)),
        ("context_heldout", "context", DynamicComputeAction.EXPAND_CONTEXT, DynamicComputeAction.REASON_AGAIN, DynamicComputeState(task_difficulty=0.76, uncertainty=0.48, progress=0.50, context_pressure=0.90, remaining_reasoning_ratio=0.66)),
        ("stronger_heldout", "stronger", DynamicComputeAction.STRONGER_MODEL, DynamicComputeAction.REASON_AGAIN, DynamicComputeState(task_difficulty=0.93, uncertainty=0.68, progress=0.32, retrieval_usefulness=0.18, current_model_tier=0, remaining_reasoning_ratio=0.78)),
        ("parallel_heldout", "parallel", DynamicComputeAction.PARALLEL_CANDIDATES, DynamicComputeAction.REASON_AGAIN, DynamicComputeState(task_difficulty=0.84, uncertainty=0.55, progress=0.46, candidate_disagreement=0.90, remaining_reasoning_ratio=0.82)),
    )
    result: list[DynamicComputeBenchmarkCase] = []
    for case_id, family, expected, alternative, state in cases:
        result.append(
            DynamicComputeBenchmarkCase(
                case_id=case_id,
                scenario_family=family,
                state=state,
                expected_action=expected,
                utility_by_action=_utilities(expected, baseline_alternative=alternative),
                cost_by_action=_costs(expected),
            )
        )
    return tuple(result)


def run_reference_dynamic_compute_benchmark() -> tuple[
    LearnedDynamicComputeController,
    DynamicComputeComparisonReport,
]:
    learned = LearnedDynamicComputeController.train(
        build_reference_dynamic_compute_training_examples()
    )
    report = compare_dynamic_compute_controllers(
        DeterministicDynamicComputeController(),
        learned,
        build_reference_dynamic_compute_eval_cases(),
    )
    return learned, report
