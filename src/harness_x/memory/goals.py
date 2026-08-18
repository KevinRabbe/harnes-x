"""Authoritative goal and governing-constraint memory."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.core.errors import GoalTransitionError, MemoryNotFoundError
from harness_x.core.events import EventType
from harness_x.core.ids import EventId, GoalId, TaskId
from harness_x.core.provenance import Provenance
from harness_x.telemetry import TraceRecorder

from .base import MemoryClass


class GoalStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_GOAL_STATUSES = {
    GoalStatus.COMPLETE,
    GoalStatus.FAILED,
    GoalStatus.CANCELLED,
}

LEGAL_GOAL_TRANSITIONS: dict[GoalStatus, frozenset[GoalStatus]] = {
    GoalStatus.ACTIVE: frozenset(
        {
            GoalStatus.BLOCKED,
            GoalStatus.COMPLETE,
            GoalStatus.FAILED,
            GoalStatus.CANCELLED,
        }
    ),
    GoalStatus.BLOCKED: frozenset(
        {
            GoalStatus.ACTIVE,
            GoalStatus.COMPLETE,
            GoalStatus.FAILED,
            GoalStatus.CANCELLED,
        }
    ),
    GoalStatus.COMPLETE: frozenset(),
    GoalStatus.FAILED: frozenset(),
    GoalStatus.CANCELLED: frozenset(),
}


class GoalConstraint(BaseModel):
    """A governing rule that remains outside model prompt retention."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    pinned: bool = True

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("constraint text cannot be blank")
        return value

    @field_validator("pinned")
    @classmethod
    def governing_constraints_are_pinned(cls, value: bool) -> bool:
        if not value:
            raise ValueError("governing goal constraints cannot be unpinned")
        return value


