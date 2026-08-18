"""Deterministic fault injections for grounded self-model curriculum generation.

Faults are explicit simulator interventions. The visible input contains symptoms and
telemetry, while the ground-truth diagnosis is kept in generator metadata/labels.
No model is used to invent either the fault or its expected diagnosis.
"""

from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from harness_x.telemetry.self_schema import SystemSelfSchema


class FaultFamily(StrEnum):
    WORKING_PRESSURE = "working_pressure"
    SEMANTIC_CONFLICT = "semantic_conflict"
    GOAL_BLOCKED = "goal_blocked"
    TOOL_FAILURE_LOOP = "tool_failure_loop"
    VERIFICATION_REJECTION = "verification_rejection"
    BUDGET_EXHAUSTION = "budget_exhaustion"
    MAINTENANCE_DUE = "maintenance_due"


class InjectedFaultCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    fault_family: FaultFamily
    visible_state: dict[str, Any]
    expected_diagnosis: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]
    uncertainty: str
    safe_next_experiment: dict[str, Any]


def _memory(state: dict[str, Any], memory_class: str) -> dict[str, Any]:
    for item in state.get("memories", []):
        if item.get("memory_class") == memory_class:
            return item
    raise ValueError(f"self-schema has no {memory_class!r} memory description")


def _metrics(state: dict[str, Any]) -> dict[str, Any]:
    metrics = state.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("self-schema metrics are missing")
    return metrics


