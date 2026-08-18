"""Non-authoritative scheduler hooks for lifecycle observations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from harness_x.core.ids import TaskId

from .budgets import BudgetDelta, BudgetSnapshot
from .modes import OperatingMode


class TransitionNotice(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: TaskId
    source: OperatingMode
    target: OperatingMode
    reason: str
    timestamp: datetime


class BudgetNotice(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: TaskId
    delta: BudgetDelta
    snapshot: BudgetSnapshot
    reason: str
    timestamp: datetime


class CheckpointNotice(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: TaskId
    path: str
    last_event_step: int
    timestamp: datetime


class SchedulerHooks:
    """Small observer surface for the scheduler planned in later milestones.

    Hooks can observe authoritative changes but cannot mutate the session directly.
    """

    def __init__(self) -> None:
        self._transition_hooks: list[Callable[[TransitionNotice], None]] = []
        self._budget_hooks: list[Callable[[BudgetNotice], None]] = []
        self._checkpoint_hooks: list[Callable[[CheckpointNotice], None]] = []

    def on_transition(self, callback: Callable[[TransitionNotice], None]) -> None:
        self._transition_hooks.append(callback)

    def on_budget_change(self, callback: Callable[[BudgetNotice], None]) -> None:
        self._budget_hooks.append(callback)

    def on_checkpoint(self, callback: Callable[[CheckpointNotice], None]) -> None:
        self._checkpoint_hooks.append(callback)

    def notify_transition(self, notice: TransitionNotice) -> None:
        for callback in tuple(self._transition_hooks):
            callback(notice)

    def notify_budget_change(self, notice: BudgetNotice) -> None:
        for callback in tuple(self._budget_hooks):
            callback(notice)

    def notify_checkpoint(self, notice: CheckpointNotice) -> None:
        for callback in tuple(self._checkpoint_hooks):
            callback(notice)
