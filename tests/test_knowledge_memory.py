from datetime import datetime, timezone

import pytest

from harness_x.core import FixedClock, SystemVersion, TaskId, TraceId
from harness_x.core.events import EventType
from harness_x.core.ids import CandidateId, MemoryId
from harness_x.core.provenance import Provenance, SourceKind, VerificationState
from harness_x.memory import (
    EpisodeOutcome,
    EpisodicMemory,
    ProceduralConsolidator,
    ProceduralMemory,
    ProcedureState,
    SemanticConsolidator,
    SemanticMemory,
    SemanticState,
)
from harness_x.orchestrator import TaskOrchestrator
from harness_x.telemetry import TraceRecorder, TraceReplayer, TraceStore


def _system(tmp_path):
    clock = FixedClock(datetime(2026, 8, 18, 18, 0, tzinfo=timezone.utc))
    recorder = TraceRecorder(
        TraceStore(tmp_path / "knowledge.jsonl"),
        TraceId(value="trace_knowledge"),
        TaskId(value="task_knowledge"),
        SystemVersion(value="test-v1"),
        clock,
    )
    TaskOrchestrator.create(recorder)
    provenance = Provenance(
        source_kind=SourceKind.TEST,
        source_ref="test:knowledge",
        created_at=clock.now(),
        system_version=recorder.system_version,
        trace_id=recorder.trace_id,
        verification=VerificationState.VERIFIED,
    )
    episodic = EpisodicMemory(recorder)
    semantic = SemanticMemory(recorder, episodic)
    procedural = ProceduralMemory(recorder, episodic)
    return recorder, provenance, episodic, semantic, procedural


def _episode(recorder, episodic, provenance, *, suffix: str, outcome=EpisodeOutcome.SUCCESS):
    event = recorder.emit(
        EventType.OBSERVATION_RECEIVED,
        "test.knowledge",
        metadata={"suffix": suffix},
    )
    return episodic.record(
        start_step=event.step,
        end_step=event.step,
        summary=f"episode {suffix}",
        outcome=outcome,
        tags=("knowledge", suffix),
        provenance=provenance,
        memory_id=MemoryId(value=f"mem_episode_{suffix}"),
    )


def test_semantic_contradictions_remain_explicit_and_revisioned(tmp_path) -> None:
    recorder, provenance, episodic, semantic, _ = _system(tmp_path)
    first_episode = _episode(recorder, episodic, provenance, suffix="alpha")
    second_episode = _episode(recorder, episodic, provenance, suffix="beta")
    consolidator = SemanticConsolidator(episodic, semantic)

    first = consolidator.candidate_from_episode(
        first_episode.memory_id,
        claim_key="service.endpoint",
        statement="The endpoint is alpha",
        value={"endpoint": "alpha"},
        confidence=0.8,
        candidate_id=CandidateId(value="candidate_semantic_alpha"),
        memory_id=MemoryId(value="mem_semantic_alpha"),
    )
    assert first.state == SemanticState.CANDIDATE
    assert first.source_provenance == (first_episode.provenance,)
    assert semantic.verified() == ()

    semantic.evaluate(
        first.memory_id,
        accepted=True,
        evidence_refs=("verify:alpha",),
        reason="independent check passed",
    )
    semantic.promote(first.memory_id, reason="promote verified alpha claim")

    second = consolidator.candidate_from_episode(
        second_episode.memory_id,
        claim_key="service.endpoint",
        statement="The endpoint is beta",
        value={"endpoint": "beta"},
        confidence=0.85,
        candidate_id=CandidateId(value="candidate_semantic_beta"),
        memory_id=MemoryId(value="mem_semantic_beta"),
    )
    semantic.evaluate(
        second.memory_id,
        accepted=True,
        evidence_refs=("verify:beta",),
        reason="independent check also passed",
    )
    semantic.promote(second.memory_id, reason="retain conflicting verified evidence")

    current_first = semantic.get(first.memory_id)
    current_second = semantic.get(second.memory_id)
    assert current_first.state == SemanticState.VERIFIED
    assert current_second.state == SemanticState.VERIFIED
    assert current_second.memory_id in current_first.contradiction_ids
    assert current_first.memory_id in current_second.contradiction_ids
    assert {item.memory_id for item in semantic.contradictions("service.endpoint")} == {
        first.memory_id,
        second.memory_id,
    }
    assert len(semantic.history(first.memory_id)) >= 4


