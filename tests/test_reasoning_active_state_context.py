from __future__ import annotations

from harness_x.core import ComputeBudget, GoalId, ReasoningRequest, RoutineId, TaskId
from harness_x.reasoning.context_builder import BoundedContextBuilder


def _request(*, active_goal: dict[str, object], active_state: dict[str, object]) -> ReasoningRequest:
    return ReasoningRequest(
        task_id=TaskId.new(),
        goal_id=GoalId.new(),
        routine_id=RoutineId.new(),
        instruction="test context plumbing",
        active_goal=active_goal,
        active_state=active_state,
        budget=ComputeBudget(),
    )


def test_context_preserves_active_goal_and_distinct_runtime_state() -> None:
    result = BoundedContextBuilder().build(
        _request(
            active_goal={"description": "fix the bug"},
            active_state={
                "iteration": 7,
                "verification_fresh": False,
                "dirty_since_verification": False,
            },
        )
    )

    sections = result.payload["sections"]
    assert sections["active_goal"]["data"] == {"description": "fix the bug"}
    assert sections["active_state"] == {
        "authority": "authoritative_runtime_state",
        "data": {
            "iteration": 7,
            "verification_fresh": False,
            "dirty_since_verification": False,
        },
    }
    assert '"active_state"' in result.serialized


def test_legacy_active_state_only_keeps_historical_active_goal_shape() -> None:
    result = BoundedContextBuilder().build(
        _request(active_goal={}, active_state={"legacy": "state"})
    )

    sections = result.payload["sections"]
    assert sections["active_goal"]["data"] == {"legacy": "state"}
    assert "active_state" not in sections
