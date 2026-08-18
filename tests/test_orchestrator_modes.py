import pytest

from harness_x.core.errors import InvalidTransitionError
from harness_x.orchestrator import LEGAL_TRANSITIONS, OperatingMode, can_transition


def test_terminal_modes_have_no_outgoing_transitions() -> None:
    assert LEGAL_TRANSITIONS[OperatingMode.COMPLETE] == frozenset()
    assert LEGAL_TRANSITIONS[OperatingMode.FAILED] == frozenset()


def test_key_mode_transitions_are_explicit() -> None:
    assert can_transition(OperatingMode.READY, OperatingMode.TASK_ACTIVE)
    assert can_transition(OperatingMode.TASK_ACTIVE, OperatingMode.VERIFY)
    assert can_transition(OperatingMode.TASK_ACTIVE, OperatingMode.RECOVERY)
    assert can_transition(OperatingMode.RECOVERY, OperatingMode.MAINTENANCE)
    assert can_transition(OperatingMode.MAINTENANCE, OperatingMode.CONSOLIDATION)
    assert can_transition(OperatingMode.MAINTENANCE, OperatingMode.EXPERIMENT)
    assert not can_transition(OperatingMode.READY, OperatingMode.COMPLETE)
    assert not can_transition(OperatingMode.COMPLETE, OperatingMode.TASK_ACTIVE)