def test_rejected_semantic_candidate_keeps_episode_and_invalidation_replays(tmp_path) -> None:
    recorder, provenance, episodic, semantic, _ = _system(tmp_path)
    episode = _episode(recorder, episodic, provenance, suffix="reject")
    consolidator = SemanticConsolidator(episodic, semantic)

    rejected = consolidator.candidate_from_episode(
        episode.memory_id,
        claim_key="build.status",
        statement="The build is permanently green",
        value={"green": True},
        confidence=0.4,
        candidate_id=CandidateId(value="candidate_semantic_rejected"),
        memory_id=MemoryId(value="mem_semantic_rejected"),
    )
    semantic.evaluate(
        rejected.memory_id,
        accepted=False,
        evidence_refs=("verify:failed",),
        reason="counterexample found",
    )
    assert semantic.get(rejected.memory_id).state == SemanticState.REJECTED
    assert episodic.get(episode.memory_id) == episode

    valid = consolidator.candidate_from_episode(
        episode.memory_id,
        claim_key="build.tool",
        statement="The build used pytest",
        value={"tool": "pytest"},
        confidence=0.9,
        candidate_id=CandidateId(value="candidate_semantic_invalidated"),
        memory_id=MemoryId(value="mem_semantic_invalidated"),
    )
    semantic.evaluate(
        valid.memory_id,
        accepted=True,
        evidence_refs=("verify:tool",),
        reason="trace confirms tool",
    )
    semantic.promote(valid.memory_id, reason="promote traced claim")
    semantic.invalidate(
        valid.memory_id,
        evidence_refs=("verify:newer-trace",),
        reason="newer evidence supersedes this claim",
    )
    assert semantic.get(valid.memory_id).state == SemanticState.INVALIDATED
    assert [entry.state for entry in semantic.history(valid.memory_id)][-1] == SemanticState.INVALIDATED

    replay = TraceReplayer().replay(recorder.store.events(trace_id=recorder.trace_id))
    assert replay.candidates[str(rejected.candidate_id)] == "rejected"
    assert replay.candidates[str(valid.candidate_id)] == "invalidated"
    assert str(episode.memory_id) in replay.memories


def test_procedure_versions_coexist_with_independent_usage_history(tmp_path) -> None:
    recorder, provenance, episodic, _, procedural = _system(tmp_path)
    first = _episode(recorder, episodic, provenance, suffix="p1")
    second = _episode(recorder, episodic, provenance, suffix="p2")
    consolidator = ProceduralConsolidator(episodic, procedural)

    v1 = consolidator.candidate_from_successes(
        (first.memory_id, second.memory_id),
        name="dependency_resolution",
        version="v1",
        steps=("inspect", "resolve", "verify"),
        task_categories=("build",),
        candidate_id=CandidateId(value="candidate_procedure_v1"),
        memory_id=MemoryId(value="mem_procedure_v1"),
    )
    assert v1.state == ProcedureState.CANDIDATE
    assert v1.source_provenance == (first.provenance, second.provenance)
    procedural.evaluate(
        v1.memory_id,
        accepted=True,
        evidence_refs=("benchmark:v1",),
        reason="baseline benchmark passed",
    )
    procedural.promote(v1.memory_id, reason="activate v1")
    procedural.record_usage(
        v1.memory_id,
        success=True,
        cost=0.2,
        task_category="build",
    )
    procedural.record_usage(
        v1.memory_id,
        success=False,
        cost=0.5,
        task_category="build",
        failure_mode="cyclic_dependency",
    )

    v2 = consolidator.candidate_from_successes(
        (first.memory_id, second.memory_id),
        name="dependency_resolution",
        version="v2",
        steps=("inspect", "rank", "resolve", "verify"),
        task_categories=("build",),
        candidate_id=CandidateId(value="candidate_procedure_v2"),
        memory_id=MemoryId(value="mem_procedure_v2"),
    )
    procedural.evaluate(
        v2.memory_id,
        accepted=True,
        evidence_refs=("benchmark:v2",),
        reason="candidate benchmark passed",
    )
    procedural.promote(v2.memory_id, reason="activate v2 for comparison")

    active = procedural.versions("dependency_resolution", active_only=True)
    assert [item.version for item in active] == ["v1", "v2"]
    current_v1 = procedural.get(v1.memory_id)
    current_v2 = procedural.get(v2.memory_id)
    assert current_v1.usage_count == 2
    assert current_v1.success_count == 1
    assert current_v1.failure_count == 1
    assert current_v1.success_rate == pytest.approx(0.5)
    assert current_v1.average_cost == pytest.approx(0.35)
    assert current_v1.known_failure_modes == ("cyclic_dependency",)
    assert current_v2.usage_count == 0
    assert current_v2.success_rate is None
    assert len(procedural.history(v1.memory_id)) == 5


def test_procedure_candidate_requires_repeated_successful_evidence(tmp_path) -> None:
    recorder, provenance, episodic, _, procedural = _system(tmp_path)
    success = _episode(recorder, episodic, provenance, suffix="single")
    failure = _episode(
        recorder,
        episodic,
        provenance,
        suffix="failure",
        outcome=EpisodeOutcome.FAILURE,
    )
    consolidator = ProceduralConsolidator(episodic, procedural)

    with pytest.raises(ValueError):
        consolidator.candidate_from_successes(
            (success.memory_id,),
            name="too_early",
            version="v1",
            steps=("try",),
        )

    with pytest.raises(ValueError):
        consolidator.candidate_from_successes(
            (success.memory_id, failure.memory_id),
            name="mixed_evidence",
            version="v1",
            steps=("try", "verify"),
        )