def inject_fault(
    schema: SystemSelfSchema,
    family: FaultFamily,
) -> InjectedFaultCase:
    """Return an observable faulted state plus simulator-known ground truth."""

    state = deepcopy(schema.model_dump(mode="json"))
    # The original state fingerprint identifies the source snapshot; it is not
    # recomputed because the faulted state is a training simulation, not live state.
    metrics = _metrics(state)

    if family == FaultFamily.WORKING_PRESSURE:
        working = _memory(state, "working")
        capacity = int(working.get("capacity_units") or 1)
        working["used_units"] = capacity
        working["utilization"] = 1.0
        working["pressure"] = 1.0
        metrics["working_pressure"] = 1.0
        return InjectedFaultCase(
            fault_family=family,
            visible_state=state,
            expected_diagnosis={
                "observed_symptom": "working memory is saturated",
                "likely_component": "memory.working",
                "recommended_control": "maintenance_gate",
            },
            evidence=(
                {"path": "memories[working].pressure", "value": 1.0},
                {"path": "metrics.working_pressure", "value": 1.0},
            ),
            uncertainty="low",
            safe_next_experiment={
                "action": "run_maintenance_on_copy",
                "measure": "working_pressure_and_evictions",
            },
        )

    if family == FaultFamily.SEMANTIC_CONFLICT:
        semantic = _memory(state, "semantic")
        semantic["contradiction_count"] = max(
            1, int(semantic.get("contradiction_count") or 0)
        )
        metrics["semantic_contradictions"] = max(
            1, int(metrics.get("semantic_contradictions") or 0)
        )
        state["retrieved_evidence"] = [
            {"claim": "endpoint=alpha", "verification": "verified"},
            {"claim": "endpoint=beta", "verification": "verified"},
        ]
        return InjectedFaultCase(
            fault_family=family,
            visible_state=state,
            expected_diagnosis={
                "observed_symptom": "verified semantic claims conflict",
                "likely_component": "memory.semantic",
                "recommended_control": "verification",
            },
            evidence=(
                {"path": "metrics.semantic_contradictions", "minimum": 1},
                {"path": "retrieved_evidence", "relationship": "contradiction"},
            ),
            uncertainty="low",
            safe_next_experiment={
                "action": "reverify_claims_against_sources",
                "preserve_both_claims": True,
            },
        )

    if family == FaultFamily.GOAL_BLOCKED:
        state["goal_state"] = {
            "status": "blocked",
            "blocking_reason": "required_dependency_missing",
            "governing_constraints_present": True,
        }
        return InjectedFaultCase(
            fault_family=family,
            visible_state=state,
            expected_diagnosis={
                "observed_symptom": "active goal cannot currently progress",
                "likely_component": "memory.goal",
                "recommended_control": "planning_or_recovery",
            },
            evidence=(
                {"path": "goal_state.status", "value": "blocked"},
                {
                    "path": "goal_state.blocking_reason",
                    "value": "required_dependency_missing",
                },
            ),
            uncertainty="low",
            safe_next_experiment={
                "action": "resolve_dependency_in_sandbox",
                "measure": "goal_unblocked_without_constraint_loss",
            },
        )

    if family == FaultFamily.TOOL_FAILURE_LOOP:
        errors = list(state.get("recent_errors", []))
        errors.extend(
            [
                {
                    "memory_id": "simulated_tool_failure_1",
                    "severity": "error",
                    "status": "open",
                    "anomaly": "tool unreliable_tool failed attempt 1",
                    "revision": 1,
                },
                {
                    "memory_id": "simulated_tool_failure_2",
                    "severity": "error",
                    "status": "open",
                    "anomaly": "tool unreliable_tool failed attempt 2",
                    "revision": 1,
                },
            ]
        )
        state["recent_errors"] = errors[-10:]
        metrics["unresolved_errors"] = max(2, int(metrics.get("unresolved_errors") or 0))
        return InjectedFaultCase(
            fault_family=family,
            visible_state=state,
            expected_diagnosis={
                "observed_symptom": "same tool path is failing repeatedly",
                "likely_component": "tool.unreliable_tool",
                "recommended_control": "recovery",
            },
            evidence=(
                {"path": "recent_errors", "repeated_tool_failures": 2},
                {"path": "metrics.unresolved_errors", "minimum": 2},
            ),
            uncertainty="medium",
            safe_next_experiment={
                "action": "try_declared_alternative_in_sandbox",
                "do_not_repeat_identical_side_effect": True,
            },
        )

    if family == FaultFamily.VERIFICATION_REJECTION:
        metrics["verifier_checks"] = max(1, int(metrics.get("verifier_checks") or 0))
        metrics["verifier_rejections"] = metrics["verifier_checks"]
        metrics["verifier_rejection_rate"] = 1.0
        state["last_verification"] = {
            "accepted": False,
            "reason": "observed output does not match required result",
        }
        return InjectedFaultCase(
            fault_family=family,
            visible_state=state,
            expected_diagnosis={
                "observed_symptom": "proposal failed independent verification",
                "likely_component": "routine.verification",
                "recommended_control": "recovery_or_replan",
            },
            evidence=(
                {"path": "last_verification.accepted", "value": False},
                {"path": "metrics.verifier_rejection_rate", "value": 1.0},
            ),
            uncertainty="low",
            safe_next_experiment={
                "action": "compare_expected_and_observed_then_replan",
                "reuse_rejected_result_as_truth": False,
            },
        )

    if family == FaultFamily.BUDGET_EXHAUSTION:
        budget = state.get("budget", {})
        usage = state.get("budget_usage", {})
        max_steps = int(budget.get("max_reasoning_steps", 1))
        usage["reasoning_steps"] = max_steps
        state["budget_usage"] = usage
        return InjectedFaultCase(
            fault_family=family,
            visible_state=state,
            expected_diagnosis={
                "observed_symptom": "reasoning-step budget is exhausted",
                "likely_component": "orchestrator",
                "recommended_control": "suspend",
            },
            evidence=(
                {
                    "path": "budget_usage.reasoning_steps",
                    "equals_path": "budget.max_reasoning_steps",
                },
            ),
            uncertainty="low",
            safe_next_experiment={
                "action": "checkpoint_and_suspend",
                "request_more_compute_only_external_to_core": True,
            },
        )

    if family == FaultFamily.MAINTENANCE_DUE:
        metrics["working_pressure"] = 0.95
        metrics["unresolved_errors"] = max(3, int(metrics.get("unresolved_errors") or 0))
        working = _memory(state, "working")
        working["pressure"] = 0.95
        working["utilization"] = max(0.95, float(working.get("utilization") or 0.0))
        return InjectedFaultCase(
            fault_family=family,
            visible_state=state,
            expected_diagnosis={
                "observed_symptom": "multiple maintenance pressure signals are active",
                "likely_component": "gate.maintenance",
                "recommended_control": "maintenance",
            },
            evidence=(
                {"path": "metrics.working_pressure", "value": 0.95},
                {"path": "metrics.unresolved_errors", "minimum": 3},
            ),
            uncertainty="low",
            safe_next_experiment={
                "action": "enter_interruptible_maintenance",
                "measure": "pressure_errors_and_task_resumability",
            },
        )

    raise ValueError(f"unsupported fault family: {family}")
