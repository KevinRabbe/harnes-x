"""Immutable authoritative task sessions and checkpoint persistence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from harness_x.core.contracts import ComputeBudget
from harness_x.core.errors import CheckpointError
from harness_x.core.ids import SystemVersion, TaskId, TraceId

from .budgets import BudgetUsage
from .modes import OperatingMode

CHECKPOINT_SCHEMA_VERSION = "1"


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


class TaskSession(BaseModel):
    """One immutable snapshot of software-owned task lifecycle state."""

    model_config = ConfigDict(frozen=True)

    task_id: TaskId
    trace_id: TraceId
    system_version: SystemVersion
    mode: OperatingMode = OperatingMode.READY
    budget: ComputeBudget = Field(default_factory=ComputeBudget)
    usage: BudgetUsage = Field(default_factory=BudgetUsage)
    parent_task_id: TaskId | None = None
    child_task_ids: tuple[TaskId, ...] = ()
    resume_mode: OperatingMode | None = None
    last_transition_reason: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_aware_time(cls, value: datetime, info) -> datetime:
        return _aware_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_relationships_and_suspend_state(self) -> "TaskSession":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        if self.parent_task_id == self.task_id:
            raise ValueError("a task cannot be its own parent")
        child_values = [str(child) for child in self.child_task_ids]
        if len(child_values) != len(set(child_values)):
            raise ValueError("child task IDs must be unique")
        if str(self.task_id) in child_values:
            raise ValueError("a task cannot be its own child")
        if self.mode == OperatingMode.SUSPENDED and self.resume_mode is None:
            raise ValueError("suspended sessions require resume_mode")
        if self.mode != OperatingMode.SUSPENDED and self.resume_mode is not None:
            raise ValueError("resume_mode is only valid while suspended")
        return self

    @property
    def is_terminal(self) -> bool:
        return self.mode in {OperatingMode.COMPLETE, OperatingMode.FAILED}


class SessionCheckpoint(BaseModel):
    """Portable authoritative snapshot used to resume a suspended task."""

    model_config = ConfigDict(frozen=True)

    checkpoint_schema_version: str = CHECKPOINT_SCHEMA_VERSION
    task_id: TaskId
    trace_id: TraceId
    system_version: SystemVersion
    mode: OperatingMode
    budget: ComputeBudget
    usage: BudgetUsage
    parent_task_id: TaskId | None = None
    child_task_ids: tuple[TaskId, ...] = ()
    resume_mode: OperatingMode | None = None
    last_transition_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    checkpointed_at: datetime
    last_event_step: int = Field(ge=0)

    @field_validator("created_at", "updated_at", "checkpointed_at")
    @classmethod
    def require_aware_time(cls, value: datetime, info) -> datetime:
        return _aware_utc(value, info.field_name)

    @model_validator(mode="after")
    def require_suspended_snapshot(self) -> "SessionCheckpoint":
        if self.checkpoint_schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("unsupported checkpoint schema version")
        if self.mode != OperatingMode.SUSPENDED:
            raise ValueError("resumable checkpoints must capture SUSPENDED mode")
        if self.resume_mode is None:
            raise ValueError("resumable checkpoints require resume_mode")
        return self

    @classmethod
    def from_session(
        cls,
        session: TaskSession,
        *,
        checkpointed_at: datetime,
        last_event_step: int,
    ) -> "SessionCheckpoint":
        if session.mode != OperatingMode.SUSPENDED:
            raise CheckpointError("checkpoint requires a suspended task")
        return cls(
            task_id=session.task_id,
            trace_id=session.trace_id,
            system_version=session.system_version,
            mode=session.mode,
            budget=session.budget,
            usage=session.usage,
            parent_task_id=session.parent_task_id,
            child_task_ids=session.child_task_ids,
            resume_mode=session.resume_mode,
            last_transition_reason=session.last_transition_reason,
            created_at=session.created_at,
            updated_at=session.updated_at,
            checkpointed_at=checkpointed_at,
            last_event_step=last_event_step,
        )

    def to_session(self) -> TaskSession:
        return TaskSession(
            task_id=self.task_id,
            trace_id=self.trace_id,
            system_version=self.system_version,
            mode=self.mode,
            budget=self.budget,
            usage=self.usage,
            parent_task_id=self.parent_task_id,
            child_task_ids=self.child_task_ids,
            resume_mode=self.resume_mode,
            last_transition_reason=self.last_transition_reason,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class CheckpointEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    envelope_schema_version: str = "1"
    checkpoint_hash: str
    checkpoint: SessionCheckpoint


def _checkpoint_hash(checkpoint: SessionCheckpoint) -> str:
    canonical = json.dumps(
        checkpoint.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class CheckpointStore:
    """Atomic file-backed checkpoint store with content-integrity verification."""

    def save(self, checkpoint: SessionCheckpoint, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        envelope = CheckpointEnvelope(
            checkpoint_hash=_checkpoint_hash(checkpoint),
            checkpoint=checkpoint,
        )
        temporary = destination.with_name(destination.name + ".tmp")
        try:
            temporary.write_text(
                envelope.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
        except OSError as exc:
            raise CheckpointError(f"failed to write checkpoint {destination}: {exc}") from exc
        return destination

    def load(self, path: str | Path) -> SessionCheckpoint:
        source = Path(path)
        try:
            raw = source.read_text(encoding="utf-8")
            envelope = CheckpointEnvelope.model_validate_json(raw)
        except (OSError, ValidationError, ValueError) as exc:
            raise CheckpointError(f"invalid checkpoint {source}: {exc}") from exc
        if envelope.envelope_schema_version != "1":
            raise CheckpointError("unsupported checkpoint envelope schema version")
        expected = _checkpoint_hash(envelope.checkpoint)
        if envelope.checkpoint_hash != expected:
            raise CheckpointError("checkpoint content hash mismatch")
        return envelope.checkpoint
