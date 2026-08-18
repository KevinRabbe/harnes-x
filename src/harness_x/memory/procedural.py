"""Evidence-gated procedural memory with coexisting strategy versions."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.core.errors import MemoryNotFoundError, MemorySubsystemError
from harness_x.core.events import EventType
from harness_x.core.ids import CandidateId, EventId, MemoryId, TaskId
from harness_x.core.provenance import Provenance
from harness_x.telemetry import TraceRecorder

from .base import MemoryClass
from .episodic import EpisodeOutcome, EpisodicMemory


class ProcedureState(StrEnum):
    CANDIDATE = "candidate"
    EVALUATED = "evaluated"
    ACTIVE = "active"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


class ProcedureRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory_id: MemoryId
    candidate_id: CandidateId
    task_id: TaskId
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    steps: tuple[str, ...]
    task_categories: tuple[str, ...] = ()
    provenance: Provenance
    source_episode_ids: tuple[MemoryId, ...]
    source_provenance: tuple[Provenance, ...]
    state: ProcedureState = ProcedureState.CANDIDATE
    evaluation_accepted: bool | None = None
    evaluation_refs: tuple[str, ...] = ()
    usage_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    total_cost: float = Field(default=0.0, ge=0.0)
    known_failure_modes: tuple[str, ...] = ()
    invalidation_reason: str | None = None
    revision: int = Field(default=1, ge=1)

    @field_validator("name", "version")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("procedure identity cannot be blank")
        return value

    @field_validator("steps")
    @classmethod
    def require_steps(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if not normalized or any(not value for value in normalized):
            raise ValueError("procedure requires non-blank steps")
        return normalized

    @property
    def success_rate(self) -> float | None:
        return None if self.usage_count == 0 else self.success_count / self.usage_count

    @property
    def average_cost(self) -> float | None:
        return None if self.usage_count == 0 else self.total_cost / self.usage_count


class ProcedureHistoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory_id: MemoryId
    revision: int = Field(ge=1)
    state: ProcedureState
    usage_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    total_cost: float = Field(ge=0.0)
    event_id: EventId
    step: int = Field(ge=1)
    timestamp: datetime
    reason: str


class ProceduralMemory:
    def __init__(self, recorder: TraceRecorder, episodic: EpisodicMemory):
        if recorder is not episodic.recorder:
            raise MemorySubsystemError("procedural and episodic memory must share one recorder")
        self.recorder = recorder
        self.episodic = episodic
        self._records: dict[str, ProcedureRecord] = {}
        self._history: dict[str, list[ProcedureHistoryEntry]] = {}
        self._candidate_to_memory: dict[str, str] = {}
        self._identity: dict[tuple[str, str], str] = {}

    def create_candidate(
        self,
        *,
        name: str,
        version: str,
        steps: tuple[str, ...],
        source_episode_ids: tuple[MemoryId, ...],
        provenance: Provenance,
        task_categories: tuple[str, ...] = (),
        candidate_id: CandidateId | None = None,
        memory_id: MemoryId | None = None,
    ) -> ProcedureRecord:
        if len(source_episode_ids) < 2:
            raise ValueError("procedural candidate requires at least two source episodes")
        episodes = tuple(self.episodic.get(item) for item in source_episode_ids)
        if any(episode.task_id != self.recorder.task_id for episode in episodes):
            raise MemorySubsystemError("procedure source episode belongs to another task")
        if any(episode.outcome != EpisodeOutcome.SUCCESS for episode in episodes):
            raise ValueError("procedural candidate requires successful source episodes")

        record = ProcedureRecord(
            memory_id=memory_id or MemoryId.new(),
            candidate_id=candidate_id or CandidateId.new(),
            task_id=self.recorder.task_id,
            name=name,
            version=version,
            steps=steps,
            task_categories=tuple(dict.fromkeys(x.strip() for x in task_categories if x.strip())),
            provenance=provenance,
            source_episode_ids=source_episode_ids,
            source_provenance=tuple(episode.provenance for episode in episodes),
        )
        memory_key = str(record.memory_id)
        candidate_key = str(record.candidate_id)
        identity = (record.name.casefold(), record.version.casefold())
        if memory_key in self._records or candidate_key in self._candidate_to_memory:
            raise MemorySubsystemError("procedure memory/candidate identifier already exists")
        if identity in self._identity:
            raise MemorySubsystemError(f"procedure {record.name!r} version {record.version!r} already exists")

        event = self.recorder.emit(
            EventType.CANDIDATE_CREATED,
            "memory.procedural",
            input_refs=tuple(str(item) for item in source_episode_ids),
            output_refs=(candidate_key,),
            metadata={"candidate_kind": "procedure", "memory_id": memory_key, "name": record.name, "version": record.version},
        )
        self.recorder.emit(
            EventType.MEMORY_WRITTEN,
            "memory.procedural",
            input_refs=(candidate_key, *tuple(str(item) for item in source_episode_ids)),
            output_refs=(memory_key,),
            metadata={"memory_class": MemoryClass.PROCEDURAL.value, "operation": "candidate_created", "snapshot": record.model_dump(mode="json")},
        )
        self._records[memory_key] = record
        self._candidate_to_memory[candidate_key] = memory_key
        self._identity[identity] = memory_key
        self._history[memory_key] = [self._history_entry(record, event, "candidate_created")]
        return record

    def evaluate(self, memory_id: MemoryId, *, accepted: bool, evidence_refs: tuple[str, ...], reason: str) -> ProcedureRecord:
        current = self._require(memory_id)
        if current.state != ProcedureState.CANDIDATE:
            raise MemorySubsystemError("only a procedure candidate can be evaluated")
        refs = self._evidence(evidence_refs)
        reason = self._reason(reason)
        updated = current.model_copy(update={
            "state": ProcedureState.EVALUATED if accepted else ProcedureState.REJECTED,
            "evaluation_accepted": accepted,
            "evaluation_refs": refs,
            "revision": current.revision + 1,
        })
        event = self.recorder.emit(
            EventType.CANDIDATE_EVALUATED,
            "memory.procedural",
            input_refs=(str(current.candidate_id), *refs),
            output_refs=(str(current.candidate_id),),
            metadata={"candidate_kind": "procedure", "accepted": accepted, "memory_id": str(memory_id), "reason": reason},
        )
        self._commit(current, updated, event, reason)
        if not accepted:
            self.recorder.emit(
                EventType.CANDIDATE_REJECTED,
                "memory.procedural",
                input_refs=(str(current.candidate_id), *refs),
                output_refs=(str(current.candidate_id),),
                metadata={"candidate_kind": "procedure", "memory_id": str(memory_id), "reason": reason},
            )
        return updated

    def promote(self, memory_id: MemoryId, *, reason: str) -> ProcedureRecord:
        current = self._require(memory_id)
        if current.state != ProcedureState.EVALUATED or current.evaluation_accepted is not True:
            raise MemorySubsystemError("procedure promotion requires an accepted evaluation")
        reason = self._reason(reason)
        updated = current.model_copy(update={"state": ProcedureState.ACTIVE, "revision": current.revision + 1})
        event = self.recorder.emit(
            EventType.CANDIDATE_PROMOTED,
            "memory.procedural",
            input_refs=(str(current.candidate_id), *current.evaluation_refs),
            output_refs=(str(current.candidate_id),),
            metadata={"candidate_kind": "procedure", "memory_id": str(memory_id), "name": current.name, "version": current.version, "reason": reason},
        )
        self._commit(current, updated, event, reason)
        return updated

    def record_usage(self, memory_id: MemoryId, *, success: bool, cost: float, task_category: str, failure_mode: str | None = None) -> ProcedureRecord:
        current = self._require(memory_id)
        if current.state != ProcedureState.ACTIVE:
            raise MemorySubsystemError("only active procedures can record usage")
        if cost < 0:
            raise ValueError("procedure usage cost cannot be negative")
        category = task_category.strip()
        if not category:
            raise ValueError("procedure usage requires a task category")
        failure = failure_mode.strip() if failure_mode else None
        if not success and not failure:
            raise ValueError("failed procedure usage requires a failure mode")
        updated = current.model_copy(update={
            "task_categories": tuple(dict.fromkeys((*current.task_categories, category))),
            "usage_count": current.usage_count + 1,
            "success_count": current.success_count + int(success),
            "failure_count": current.failure_count + int(not success),
            "total_cost": current.total_cost + cost,
            "known_failure_modes": tuple(dict.fromkeys((*current.known_failure_modes, *((failure,) if failure else ())))),
            "revision": current.revision + 1,
        })
        event = self.recorder.emit(
            EventType.MEMORY_WRITTEN,
            "memory.procedural",
            input_refs=(str(current.memory_id),),
            output_refs=(str(current.memory_id),),
            metadata={"memory_class": MemoryClass.PROCEDURAL.value, "operation": "usage_recorded", "success": success, "cost": cost, "task_category": category, "failure_mode": failure, "snapshot": updated.model_dump(mode="json")},
        )
        self._commit(current, updated, event, "usage_recorded", emit_memory=False)
        return updated

    def invalidate(self, memory_id: MemoryId, *, evidence_refs: tuple[str, ...], reason: str) -> ProcedureRecord:
        current = self._require(memory_id)
        if current.state != ProcedureState.ACTIVE:
            raise MemorySubsystemError("only active procedures can be invalidated")
        refs = self._evidence(evidence_refs)
        reason = self._reason(reason)
        updated = current.model_copy(update={
            "state": ProcedureState.INVALIDATED,
            "evaluation_refs": tuple(dict.fromkeys((*current.evaluation_refs, *refs))),
            "invalidation_reason": reason,
            "revision": current.revision + 1,
        })
        event = self.recorder.emit(
            EventType.CANDIDATE_INVALIDATED,
            "memory.procedural",
            input_refs=(str(current.candidate_id), *refs),
            output_refs=(str(current.candidate_id),),
            metadata={"candidate_kind": "procedure", "memory_id": str(memory_id), "reason": reason},
        )
        self._commit(current, updated, event, reason)
        return updated

    def get(self, memory_id: MemoryId) -> ProcedureRecord:
        return self._require(memory_id)

    def versions(self, name: str, *, active_only: bool = False) -> tuple[ProcedureRecord, ...]:
        key = name.strip().casefold()
        records = [record for record in self._records.values() if record.name.casefold() == key]
        if active_only:
            records = [record for record in records if record.state == ProcedureState.ACTIVE]
        return tuple(sorted(records, key=lambda record: (record.version.casefold(), str(record.memory_id))))

    def all(self) -> tuple[ProcedureRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    def history(self, memory_id: MemoryId) -> tuple[ProcedureHistoryEntry, ...]:
        self._require(memory_id)
        return tuple(self._history[str(memory_id)])

    def _require(self, memory_id: MemoryId) -> ProcedureRecord:
        try:
            return self._records[str(memory_id)]
        except KeyError as exc:
            raise MemoryNotFoundError(f"procedure memory {memory_id} does not exist") from exc

    @staticmethod
    def _reason(reason: str) -> str:
        value = reason.strip()
        if not value:
            raise ValueError("procedure memory reason cannot be blank")
        return value

    @staticmethod
    def _evidence(refs: tuple[str, ...]) -> tuple[str, ...]:
        values = tuple(ref.strip() for ref in refs)
        if not values or any(not ref for ref in values):
            raise ValueError("procedure evaluation requires evidence refs")
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _history_entry(record: ProcedureRecord, event, reason: str) -> ProcedureHistoryEntry:
        return ProcedureHistoryEntry(
            memory_id=record.memory_id,
            revision=record.revision,
            state=record.state,
            usage_count=record.usage_count,
            success_count=record.success_count,
            failure_count=record.failure_count,
            total_cost=record.total_cost,
            event_id=event.event_id,
            step=event.step,
            timestamp=event.timestamp,
            reason=reason,
        )

    def _commit(self, previous: ProcedureRecord, updated: ProcedureRecord, event, reason: str, *, emit_memory: bool = True) -> None:
        self._records[str(updated.memory_id)] = updated
        self._history[str(updated.memory_id)].append(self._history_entry(updated, event, reason))
        if emit_memory:
            self.recorder.emit(
                EventType.MEMORY_WRITTEN,
                "memory.procedural",
                input_refs=(str(previous.memory_id), str(previous.candidate_id)),
                output_refs=(str(updated.memory_id),),
                metadata={"memory_class": MemoryClass.PROCEDURAL.value, "operation": "revision", "previous_revision": previous.revision, "snapshot": updated.model_dump(mode="json")},
            )