class Goal(BaseModel):
    model_config = ConfigDict(frozen=True)

    goal_id: GoalId
    task_id: TaskId
    title: str = Field(min_length=1)
    parent_goal_id: GoalId | None = None
    status: GoalStatus = GoalStatus.ACTIVE
    governing_constraints: tuple[GoalConstraint, ...] = ()
    completion_criteria: tuple[str, ...] = ()
    provenance: Provenance
    revision: int = Field(default=1, ge=1)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("goal title cannot be blank")
        return value

    @field_validator("completion_criteria")
    @classmethod
    def normalize_criteria(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("completion criteria cannot contain blank entries")
        return normalized


class GoalHistoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    goal_id: GoalId
    revision: int = Field(ge=1)
    status: GoalStatus
    event_id: EventId
    step: int = Field(ge=1)
    timestamp: datetime
    reason: str


class GoalMemory:
    """Software-owned goals with explicit history and causal trace events."""

    def __init__(self, recorder: TraceRecorder):
        self.recorder = recorder
        self._goals: dict[str, Goal] = {}
        self._history: dict[str, list[GoalHistoryEntry]] = {}

    def create_goal(
        self,
        title: str,
        provenance: Provenance,
        *,
        parent_goal_id: GoalId | None = None,
        governing_constraints: tuple[str, ...] = (),
        completion_criteria: tuple[str, ...] = (),
        goal_id: GoalId | None = None,
    ) -> Goal:
        if parent_goal_id is not None:
            self._require(parent_goal_id)

        candidate = Goal(
            goal_id=goal_id or GoalId.new(),
            task_id=self.recorder.task_id,
            title=title,
            parent_goal_id=parent_goal_id,
            governing_constraints=tuple(
                GoalConstraint(text=text) for text in governing_constraints
            ),
            completion_criteria=completion_criteria,
            provenance=provenance,
        )
        key = str(candidate.goal_id)
        if key in self._goals:
            raise GoalTransitionError(f"goal {candidate.goal_id} already exists")

        event = self.recorder.emit(
            EventType.GOAL_CREATED,
            "memory.goals",
            input_refs=(str(parent_goal_id),) if parent_goal_id else (),
            output_refs=(key,),
            metadata={
                "memory_class": MemoryClass.GOAL.value,
                "status": candidate.status.value,
                "revision": candidate.revision,
                "snapshot": candidate.model_dump(mode="json"),
            },
        )
        self._goals[key] = candidate
        self._history[key] = [
            GoalHistoryEntry(
                goal_id=candidate.goal_id,
                revision=candidate.revision,
                status=candidate.status,
                event_id=event.event_id,
                step=event.step,
                timestamp=event.timestamp,
                reason="created",
            )
        ]
        return candidate

    def create_subgoal(
        self,
        parent_goal_id: GoalId,
        title: str,
        provenance: Provenance,
        **kwargs: object,
    ) -> Goal:
        return self.create_goal(
            title,
            provenance,
            parent_goal_id=parent_goal_id,
            **kwargs,
        )

    def update_status(
        self,
        goal_id: GoalId,
        status: GoalStatus,
        *,
        reason: str,
    ) -> Goal:
        current = self._require(goal_id)
        if status == current.status:
            return current
        if status not in LEGAL_GOAL_TRANSITIONS[current.status]:
            raise GoalTransitionError(
                f"illegal goal transition {current.status.value} -> {status.value}"
            )

        updated = current.model_copy(
            update={"status": status, "revision": current.revision + 1}
        )
        return self._commit_update(current, updated, reason)

    def add_constraint(self, goal_id: GoalId, text: str, *, reason: str) -> Goal:
        current = self._require_mutable(goal_id)
        constraint = GoalConstraint(text=text)
        updated = current.model_copy(
            update={
                "governing_constraints": current.governing_constraints + (constraint,),
                "revision": current.revision + 1,
            }
        )
        return self._commit_update(current, updated, reason)

    def add_completion_criterion(
        self,
        goal_id: GoalId,
        criterion: str,
        *,
        reason: str,
    ) -> Goal:
        current = self._require_mutable(goal_id)
        criterion = criterion.strip()
        if not criterion:
            raise ValueError("completion criterion cannot be blank")
        updated = current.model_copy(
            update={
                "completion_criteria": current.completion_criteria + (criterion,),
                "revision": current.revision + 1,
            }
        )
        return self._commit_update(current, updated, reason)

    def get(self, goal_id: GoalId) -> Goal:
        return self._require(goal_id)

    def retrieve(self, goal_id: GoalId) -> Goal:
        goal = self._require(goal_id)
        self.recorder.emit(
            EventType.MEMORY_RETRIEVED,
            "memory.goals",
            input_refs=(str(goal_id),),
            metadata={
                "memory_class": MemoryClass.GOAL.value,
                "revision": goal.revision,
            },
        )
        return goal

    def history(self, goal_id: GoalId) -> tuple[GoalHistoryEntry, ...]:
        self._require(goal_id)
        return tuple(self._history[str(goal_id)])

    def all(self) -> tuple[Goal, ...]:
        return tuple(self._goals[key] for key in sorted(self._goals))

    def _commit_update(self, current: Goal, updated: Goal, reason: str) -> Goal:
        reason = reason.strip()
        if not reason:
            raise ValueError("goal update reason cannot be blank")

        event = self.recorder.emit(
            EventType.GOAL_UPDATED,
            "memory.goals",
            input_refs=(str(current.goal_id),),
            output_refs=(str(updated.goal_id),),
            metadata={
                "memory_class": MemoryClass.GOAL.value,
                "status": updated.status.value,
                "revision": updated.revision,
                "reason": reason,
                "snapshot": updated.model_dump(mode="json"),
            },
        )
        key = str(updated.goal_id)
        self._goals[key] = updated
        self._history[key].append(
            GoalHistoryEntry(
                goal_id=updated.goal_id,
                revision=updated.revision,
                status=updated.status,
                event_id=event.event_id,
                step=event.step,
                timestamp=event.timestamp,
                reason=reason,
            )
        )
        return updated

    def _require(self, goal_id: GoalId) -> Goal:
        try:
            return self._goals[str(goal_id)]
        except KeyError as exc:
            raise MemoryNotFoundError(f"goal {goal_id} does not exist") from exc

    def _require_mutable(self, goal_id: GoalId) -> Goal:
        goal = self._require(goal_id)
        if goal.status in TERMINAL_GOAL_STATUSES:
            raise GoalTransitionError(
                f"terminal goal {goal_id} cannot be structurally modified"
            )
        return goal
