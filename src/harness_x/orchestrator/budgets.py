"""Externally enforced task-budget accounting."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from harness_x.core.contracts import ComputeBudget


class BudgetDelta(BaseModel):
    model_config = ConfigDict(frozen=True)

    reasoning_steps: int = Field(default=0, ge=0)
    tool_actions: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class BudgetUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    reasoning_steps: int = Field(default=0, ge=0)
    tool_actions: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    def projected(self, delta: BudgetDelta) -> "BudgetUsage":
        return BudgetUsage(
            reasoning_steps=self.reasoning_steps + delta.reasoning_steps,
            tool_actions=self.tool_actions + delta.tool_actions,
            output_tokens=self.output_tokens + delta.output_tokens,
        )


class BudgetSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    limits: ComputeBudget
    usage: BudgetUsage
    remaining: BudgetUsage


def exceeded_dimensions(
    budget: ComputeBudget,
    projected: BudgetUsage,
) -> tuple[str, ...]:
    exceeded: list[str] = []
    if projected.reasoning_steps > budget.max_reasoning_steps:
        exceeded.append("reasoning_steps")
    if projected.tool_actions > budget.max_tool_actions:
        exceeded.append("tool_actions")
    if projected.output_tokens > budget.max_output_tokens:
        exceeded.append("output_tokens")
    return tuple(exceeded)


def snapshot_budget(budget: ComputeBudget, usage: BudgetUsage) -> BudgetSnapshot:
    return BudgetSnapshot(
        limits=budget,
        usage=usage,
        remaining=BudgetUsage(
            reasoning_steps=max(0, budget.max_reasoning_steps - usage.reasoning_steps),
            tool_actions=max(0, budget.max_tool_actions - usage.tool_actions),
            output_tokens=max(0, budget.max_output_tokens - usage.output_tokens),
        ),
    )
