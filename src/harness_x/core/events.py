"""Stable structured events for causal observability."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ids import EventId, SystemVersion, TaskId, TraceId

EVENT_SCHEMA_VERSION = "1"


class EventType(StrEnum):
    TASK_CREATED = "task_created"
    TASK_CHILD_ADDED = "task_child_added"
    GOAL_CREATED = "goal_created"
    GOAL_UPDATED = "goal_updated"
    MODE_CHANGED = "mode_changed"
    CHECKPOINT_CREATED = "checkpoint_created"
    MEMORY_WRITTEN = "memory_written"
    MEMORY_EVICTED = "memory_evicted"
    MEMORY_RETRIEVED = "memory_retrieved"
    GATE_DECISION = "gate_decision"
    ROUTINE_STARTED = "routine_started"
    ROUTINE_FINISHED = "routine_finished"
    ACTION_PROPOSED = "action_proposed"
    TOOL_PERMISSION_CHECKED = "tool_permission_checked"
    TOOL_EXECUTION_FINISHED = "tool_execution_finished"
    ACTION_EXECUTED = "action_executed"
    OBSERVATION_RECEIVED = "observation_received"
    VERIFICATION_COMPLETED = "verification_completed"
    ERROR_RECORDED = "error_recorded"
    BUDGET_CHANGED = "budget_changed"
    CANDIDATE_CREATED = "candidate_created"
    CANDIDATE_EVALUATED = "candidate_evaluated"
    CANDIDATE_PROMOTED = "candidate_promoted"
    CANDIDATE_REJECTED = "candidate_rejected"


class TraceEvent(BaseModel):
    """One causal system event.

    Events record system decisions, evidence, references, and state-relevant facts.
    They are intentionally not a storage format for private free-form reasoning.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = EVENT_SCHEMA_VERSION
    event_id: EventId
    trace_id: TraceId
    task_id: TaskId
    step: int = Field(ge=1)
    timestamp: datetime
    event_type: EventType
    component: str = Field(min_length=1)
    system_version: SystemVersion
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def supported_schema(cls, value: str) -> str:
        if value != EVENT_SCHEMA_VERSION:
            raise ValueError(f"unsupported event schema version: {value}")
        return value

    @field_validator("timestamp")
    @classmethod
    def aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("component")
    @classmethod
    def normalized_component(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("component cannot be blank")
        return value
