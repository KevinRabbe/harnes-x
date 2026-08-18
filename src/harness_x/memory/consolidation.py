"""Explicit candidate-only consolidation pipelines over episodic evidence."""

from __future__ import annotations

from typing import Any

from harness_x.core.ids import CandidateId, MemoryId

from .episodic import EpisodeOutcome, EpisodicMemory
from .procedural import ProceduralMemory, ProcedureRecord
from .semantic import SemanticClaim, SemanticMemory


class SemanticConsolidator:
    """Turn an episode into an unverified semantic candidate, never directly into truth."""

    def __init__(self, episodic: EpisodicMemory, semantic: SemanticMemory):
        if episodic is not semantic.episodic:
            raise ValueError("semantic consolidator requires the semantic store's episodic source")
        self.episodic = episodic
        self.semantic = semantic

    def candidate_from_episode(
        self,
        episode_id: MemoryId,
        *,
        claim_key: str,
        statement: str,
        value: Any,
        confidence: float,
        candidate_id: CandidateId | None = None,
        memory_id: MemoryId | None = None,
    ) -> SemanticClaim:
        episode = self.episodic.get(episode_id)
        return self.semantic.create_candidate(
            claim_key=claim_key,
            statement=statement,
            value=value,
            confidence=confidence,
            source_episode_ids=(episode.memory_id,),
            provenance=episode.provenance,
            candidate_id=candidate_id,
            memory_id=memory_id,
        )


class ProceduralConsolidator:
    """Turn repeated successful episodes into an unevaluated procedure candidate."""

    def __init__(self, episodic: EpisodicMemory, procedural: ProceduralMemory):
        if episodic is not procedural.episodic:
            raise ValueError("procedural consolidator requires the procedural store's episodic source")
        self.episodic = episodic
        self.procedural = procedural

    def candidate_from_successes(
        self,
        episode_ids: tuple[MemoryId, ...],
        *,
        name: str,
        version: str,
        steps: tuple[str, ...],
        task_categories: tuple[str, ...] = (),
        candidate_id: CandidateId | None = None,
        memory_id: MemoryId | None = None,
    ) -> ProcedureRecord:
        episodes = tuple(self.episodic.get(item) for item in episode_ids)
        if len(episodes) < 2:
            raise ValueError("procedural consolidation requires repeated successful episodes")
        if any(episode.outcome != EpisodeOutcome.SUCCESS for episode in episodes):
            raise ValueError("procedural consolidation rejects non-success source episodes")
        return self.procedural.create_candidate(
            name=name,
            version=version,
            steps=steps,
            source_episode_ids=episode_ids,
            provenance=episodes[0].provenance,
            task_categories=task_categories,
            candidate_id=candidate_id,
            memory_id=memory_id,
        )
