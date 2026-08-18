"""Model-assisted recommendation routines with deterministic shadow baselines.

Milestone 11 does not transfer authority to the reasoning core. These routines ask
for bounded recommendations, compare them with conservative deterministic
baselines, and return a selected recommendation. State transitions, memory writes,
tool execution, candidate promotion, and verification remain external.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.core.contracts import ReasoningRequest
from harness_x.core.events import EventType
from harness_x.core.ids import GoalId, RoutineId, TaskId
from harness_x.gates import ComputeRequest
from harness_x.orchestrator import BudgetDelta, OperatingMode
from harness_x.reasoning import ReasoningCoreError

from .base import (
    RoutineError,
    RoutineExecutionContext,
    RoutineResult,
    RoutineSpec,
    RoutineStatus,
    ScriptedRoutine,
)
from .engine import RoutineEngine


class DecisionFamily(StrEnum):
    PLANNING = "planning"
    RETRIEVAL_QUERY = "retrieval_query"
    HYPOTHESIS = "hypothesis"
    RECOVERY = "recovery"
    SEMANTIC_CANDIDATE = "semantic_candidate"
    ROUTINE_SELECTION = "routine_selection"
    EXPERIMENT = "experiment"


class RecommendationSource(StrEnum):
    BASELINE = "baseline"
    MODEL = "model"


class AssistedDecisionRequest(BaseModel):
    """One bounded decision problem evaluated against a deterministic baseline.

    ``evaluation_reference`` is evaluator-only ground truth. It is never placed in
    the model context. When it is absent, the model runs in shadow mode and the
    deterministic baseline remains selected.
    """

    model_config = ConfigDict(frozen=True)

    task_id: TaskId
    goal_id: GoalId
    family: DecisionFamily
    instruction: str = Field(min_length=1)
    problem: dict[str, Any] = Field(default_factory=dict)
    retrieved_memories: tuple[dict[str, Any], ...] = ()
    self_schema: dict[str, Any] = Field(default_factory=dict)
    available_actions: tuple[dict[str, Any], ...] = ()
    evaluation_reference: dict[str, Any] | None = None
    minimum_assisted_score: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("instruction")
    @classmethod
    def normalize_instruction(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("assisted decision instruction cannot be blank")
        return value


class DecisionEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: float | None = Field(default=None, ge=0.0, le=1.0)
    matched_checks: int = Field(default=0, ge=0)
    total_checks: int = Field(default=0, ge=0)
    invariant_violations: tuple[str, ...] = ()


class AssistedDecisionOutcome(BaseModel):
    """Measured comparison result. Selection is a recommendation, not mutation."""

    model_config = ConfigDict(frozen=True)

    family: DecisionFamily
    baseline_payload: dict[str, Any]
    assisted_payload: dict[str, Any] | None
    baseline_evaluation: DecisionEvaluation
    assisted_evaluation: DecisionEvaluation
    selected_source: RecommendationSource
    selected_payload: dict[str, Any]
    assisted_candidate_id: str | None = None
    model_error: str | None = None
    promotion_reason: str


_FORBIDDEN_AUTHORITY_KEYS = frozenset(
    {
        "candidate_id",
        "execute",
        "execution",
        "memory_write",
        "memory_writes",
        "mode",
        "operating_mode",
        "permissions",
        "provenance",
        "state_mutation",
        "verification",
        "verification_state",
    }
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _flatten(value: Any, prefix: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        if not value:
            return [(prefix, "{}")]
        items: list[tuple[str, str]] = []
        for key in sorted(value):
            items.extend(_flatten(value[key], f"{prefix}.{key}"))
        return items
    if isinstance(value, (list, tuple)):
        if not value:
            return [(prefix, "[]")]
        items = []
        for index, item in enumerate(value):
            items.extend(_flatten(item, f"{prefix}[{index}]"))
        return items
    return [(prefix, _canonical(value))]


def _violations(value: Any, path: str = "$") -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().casefold()
            child_path = f"{path}.{key}"
            if normalized in _FORBIDDEN_AUTHORITY_KEYS:
                found.append(child_path)
            found.extend(_violations(child, child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found.extend(_violations(child, f"{path}[{index}]"))
    return tuple(sorted(set(found)))


def evaluate_payload(
    payload: dict[str, Any] | None,
    reference: dict[str, Any] | None,
    *,
    extra_violations: tuple[str, ...] = (),
) -> DecisionEvaluation:
    if payload is None:
        return DecisionEvaluation(
            score=0.0 if reference is not None else None,
            invariant_violations=tuple(sorted(set(extra_violations))),
        )

    violations = tuple(
        sorted(set((*_violations(payload), *extra_violations)))
    )
    if reference is None:
        return DecisionEvaluation(
            score=None,
            invariant_violations=violations,
        )

    checks = _flatten(reference)
    actual = dict(_flatten(payload))
    matched = sum(actual.get(path) == expected for path, expected in checks)
    total = len(checks)
    score = matched / total if total else 1.0
    if violations:
        score = 0.0
    return DecisionEvaluation(
        score=score,
        matched_checks=matched,
        total_checks=total,
        invariant_violations=violations,
    )


def _text_list(problem: dict[str, Any], key: str) -> list[str]:
    value = problem.get(key)
    if not isinstance(value, (list, tuple)):
        raise RoutineError(f"assisted decision problem requires list field {key!r}")
    normalized = [str(item).strip() for item in value if str(item).strip()]
    if not normalized:
        raise RoutineError(f"assisted decision problem field {key!r} cannot be empty")
    return normalized


def deterministic_baseline(
    family: DecisionFamily,
    problem: dict[str, Any],
) -> dict[str, Any]:
    """Small conservative baseline used for shadow comparison.

    These rules are intentionally simple and stable. They are not intended to be
    globally optimal; they give each model-assisted behavior a measurable baseline
    that remains available when the model fails or adds no value.
    """

    if family == DecisionFamily.PLANNING:
        return {"steps": _text_list(problem, "candidate_steps")}

    if family == DecisionFamily.RETRIEVAL_QUERY:
        entities = sorted(set(_text_list(problem, "unresolved_entities")))
        return {"query": " ".join(entities)}

    if family == DecisionFamily.HYPOTHESIS:
        observation = _text_list(problem, "observations")[0]
        return {"hypothesis": f"investigate:{observation}"}

    if family == DecisionFamily.RECOVERY:
        option = sorted(set(_text_list(problem, "safe_options")))[0]
        return {"strategy": option}

    if family == DecisionFamily.SEMANTIC_CANDIDATE:
        claim = _text_list(problem, "verified_claims")[0]
        return {"claim": claim}

    if family == DecisionFamily.ROUTINE_SELECTION:
        routine = sorted(set(_text_list(problem, "eligible_routines")))[0]
        return {"routine": routine}

    if family == DecisionFamily.EXPERIMENT:
        experiment = sorted(set(_text_list(problem, "safe_experiments")))[0]
        return {"experiment": experiment}

    raise RoutineError(f"no deterministic baseline for decision family {family.value}")


class _AssistedRecommendationRoutine(ScriptedRoutine):
    family: DecisionFamily
    request_type = AssistedDecisionRequest

    def run(
        self,
        context: RoutineExecutionContext,
        request: AssistedDecisionRequest,
    ) -> RoutineResult:
        if request.family != self.family:
            raise RoutineError(
                f"routine {self.spec.name} requires decision family {self.family.value}"
            )
        b = context.bindings
        session = b.orchestrator.session
        if request.task_id != session.task_id:
            raise RoutineError("assisted decision request belongs to another task")
        goal = b.goals.get(request.goal_id)
        if goal.task_id != session.task_id:
            raise RoutineError("assisted decision goal belongs to another task")

        baseline = deterministic_baseline(self.family, request.problem)
        baseline_eval = evaluate_payload(baseline, request.evaluation_reference)

        requested_budget = BudgetDelta(reasoning_steps=1)
        compute = b.compute_gate.evaluate(
            ComputeRequest(
                budget=session.budget,
                usage=session.usage,
                requested=requested_budget,
            )
        )
        if compute.decision.get("action") == "suspend":
            b.orchestrator.suspend(
                f"assisted_{self.family.value}_compute_budget_exhausted"
            )
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                data={"reason": "budget_exhausted", "baseline": baseline},
            )
        if compute.decision.get("action") == "stop":
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                data={
                    "reason": compute.decision.get("reason", "compute_gate_stop"),
                    "baseline": baseline,
                },
            )
        b.orchestrator.consume_budget(
            requested_budget,
            reason=f"assisted_decision:{self.family.value}",
        )

        service = context.require_reasoning_service()
        model_error: str | None = None
        assisted_payload: dict[str, Any] | None = None
        assisted_candidate_id: str | None = None
        extra_violations: list[str] = []

        instruction = (
            f"Decision family: {self.family.value}. {request.instruction} "
            "Return exactly one recommendation in proposals[0].payload and no tool "
            "actions. This is a recommendation only; do not claim authority to mutate "
            "state, write memory, execute tools, grant permissions, or verify itself."
        )
        reasoning_request = ReasoningRequest(
            task_id=request.task_id,
            goal_id=request.goal_id,
            routine_id=self.spec.routine_id,
            instruction=instruction,
            active_goal=goal.model_dump(mode="json"),
            working_state=[
                item.model_dump(mode="json") for item in b.working.items()
            ],
            retrieved_memories=list(request.retrieved_memories),
            self_schema=request.self_schema,
            available_actions=list(request.available_actions),
            budget=session.budget,
            context={
                "decision_family": self.family.value,
                "problem": request.problem,
            },
        )

        try:
            result = service.invoke(reasoning_request)
            if result.actions:
                extra_violations.append("model_returned_action")
            if len(result.proposals) != 1:
                extra_violations.append("model_must_return_exactly_one_proposal")
            else:
                assisted_payload = dict(result.proposals[0].payload)
                assisted_candidate_id = str(result.proposals[0].candidate_id)
        except ReasoningCoreError as exc:
            model_error = str(exc)
            extra_violations.append("reasoning_core_error")

        assisted_eval = evaluate_payload(
            assisted_payload,
            request.evaluation_reference,
            extra_violations=tuple(extra_violations),
        )

        selected_source = RecommendationSource.BASELINE
        selected_payload = baseline
        promotion_reason = "baseline_retained"
        if request.evaluation_reference is None:
            promotion_reason = "shadow_only_without_reference"
        elif assisted_eval.invariant_violations:
            promotion_reason = "assisted_invariant_violation"
        elif assisted_eval.score is None or baseline_eval.score is None:
            promotion_reason = "insufficient_evaluation_evidence"
        elif assisted_eval.score < request.minimum_assisted_score:
            promotion_reason = "assisted_below_minimum_score"
        elif assisted_eval.score <= baseline_eval.score:
            promotion_reason = "assisted_did_not_beat_baseline"
        else:
            selected_source = RecommendationSource.MODEL
            selected_payload = assisted_payload or baseline
            promotion_reason = "assisted_strictly_better_than_baseline"

        outcome = AssistedDecisionOutcome(
            family=self.family,
            baseline_payload=baseline,
            assisted_payload=assisted_payload,
            baseline_evaluation=baseline_eval,
            assisted_evaluation=assisted_eval,
            selected_source=selected_source,
            selected_payload=selected_payload,
            assisted_candidate_id=assisted_candidate_id,
            model_error=model_error,
            promotion_reason=promotion_reason,
        )
        event = context.recorder.emit(
            EventType.ASSISTED_DECISION_COMPARED,
            f"routine.{self.spec.name}",
            input_refs=(str(request.goal_id),),
            output_refs=(
                (assisted_candidate_id,) if assisted_candidate_id is not None else ()
            ),
            metadata={
                "family": self.family.value,
                "baseline": baseline,
                "assisted_payload": assisted_payload,
                "baseline_evaluation": baseline_eval.model_dump(mode="json"),
                "assisted_evaluation": assisted_eval.model_dump(mode="json"),
                "selected_source": selected_source.value,
                "promotion_reason": promotion_reason,
                "model_error": model_error,
                "authoritative_mutation": False,
            },
        )
        return RoutineResult(
            status=RoutineStatus.SUCCEEDED,
            output_refs=(str(event.event_id),),
            data=outcome.model_dump(mode="json"),
        )


def _spec(
    *,
    family: DecisionFamily,
    name: str,
    routine_id: str,
) -> RoutineSpec:
    return RoutineSpec(
        routine_id=RoutineId(value=routine_id),
        name=name,
        version=f"{name}-v1",
        precondition_modes=(OperatingMode.TASK_ACTIVE, OperatingMode.RECOVERY),
        required_state_views=(
            "active_goal",
            "working_state",
            "selected_retrieved_memories",
            "grounded_self_schema",
            "compute_budget",
        ),
        allowed_tools=(),
        allowed_memory_writes=(),
        step_policy=(
            "compute_gate",
            "deterministic_baseline",
            "bounded_reasoning_recommendation",
            "deterministic_comparison",
            "return_recommendation_without_mutation",
        ),
        verification_requirements=(
            "shadow_baseline_is_always_available",
            "model_must_strictly_beat_baseline_to_be_selected",
            "authority_violations_force_baseline",
        ),
        termination_rule=(
            f"return a {family.value} recommendation only; owning subsystem retains authority"
        ),
    )


class PlanningProposalRoutine(_AssistedRecommendationRoutine):
    family = DecisionFamily.PLANNING
    spec = _spec(
        family=family,
        name="planning_proposal",
        routine_id="routine_planning_proposal_v1",
    )


class RetrievalQueryRoutine(_AssistedRecommendationRoutine):
    family = DecisionFamily.RETRIEVAL_QUERY
    spec = _spec(
        family=family,
        name="retrieval_query_proposal",
        routine_id="routine_retrieval_query_proposal_v1",
    )


class HypothesisProposalRoutine(_AssistedRecommendationRoutine):
    family = DecisionFamily.HYPOTHESIS
    spec = _spec(
        family=family,
        name="hypothesis_proposal",
        routine_id="routine_hypothesis_proposal_v1",
    )


class RecoveryProposalRoutine(_AssistedRecommendationRoutine):
    family = DecisionFamily.RECOVERY
    spec = _spec(
        family=family,
        name="recovery_proposal",
        routine_id="routine_recovery_proposal_v1",
    )


class SemanticCandidateProposalRoutine(_AssistedRecommendationRoutine):
    family = DecisionFamily.SEMANTIC_CANDIDATE
    spec = _spec(
        family=family,
        name="semantic_candidate_proposal",
        routine_id="routine_semantic_candidate_proposal_v1",
    )


class RoutineSelectionProposalRoutine(_AssistedRecommendationRoutine):
    family = DecisionFamily.ROUTINE_SELECTION
    spec = _spec(
        family=family,
        name="routine_selection_proposal",
        routine_id="routine_routine_selection_proposal_v1",
    )


class ExperimentProposalRoutine(_AssistedRecommendationRoutine):
    family = DecisionFamily.EXPERIMENT
    spec = _spec(
        family=family,
        name="experiment_proposal",
        routine_id="routine_experiment_proposal_v1",
    )


MODEL_ASSISTED_ROUTINES = (
    PlanningProposalRoutine,
    RetrievalQueryRoutine,
    HypothesisProposalRoutine,
    RecoveryProposalRoutine,
    SemanticCandidateProposalRoutine,
    RoutineSelectionProposalRoutine,
    ExperimentProposalRoutine,
)


def register_model_assisted_routines(engine: RoutineEngine) -> None:
    for routine_type in MODEL_ASSISTED_ROUTINES:
        engine.register(routine_type())
