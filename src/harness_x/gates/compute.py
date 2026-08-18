"""Deterministic compute-allocation gate over externally owned budgets."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from harness_x.config import ComputeGateConfig
from harness_x.core.contracts import ComputeBudget
from harness_x.orchestrator.budgets import BudgetDelta, BudgetUsage, exceeded_dimensions, snapshot_budget
from harness_x.telemetry import TraceRecorder

from .base import DeterministicGate, GateDecision


class ComputeAction(StrEnum):
    ALLOW = "allow"
    STOP = "stop"
    SUSPEND = "suspend"


class ComputeRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    budget: ComputeBudget
    usage: BudgetUsage
    requested: BudgetDelta = BudgetDelta()
    explicit_stop: bool = False
    completion_condition_met: bool = False


class ComputeGate(DeterministicGate):
    gate_id = "compute"

    def __init__(self, recorder: TraceRecorder, config: ComputeGateConfig):
        super().__init__(recorder, policy_version=config.policy_version)
        self.config = config

    def evaluate(self, request: ComputeRequest) -> GateDecision:
        snapshot = snapshot_budget(request.budget, request.usage)
        projected = request.usage.projected(request.requested)
        exceeded = exceeded_dimensions(request.budget, projected)

        if request.explicit_stop:
            action = ComputeAction.STOP
            reason = "explicit_stop"
        elif request.completion_condition_met:
            action = ComputeAction.STOP
            reason = "completion_condition"
        elif exceeded:
            action = ComputeAction.SUSPEND
            reason = "budget_exhausted"
        else:
            action = ComputeAction.ALLOW
            reason = "within_budget"

        remaining_after = {
            "reasoning_steps": max(
                0, request.budget.max_reasoning_steps - projected.reasoning_steps
            ),
            "tool_actions": max(
                0, request.budget.max_tool_actions - projected.tool_actions
            ),
            "output_tokens": max(
                0, request.budget.max_output_tokens - projected.output_tokens
            ),
        }

        return self._record(
            input_state=request,
            decision={
                "action": action.value,
                "reason": reason,
                "exceeded_dimensions": list(exceeded),
                "remaining_before": snapshot.remaining.model_dump(mode="json"),
                "remaining_after": remaining_after,
            },
            confidence=1.0,
            cost=0.0,
        )
