"""Compact structured episodic memory referencing raw causal traces."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from harness_x.core.errors import MemoryNotFoundError, MemorySubsystemError
from harness_x.core.events import EventType
from harness_x.core.ids import MemoryId, TaskId, TraceId
from harness_x.core.provenance import Provenance
from harness_x.telemetry import TraceRecorder

from .base import MemoryClass


class EpisodeOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class Episode(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory_id: MemoryId
    task_id: TaskId
    trace_id: TraceId
    start_step: int = Field(ge=1)
    end_step: int = Field(ge=1)
    summary: str = Field(min_length=1)
    outcome: EpisodeOutcome = EpisodeOutcome.UNKNOWN
    tags: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("episode summary cannot be blank")
        return value

    @field_validator("tags", "entities")
    @classmethod
    def normalize_labels(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("episode labels cannot contain blank entries")
        return normalized

    @model_validator(mode="after")
    def ordered_steps(self) -> "Episode":
        if self.end_step < self.start_step:
            raise ValueError("episode end_step cannot precede start_step")
        return self


class EpisodicMemory:
    """Baseline episode store with deterministic metadata/full-text retrieval."""

    def __init__(self, recorder: TraceRecorder):
        self.recorder = recorder
        self._episodes: dict[str, Episode] = {}

    def record(
        self,
        *,
        start_step: int,
        end_step: int,
        summary: str,
        provenance: Provenance,
        outcome: EpisodeOutcome = EpisodeOutcome.UNKNOWN,
        tags: tuple[str, ...] = (),
        entities: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
        trace_id: TraceId | None = None,
        memory_id: MemoryId | None = None,
    ) -> Episode:
        candidate = Episode(
            memory_id=memory_id or MemoryId.new(),
            task_id=self.recorder.task_id,
            trace_id=trace_id or self.recorder.trace_id,
            start_step=start_step,
            end_step=end_step,
            summary=summary,
            outcome=outcome,
            tags=tags,
            entities=entities,
            metadata=metadata or {},
            provenance=provenance,
        )
        key = str(candidate.memory_id)
        if key in self._episodes:
            raise MemorySubsystemError(f"episode {candidate.memory_id} already exists")

        self.recorder.emit(
            EventType.MEMORY_WRITTEN,
            "memory.episodic",
            input_refs=(str(candidate.trace_id),),
            output_refs=(key,),
            metadata={
                "memory_class": MemoryClass.EPISODIC.value,
                "operation": "record",
                "outcome": candidate.outcome.value,
                "start_step": candidate.start_step,
                "end_step": candidate.end_step,
                "snapshot": candidate.model_dump(mode="json"),
            },
        )
        self._episodes[key] = candidate
        return candidate

    def get(self, memory_id: MemoryId) -> Episode:
        try:
            return self._episodes[str(memory_id)]
        except KeyError as exc:
            raise MemoryNotFoundError(f"episode {memory_id} does not exist") from exc

    def search(
        self,
        *,
        query: str | None = None,
        outcome: EpisodeOutcome | None = None,
        tags: tuple[str, ...] = (),
        limit: int = 20,
    ) -> tuple[Episode, ...]:
        if limit <= 0:
            raise ValueError("episode search limit must be positive")
        needle = query.strip().casefold() if query else None
        required_tags = {tag.strip().casefold() for tag in tags if tag.strip()}

        matches: list[Episode] = []
        for episode in self._episodes.values():
            if outcome is not None and episode.outcome != outcome:
                continue
            episode_tags = {tag.casefold() for tag in episode.tags}
            if required_tags and not required_tags.issubset(episode_tags):
                continue
            if needle is not None:
                haystack = " ".join(
                    (episode.summary, *episode.tags, *episode.entities)
                ).casefold()
                if needle not in haystack:
                    continue
            matches.append(episode)

        matches.sort(
            key=lambda episode: (
                -episode.end_step,
                -episode.start_step,
                str(episode.memory_id),
            )
        )
        selected = tuple(matches[:limit])
        self.recorder.emit(
            EventType.MEMORY_RETRIEVED,
            "memory.episodic",
            input_refs=tuple(str(episode.memory_id) for episode in selected),
            metadata={
                "memory_class": MemoryClass.EPISODIC.value,
                "query": query,
                "outcome": outcome.value if outcome else None,
                "tags": list(tags),
                "result_count": len(selected),
            },
        )
        return selected

    def all(self) -> tuple[Episode, ...]:
        return tuple(self._episodes[key] for key in sorted(self._episodes))
