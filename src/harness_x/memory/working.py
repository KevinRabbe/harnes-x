"""Bounded, explicit working state with deterministic eviction."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.core.errors import (
    MemoryCapacityError,
    MemoryNotFoundError,
    MemorySubsystemError,
)
from harness_x.core.events import EventType
from harness_x.core.ids import MemoryId, TaskId
from harness_x.core.provenance import Provenance
from harness_x.telemetry import TraceRecorder

from .base import MemoryClass, MemoryPressure


class WorkingItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory_id: MemoryId
    task_id: TaskId
    kind: str = Field(min_length=1)
    content: dict[str, Any] = Field(default_factory=dict)
    priority: float = Field(ge=0.0, le=1.0)
    pinned: bool = False
    size_units: int = Field(gt=0)
    source: str = Field(min_length=1)
    created_step: int = Field(ge=1)
    last_used_step: int = Field(ge=1)
    provenance: Provenance

    @field_validator("kind", "source")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("working-state text fields cannot be blank")
        return value


class WorkingState:
    """Small software-owned state used for the active task.

    Eviction is deliberately simple and deterministic: among unpinned items, evict
    lowest priority first, then least-recently used, then oldest, then ID order.
    """

    def __init__(self, recorder: TraceRecorder, *, capacity_units: int):
        if capacity_units <= 0:
            raise ValueError("working-state capacity must be positive")
        self.recorder = recorder
        self.capacity_units = capacity_units
        self._items: dict[str, WorkingItem] = {}

    @property
    def used_units(self) -> int:
        return sum(item.size_units for item in self._items.values())

    @property
    def pressure(self) -> MemoryPressure:
        return MemoryPressure.from_usage(self.capacity_units, self.used_units)

    def add(
        self,
        *,
        kind: str,
        content: dict[str, Any],
        priority: float,
        size_units: int,
        source: str,
        provenance: Provenance,
        pinned: bool = False,
        memory_id: MemoryId | None = None,
    ) -> WorkingItem:
        memory_id = memory_id or MemoryId.new()
        key = str(memory_id)
        if key in self._items:
            raise MemorySubsystemError(f"working item {memory_id} already exists")
        if size_units > self.capacity_units:
            raise MemoryCapacityError(
                f"item size {size_units} exceeds working capacity {self.capacity_units}"
            )

        required = max(0, self.used_units + size_units - self.capacity_units)
        evictions = self._evictions_for(required)
        if sum(item.size_units for item in evictions) < required:
            raise MemoryCapacityError(
                "working state cannot admit item without evicting pinned state"
            )

        for item in evictions:
            self._evict(item, reason="capacity")

        created_step = self.recorder.store.next_step(self.recorder.trace_id)
        candidate = WorkingItem(
            memory_id=memory_id,
            task_id=self.recorder.task_id,
            kind=kind,
            content=content,
            priority=priority,
            pinned=pinned,
            size_units=size_units,
            source=source,
            created_step=created_step,
            last_used_step=created_step,
            provenance=provenance,
        )
        event = self.recorder.emit(
            EventType.MEMORY_WRITTEN,
            "memory.working",
            output_refs=(key,),
            metadata={
                "memory_class": MemoryClass.WORKING.value,
                "operation": "add",
                "pressure_before": self.pressure.pressure,
                "snapshot": candidate.model_dump(mode="json"),
            },
        )
        if event.step != created_step:
            raise MemorySubsystemError("working-state step allocation changed during write")
        self._items[key] = candidate
        return candidate

    def retrieve(self, memory_id: MemoryId) -> WorkingItem:
        current = self._require(memory_id)
        event = self.recorder.emit(
            EventType.MEMORY_RETRIEVED,
            "memory.working",
            input_refs=(str(memory_id),),
            metadata={
                "memory_class": MemoryClass.WORKING.value,
                "last_used_step": current.last_used_step,
            },
        )
        updated = current.model_copy(update={"last_used_step": event.step})
        self._items[str(memory_id)] = updated
        return updated

    def set_pinned(
        self,
        memory_id: MemoryId,
        pinned: bool,
        *,
        reason: str,
    ) -> WorkingItem:
        current = self._require(memory_id)
        reason = reason.strip()
        if not reason:
            raise ValueError("pin-state reason cannot be blank")
        if current.pinned == pinned:
            return current

        updated = current.model_copy(update={"pinned": pinned})
        self.recorder.emit(
            EventType.MEMORY_WRITTEN,
            "memory.working",
            input_refs=(str(memory_id),),
            output_refs=(str(memory_id),),
            metadata={
                "memory_class": MemoryClass.WORKING.value,
                "operation": "set_pinned",
                "reason": reason,
                "snapshot": updated.model_dump(mode="json"),
            },
        )
        self._items[str(memory_id)] = updated
        return updated

    def remove(
        self,
        memory_id: MemoryId,
        *,
        reason: str,
        allow_pinned: bool = False,
    ) -> WorkingItem:
        item = self._require(memory_id)
        if item.pinned and not allow_pinned:
            raise MemoryCapacityError("pinned working state requires explicit removal authority")
        reason = reason.strip()
        if not reason:
            raise ValueError("removal reason cannot be blank")
        return self._evict(item, reason=reason)

    def get(self, memory_id: MemoryId) -> WorkingItem:
        return self._require(memory_id)

    def items(self) -> tuple[WorkingItem, ...]:
        return tuple(self._items[key] for key in sorted(self._items))

    def _evictions_for(self, required_units: int) -> list[WorkingItem]:
        if required_units <= 0:
            return []
        candidates = sorted(
            (item for item in self._items.values() if not item.pinned),
            key=lambda item: (
                item.priority,
                item.last_used_step,
                item.created_step,
                str(item.memory_id),
            ),
        )
        selected: list[WorkingItem] = []
        freed = 0
        for item in candidates:
            selected.append(item)
            freed += item.size_units
            if freed >= required_units:
                break
        return selected

    def _evict(self, item: WorkingItem, *, reason: str) -> WorkingItem:
        self.recorder.emit(
            EventType.MEMORY_EVICTED,
            "memory.working",
            input_refs=(str(item.memory_id),),
            metadata={
                "memory_class": MemoryClass.WORKING.value,
                "reason": reason,
                "snapshot": item.model_dump(mode="json"),
            },
        )
        del self._items[str(item.memory_id)]
        return item

    def _require(self, memory_id: MemoryId) -> WorkingItem:
        try:
            return self._items[str(memory_id)]
        except KeyError as exc:
            raise MemoryNotFoundError(
                f"working item {memory_id} does not exist"
            ) from exc
