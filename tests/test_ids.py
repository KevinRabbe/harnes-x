from pydantic import BaseModel

from harness_x.core.ids import GoalId, TaskId


class Envelope(BaseModel):
    task_id: TaskId
    goal_id: GoalId


def test_ids_are_prefixed_and_unique() -> None:
    first = TaskId.new()
    second = TaskId.new()
    assert first != second
    assert first.value.startswith("task_")


def test_ids_round_trip_through_json_without_losing_type_or_value() -> None:
    original = Envelope(task_id=TaskId.new(), goal_id=GoalId.new())
    restored = Envelope.model_validate_json(original.model_dump_json())
    assert restored == original
    assert isinstance(restored.task_id, TaskId)
    assert isinstance(restored.goal_id, GoalId)
