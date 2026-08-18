"""Explicit operating modes and legal lifecycle transitions."""

from __future__ import annotations

from enum import StrEnum


class OperatingMode(StrEnum):
    READY = "ready"
    TASK_ACTIVE = "task_active"
    VERIFY = "verify"
    RECOVERY = "recovery"
    MAINTENANCE = "maintenance"
    CONSOLIDATION = "consolidation"
    EXPERIMENT = "experiment"
    SUSPENDED = "suspended"
    COMPLETE = "complete"
    FAILED = "failed"


TERMINAL_MODES = frozenset({OperatingMode.COMPLETE, OperatingMode.FAILED})

LEGAL_TRANSITIONS: dict[OperatingMode, frozenset[OperatingMode]] = {
    OperatingMode.READY: frozenset(
        {OperatingMode.TASK_ACTIVE, OperatingMode.FAILED}
    ),
    OperatingMode.TASK_ACTIVE: frozenset(
        {
            OperatingMode.VERIFY,
            OperatingMode.RECOVERY,
            OperatingMode.MAINTENANCE,
            OperatingMode.SUSPENDED,
            OperatingMode.COMPLETE,
            OperatingMode.FAILED,
        }
    ),
    OperatingMode.VERIFY: frozenset(
        {
            OperatingMode.TASK_ACTIVE,
            OperatingMode.RECOVERY,
            OperatingMode.SUSPENDED,
            OperatingMode.COMPLETE,
            OperatingMode.FAILED,
        }
    ),
    OperatingMode.RECOVERY: frozenset(
        {
            OperatingMode.TASK_ACTIVE,
            OperatingMode.MAINTENANCE,
            OperatingMode.SUSPENDED,
            OperatingMode.FAILED,
        }
    ),
    OperatingMode.MAINTENANCE: frozenset(
        {
            OperatingMode.TASK_ACTIVE,
            OperatingMode.CONSOLIDATION,
            OperatingMode.EXPERIMENT,
            OperatingMode.SUSPENDED,
            OperatingMode.FAILED,
        }
    ),
    OperatingMode.CONSOLIDATION: frozenset(
        {
            OperatingMode.MAINTENANCE,
            OperatingMode.TASK_ACTIVE,
            OperatingMode.SUSPENDED,
            OperatingMode.FAILED,
        }
    ),
    OperatingMode.EXPERIMENT: frozenset(
        {
            OperatingMode.MAINTENANCE,
            OperatingMode.TASK_ACTIVE,
            OperatingMode.SUSPENDED,
            OperatingMode.FAILED,
        }
    ),
    OperatingMode.SUSPENDED: frozenset(
        {
            OperatingMode.TASK_ACTIVE,
            OperatingMode.VERIFY,
            OperatingMode.RECOVERY,
            OperatingMode.MAINTENANCE,
            OperatingMode.CONSOLIDATION,
            OperatingMode.EXPERIMENT,
            OperatingMode.FAILED,
        }
    ),
    OperatingMode.COMPLETE: frozenset(),
    OperatingMode.FAILED: frozenset(),
}


def can_transition(source: OperatingMode, target: OperatingMode) -> bool:
    return target in LEGAL_TRANSITIONS[source]
