from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from harness_x.coding.procedure_reliability import ProcedureReliabilityPolicy, ProcedureReliabilityStore
from harness_x.coding.procedure_revision import (
    ProcedureRevisionProposal,
    ProcedureRevisionState,
    ProcedureRevisionStore,
    ProcedureRevisionValidationEvidence,
)
from harness_x.coding.project_memory import ProjectMemoryStore, ProposedProjectProcedure


PARENT = ProposedProjectProcedure(
    key="crash-safe-revision-parent",
    statement="Run the targeted test before the full suite",
    steps=("Run targeted pytest", "Run the full suite"),
)


def _episode(store: ProjectMemoryStore, name: str, *, succeeded: bool = True):
    return store.record_episode(task=name, succeeded=succeeded, source_ref=f"m30-crash:{name}")


def test_retry_after_validation_append_before_state_replacement_applies_trial_once(
    tmp_path: Path,
) -> None:
    memory = ProjectMemoryStore(tmp_path, project_key="crash-revision-project")
    first = _episode(memory, "support-1")
    memory.support_candidates(first, (PARENT,))
    second = _episode(memory, "support-2")
    parent = memory.support_candidates(second, (PARENT,))[0]
    reliability = ProcedureReliabilityStore(
        tmp_path,
        project_id=memory.project_id,
        policy=ProcedureReliabilityPolicy(consecutive_failures_to_suspend=1),
    )
    failed = _episode(memory, "parent-reuse-failed", succeeded=False)
    suspended = reliability.record_usage(procedure=parent, episode=failed, success=False)

    revisions = ProcedureRevisionStore(tmp_path, project_id=memory.project_id)
    candidate = revisions.propose(
        proposal=ProcedureRevisionProposal(
            parent_procedure_id=parent.entry_id,
            statement="Run the targeted test, inspect the failure, then run the full suite",
            steps=("Run targeted pytest", "Inspect the failure", "Run the full suite"),
            rationale="Repeated failure requires an explicit inspection step.",
        ),
        parent=parent,
        origin_episode=failed,
        reliability=suspended,
    )
    validation = _episode(memory, "isolated-validation")
    durable = ProcedureRevisionValidationEvidence(
        validation_id="prval_crash_window",
        project_id=memory.project_id,
        candidate_id=candidate.candidate_id,
        parent_procedure_id=parent.entry_id,
        episode_id=validation.episode_id,
        success=True,
        created_at=datetime.now(timezone.utc),
    )
    revisions._append_validation(durable)
    assert revisions.state.validation_total == 0
    assert revisions.candidate(candidate.candidate_id).success_count == 0

    recovered = ProcedureRevisionStore(tmp_path, project_id=memory.project_id)
    assert recovered.state.validation_total == 1
    assert recovered.candidate(candidate.candidate_id).success_count == 0

    applied = recovered.record_validation(
        candidate_id=candidate.candidate_id,
        episode=validation,
        success=True,
    )
    assert applied.state == ProcedureRevisionState.CANDIDATE
    assert applied.success_count == 1
    assert applied.last_validation_episode_id == validation.episode_id
    assert recovered.state.validation_total == 1

    retried = recovered.record_validation(
        candidate_id=candidate.candidate_id,
        episode=validation,
        success=True,
    )
    assert retried == applied
    assert recovered.state.validation_total == 1
    lines = [
        line
        for line in recovered.validation_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(lines) == 1
