"""Authoritative lifecycle, budgets, checkpoints, and scheduler hooks."""

from .budgets import BudgetDelta, BudgetSnapshot, BudgetUsage, snapshot_budget
from .modes import LEGAL_TRANSITIONS, TERMINAL_MODES, OperatingMode, can_transition
from .scheduler import BudgetNotice, CheckpointNotice, SchedulerHooks, TransitionNotice
from .session import CheckpointStore, SessionCheckpoint, TaskSession
from .state_machine import TaskOrchestrator

__all__ = [
    "BudgetDelta",
    "BudgetNotice",
    "BudgetSnapshot",
    "BudgetUsage",
    "CheckpointNotice",
    "CheckpointStore",
    "LEGAL_TRANSITIONS",
    "OperatingMode",
    "SchedulerHooks",
    "SessionCheckpoint",
    "TERMINAL_MODES",
    "TaskOrchestrator",
    "TaskSession",
    "TransitionNotice",
    "can_transition",
    "snapshot_budget",
]
