"""Evidence-gated semantic memory with contradictions and revision history."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.core.errors import MemoryNotFoundError, MemorySubsystemError
from harness_x.core.events import EventType
from harness_x.core.ids import CandidateId, EventId, MemoryId, TaskId
from harness_x.core.provenance import Provenance
from harness_x.telemetry import TraceRecorder

from .base import MemoryClass
from .episodic import EpisodicMemory


class SemanticState(StrEnum):
    CANDIDATE = "candidate"
    EVALUATED = "evaluated"
    VERIFIED = "verified"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


class SemanticClaim(BaseModel):
    """One revisioned semantic claim; candidate state is not semantic truth."""

    model_config = ConfigDict(frozen=True)

    memory_id: MemoryId
    candidate_id: CandidateId
    task_id: TaskId
    claim_key: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: Provenance
    source_episode_ids: tuple[MemoryId, ...]
    source_provenance: tuple[Provenance, ...]
    state: SemanticState = SemanticState.CANDIDATE
    evaluation_accepted: bool | None = None
    verification_refs: tuple[str, ...] = ()
    contradiction_ids: tuple[MemoryId, ...] = ()
    invalidation_reason: str | None = None
    revision: int = Field(default=1, ge=1)

    @field_validator("claim_key", "statement")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("semantic claim text cannot be blank")
        return value

    @property
    def is_contradicted(self) -> bool:
        return bool(self.contradiction_ids)


class SemanticHistoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory_id: MemoryId
    revision: int = Field(ge=1)
    state: SemanticState
    confidence: float = Field(ge=0.0, le=1.0)
    event_id: EventId
    step: int = Field(ge=1)
    timestamp: datetime
    reason: str


class SemanticMemory:
    """Software-owned semantic candidates, verification, contradictions, and history."""

    def __init__(self, recorder: TraceRecorder, episodic: EpisodicMemory):
        if recorder is not episodic.recorder:
            raise MemorySubsystemError("semantic and episodic memory must share one recorder")
        self.recorder = recorder
        self.episodic = episodic
        self._claims: dict[str, SemanticClaim] = {}
        self._history: dict[str, list[SemanticHistoryEntry]] = {}
        self._candidate_to_memory: dict[str, str] = {}

    @staticmethod
    def _canonical_value(value: Any) -> str:
        try:
            return json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("semantic claim value must be JSON-serializable") from exc

    def _source_episodes(
        self, source_episode_ids: tuple[MemoryId, ...]
    ) -> tuple[object, ...]:
        if not source_episode_ids:
            raise ValueError("semantic candidate requires at least one source episode")
        episodes = tuple(self.episodic.get(memory_id) for memory_id in source_episode_ids)
        if any(episode.task_id != self.recorder.task_id for episode in episodes):
            raise MemorySubsystemError("semantic source episode belongs to another task")
        return episodes

    def create_candidate(
        self,
        *,
        claim_key: str,
        statement: str,
        value: Any,
        confidence: float,
        source_episode_ids: tuple[MemoryId, ...],
        provenance: Provenance,
        candidate_id: CandidateId | None = None,
        memory_id: MemoryId | None = None,
    ) -> SemanticClaim:
        self._canonical_value(value)
        episodes = self._source_episodes(source_episode_ids)
        claim = SemanticClaim(
            memory_id=memory_id or MemoryId.new(),
            candidate_id=candidate_id or CandidateId.new(),
            task_id=self.recorder.task_id,
            claim_key=claim_key,
            statement=statement,
            value=value,
            confidence=confidence,
            provenance=provenance,
            source_episode_ids=source_episode_ids,
            source_provenance=tuple(episode.provenance for episode in episodes),
        )
        memory_key = str(claim.memory_id)
        candidate_key = str(claim.candidate_id)
        if memory_key in self._claims:
            raise MemorySubsystemError(f"semantic memory {claim.memory_id} already exists")
        if candidate_key in self._candidate_to_memory:
            raise MemorySubsystemError(f"semantic candidate {claim.candidate_id} already exists")

        candidate_event = self.recorder.emit(
            EventType.CANDIDATE_CREATED,
            "memory.semantic",
            input_refs=tuple(str(item) for item in source_episode_ids),
            output_refs=(candidate_key,),
            metadata={
                "candidate_kind": "semantic",
                "memory_id": memory_key,
                "claim_key": claim.claim_key,
                "confidence": claim.confidence,
                "state": claim.state.value,
            },
        )
        self.recorder.emit(
            EventType.MEMORY_WRITTEN,
            "memory.semantic",
            input_refs=(candidate_key, *tuple(str(item) for item in source_episode_ids)),
            output_refs=(memory_key,),
            metadata={
                "memory_class": MemoryClass.SEMANTIC.value,
                "operation": "candidate_created",
                "snapshot": claim.model_dump(mode="json"),
            },
        )
        self._claims[memory_key] = claim
        self._candidate_to_memory[candidate_key] = memory_key
        self._history[memory_key] = [
            SemanticHistoryEntry(
                memory_id=claim.memory_id,
                revision=claim.revision,
                state=claim.state,
                confidence=claim.confidence,
                event_id=candidate_event.event_id,
                step=candidate_event.step,
                timestamp=candidate_event.timestamp,
                reason="candidate_created",
            )
        ]
        return claim

    def evaluate(
        self,
        memory_id: MemoryId,
        *,
        accepted: bool,
        evidence_refs: tuple[str, ...],
        reason: str,
        confidence: float | None = None,
    ) -> SemanticClaim:
        current = self._require(memory_id)
        if current.state != SemanticState.CANDIDATE:
            raise MemorySubsystemError("only a semantic candidate can be evaluated")
        reason = self._reason(reason)
        refs = self._evidence(evidence_refs)
        next_confidence = current.confidence if confidence is None else confidence
        next_state = SemanticState.EVALUATED if accepted else SemanticState.REJECTED
        updated = current.model_copy(
            update={
                "state": next_state,
                "evaluation_accepted": accepted,
                "verification_refs": refs,
                "confidence": next_confidence,
                "revision": current.revision + 1,
            }
        )
        evaluated_event = self.recorder.emit(
            EventType.CANDIDATE_EVALUATED,
            "memory.semantic",
            input_refs=(str(current.candidate_id), *refs),
            output_refs=(str(current.candidate_id),),
            metadata={
                "candidate_kind": "semantic",
                "accepted": accepted,
                "memory_id": str(memory_id),
                "reason": reason,
                "confidence": next_confidence,
            },
        )
        self._commit(current, updated, evaluated_event, reason)
        if not accepted:
            rejected_event = self.recorder.emit(
                EventType.CANDIDATE_REJECTED,
                "memory.semantic",
                input_refs=(str(current.candidate_id), *refs),
                output_refs=(str(current.candidate_id),),
                metadata={
                    "candidate_kind": "semantic",
                    "memory_id": str(memory_id),
                    "reason": reason,
                },
            )
            self._append_history(updated, rejected_event, reason)
        return self._claims[str(memory_id)]

    def promote(self, memory_id: MemoryId, *, reason: str) -> SemanticClaim:
        current = self._require(memory_id)
        if current.state != SemanticState.EVALUATED or current.evaluation_accepted is not True:
            raise MemorySubsystemError("semantic promotion requires an accepted evaluation")
        reason = self._reason(reason)
        contradictions = tuple(
            claim.memory_id
            for claim in self._claims.values()
            if claim.memory_id != current.memory_id
            and claim.state == SemanticState.VERIFIED
            and claim.claim_key == current.claim_key
            and self._canonical_value(claim.value) != self._canonical_value(current.value)
        )
        promoted = current.model_copy(
            update={
                "state": SemanticState.VERIFIED,
                "contradiction_ids": contradictions,
                "revision": current.revision + 1,
            }
        )
        event = self.recorder.emit(
            EventType.CANDIDATE_PROMOTED,
            "memory.semantic",
            input_refs=(str(current.candidate_id), *current.verification_refs),
            output_refs=(str(current.candidate_id),),
            metadata={
                "candidate_kind": "semantic",
                "memory_id": str(memory_id),
                "reason": reason,
                "contradiction_ids": [str(item) for item in contradictions],
            },
        )
        self._commit(current, promoted, event, reason)

        for conflicting_id in contradictions:
            conflicting = self._require(conflicting_id)
            if current.memory_id in conflicting.contradiction_ids:
                continue
            linked = conflicting.model_copy(
                update={
                    "contradiction_ids": conflicting.contradiction_ids + (current.memory_id,),
                    "revision": conflicting.revision + 1,
                }
            )
            link_event = self.recorder.emit(
                EventType.MEMORY_WRITTEN,
                "memory.semantic",
                input_refs=(str(current.memory_id), str(conflicting.memory_id)),
                output_refs=(str(conflicting.memory_id),),
                metadata={
                    "memory_class": MemoryClass.SEMANTIC.value,
                    "operation": "contradiction_linked",
                    "contradiction_id": str(current.memory_id),
                    "snapshot": linked.model_dump(mode="json"),
                },
            )
            self._commit(conflicting, linked, link_event, "contradiction_linked", emit_memory=False)
        return self._claims[str(memory_id)]

    def invalidate(
        self,
        memory_id: MemoryId,
        *,
        evidence_refs: tuple[str, ...],
        reason: str,
    ) -> SemanticClaim:
        current = self._require(memory_id)
        if current.state != SemanticState.VERIFIED:
            raise MemorySubsystemError("only verified semantic memory can be invalidated")
        refs = self._evidence(evidence_refs)
        reason = self._reason(reason)
        updated = current.model_copy(
            update={
                "state": SemanticState.INVALIDATED,
                "verification_refs": tuple(dict.fromkeys((*current.verification_refs, *refs))),
                "invalidation_reason": reason,
                "revision": current.revision + 1,
            }
        )
        event = self.recorder.emit(
            EventType.CANDIDATE_INVALIDATED,
            "memory.semantic",
            input_refs=(str(current.candidate_id), *refs),
            output_refs=(str(current.candidate_id),),
            metadata={
                "candidate_kind": "semantic",
                "memory_id": str(memory_id),
                "reason": reason,
            },
        )
        self._commit(current, updated, event, reason)
        return updated

    def get(self, memory_id: MemoryId) -> SemanticClaim:
        return self._require(memory_id)

    def by_candidate(self, candidate_id: CandidateId) -> SemanticClaim:
        try:
            memory_key = self._candidate_to_memory[str(candidate_id)]
        except KeyError as exc:
            raise MemoryNotFoundError(
                f"semantic candidate {candidate_id} does not exist"
            ) from exc
        return self._claims[memory_key]

    def verified(self, *, claim_key: str | None = None) -> tuple[SemanticClaim, ...]:
        items = [claim for claim in self._claims.values() if claim.state == SemanticState.VERIFIED]
        if claim_key is not None:
            items = [claim for claim in items if claim.claim_key == claim_key]
        return tuple(sorted(items, key=lambda claim: str(claim.memory_id)))

    def contradictions(self, claim_key: str | None = None) -> tuple[SemanticClaim, ...]:
        items = [claim for claim in self.verified(claim_key=claim_key) if claim.is_contradicted]
        return tuple(items)

    def all(self) -> tuple[SemanticClaim, ...]:
        return tuple(self._claims[key] for key in sorted(self._claims))

    def history(self, memory_id: MemoryId) -> tuple[SemanticHistoryEntry, ...]:
        self._require(memory_id)
        return tuple(self._history[str(memory_id)])

    def _require(self, memory_id: MemoryId) -> SemanticClaim:
        try:
            return self._claims[str(memory_id)]
        except KeyError as exc:
            raise MemoryNotFoundError(f"semantic memory {memory_id} does not exist") from exc

    @staticmethod
    def _reason(reason: str) -> str:
        normalized = reason.strip()
        if not normalized:
            raise ValueError("semantic memory reason cannot be blank")
        return normalized

    @staticmethod
    def _evidence(refs: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(ref.strip() for ref in refs)
        if not normalized or any(not ref for ref in normalized):
            raise ValueError("semantic evaluation requires non-blank evidence refs")
        return tuple(dict.fromkeys(normalized))

    def _commit(
        self,
        previous: SemanticClaim,
        updated: SemanticClaim,
        event,
        reason: str,
        *,
        emit_memory: bool = True,
    ) -> None:
        self._claims[str(updated.memory_id)] = updated
        self._append_history(updated, event, reason)
        if emit_memory:
            self.recorder.emit(
                EventType.MEMORY_WRITTEN,
                "memory.semantic",
                input_refs=(str(previous.memory_id), str(previous.candidate_id)),
                output_refs=(str(updated.memory_id),),
                metadata={
                    "memory_class": MemoryClass.SEMANTIC.value,
                    "operation": "revision",
                    "previous_revision": previous.revision,
                    "snapshot": updated.model_dump(mode="json"),
                },
            )

    def _append_history(self, claim: SemanticClaim, event, reason: str) -> None:
        self._history[str(claim.memory_id)].append(
            SemanticHistoryEntry(
                memory_id=claim.memory_id,
                revision=claim.revision,
                state=claim.state,
                confidence=claim.confidence,
                event_id=event.event_id,
                step=event.step,
                timestamp=event.timestamp,
                reason=reason,
            )
        )
