from __future__ import annotations

from pathlib import Path

import pytest

from harness_x.coding.procedure_reliability import (
    ProcedureReliabilityPolicy,
    ProcedureReliabilityStatus,
    ProcedureReliabilityStore,
)
from harness_x.coding.procedure_revision import (
    ProcedureRevisionProposal,
    ProcedureRevisionState,
    ProcedureRevisionStore,
)
from harness_x.coding.project_memory import (
    ProjectMemoryStore,
    ProposedProjectProcedure,
)


PARENT = ProposedProjectProcedure(
    key="targeted-tests-first",
    statement="Run the targeted test before the full suite",
    steps=("Run targeted pytest", "Run the full suite"),
    task_categories=("python", "testing"),
)

REVISION = ProcedureRevisionProposal(
    parent_procedure_id="placeholder",
    statement="Run the targeted test, inspect the failure, then run the full suite",
    steps=(
        "Run targeted pytest",
        "Inspect and classify any targeted failure before editing further",
        "Run the full suite",
    ),
    task_categories=("python", "testing"),
    rationale="Repeated reuse failures show that the old procedure skipped failure classification.",
)


def _episode(store: ProjectMemoryStore, name: str, *, succeeded: bool = True):
    return store.record_episode(task=name, succeeded=succeeded, source_ref=f"revision:{name}")


def _suspended_parent(tmp_path: Path):
    memory = ProjectMemoryStore(tmp_path, project_key="demo-project")
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
    record = reliability.record_usage(
        procedure=parent,
        episode=failed,
        success=False,
    )
    assert record.status == ProcedureReliabilityStatus.SUSPENDED
    return memory, reliability, parent, failed, record


def _proposal(parent_id: str) -> ProcedureRevisionProposal:
    return REVISION.model_copy(update={"parent_procedure_id": parent_id})


def test_revision_candidate_requires_suspended_parent_and_different_content(tmp_path: Path) -> None:
    memory = ProjectMemoryStore(tmp_path, project_key="demo-project")
    first = _episode(memory, "support-1")
    memory.support_candidates(first, (PARENT,))
    second = _episode(memory, "support-2")
    parent = memory.support_candidates(second, (PARENT,))[0]
    reliability = ProcedureReliabilityStore(tmp_path, project_id=memory.project_id)
    episode = _episode(memory, "proposal")
    revisions = ProcedureRevisionStore(tmp_path, project_id=memory.project_id)

    with pytest.raises(ValueError, match="suspended"):
        revisions.propose(
            proposal=_proposal(parent.entry_id),
            parent=parent,
            origin_episode=episode,
            reliability=(
                reliability.record_for(parent.entry_id)
                or reliability.record_usage(
                    procedure=parent,
                    episode=_episode(memory, "successful-reuse"),
                    success=True,
                )
            ),
        )

    suspended_memory, suspended_reliability, suspended_parent, failed, record = _suspended_parent(
        tmp_path / "suspended"
    )
    store = ProcedureRevisionStore(
        tmp_path / "suspended",
        project_id=suspended_memory.project_id,
    )
    same = ProcedureRevisionProposal(
        parent_procedure_id=suspended_parent.entry_id,
        statement=suspended_parent.statement,
        steps=suspended_parent.steps,
        rationale="no actual change",
    )
    with pytest.raises(ValueError, match="must differ"):
        store.propose(
            proposal=same,
            parent=suspended_parent,
            origin_episode=failed,
            reliability=record,
        )


def test_two_successful_validations_make_revision_ready_then_m28_active_replacement_promotes(
    tmp_path: Path,
) -> None:
    memory, reliability, parent, failed, parent_record = _suspended_parent(tmp_path)
    revisions = ProcedureRevisionStore(tmp_path, project_id=memory.project_id)
    candidate = revisions.propose(
        proposal=_proposal(parent.entry_id),
        parent=parent,
        origin_episode=failed,
        reliability=parent_record,
    )
    assert candidate.state == ProcedureRevisionState.CANDIDATE

    validation_1 = _episode(memory, "revision-validation-1")
    first = revisions.record_validation(
        candidate_id=candidate.candidate_id,
        episode=validation_1,
        success=True,
    )
    replacement_proposal = ProposedProjectProcedure(
        key=candidate.replacement_memory_key,
        statement=candidate.statement,
        steps=candidate.steps,
        task_categories=candidate.task_categories,
    )
    replacement_1 = memory.support_candidates(validation_1, (replacement_proposal,))[0]
    assert first.state == ProcedureRevisionState.CANDIDATE
    assert replacement_1.support_count == 1

    validation_2 = _episode(memory, "revision-validation-2")
    ready = revisions.record_validation(
        candidate_id=candidate.candidate_id,
        episode=validation_2,
        success=True,
    )
    replacement_2 = memory.support_candidates(validation_2, (replacement_proposal,))[0]
    assert ready.state == ProcedureRevisionState.READY
    assert replacement_2.support_count == 2

    promoted = revisions.promote(
        candidate_id=candidate.candidate_id,
        replacement=replacement_2,
        parent_reliability=reliability.record_for(parent.entry_id),
    )
    assert promoted.state == ProcedureRevisionState.PROMOTED
    assert promoted.replacement_entry_id == replacement_2.entry_id
    assert promoted.parent_procedure_id == parent.entry_id
    assert revisions.promoted_parent_ids() == frozenset({parent.entry_id})
    assert revisions.promoted_replacement_ids() == frozenset({replacement_2.entry_id})


def test_two_failed_revision_validations_reject_candidate(tmp_path: Path) -> None:
    memory, _, parent, failed, parent_record = _suspended_parent(tmp_path)
    revisions = ProcedureRevisionStore(tmp_path, project_id=memory.project_id)
    candidate = revisions.propose(
        proposal=_proposal(parent.entry_id),
        parent=parent,
        origin_episode=failed,
        reliability=parent_record,
    )

    first = revisions.record_validation(
        candidate_id=candidate.candidate_id,
        episode=_episode(memory, "revision-failed-1", succeeded=False),
        success=False,
        failure_mode="revision did not fix the targeted failure",
    )
    second = revisions.record_validation(
        candidate_id=candidate.candidate_id,
        episode=_episode(memory, "revision-failed-2", succeeded=False),
        success=False,
        failure_mode="revision caused the same failure again",
    )
    assert first.state == ProcedureRevisionState.CANDIDATE
    assert second.state == ProcedureRevisionState.REJECTED
    assert second.failure_count == 2
    assert revisions.open_candidates() == ()


def test_promoting_one_revision_supersedes_open_siblings(tmp_path: Path) -> None:
    memory, reliability, parent, failed, parent_record = _suspended_parent(tmp_path)
    revisions = ProcedureRevisionStore(tmp_path, project_id=memory.project_id)
    first = revisions.propose(
        proposal=_proposal(parent.entry_id),
        parent=parent,
        origin_episode=failed,
        reliability=parent_record,
    )
    sibling_proposal = ProcedureRevisionProposal(
        parent_procedure_id=parent.entry_id,
        statement="Run targeted tests and inspect repository history before the full suite",
        steps=("Run targeted pytest", "Inspect relevant git history", "Run the full suite"),
        rationale="Alternative repair path",
    )
    sibling = revisions.propose(
        proposal=sibling_proposal,
        parent=parent,
        origin_episode=failed,
        reliability=parent_record,
    )

    replacement_proposal = ProposedProjectProcedure(
        key=first.replacement_memory_key,
        statement=first.statement,
        steps=first.steps,
        task_categories=first.task_categories,
    )
    for index in range(2):
        episode = _episode(memory, f"winning-validation-{index}")
        ready = revisions.record_validation(
            candidate_id=first.candidate_id,
            episode=episode,
            success=True,
        )
        replacement = memory.support_candidates(episode, (replacement_proposal,))[0]
    assert ready.state == ProcedureRevisionState.READY
    revisions.promote(
        candidate_id=first.candidate_id,
        replacement=replacement,
        parent_reliability=reliability.record_for(parent.entry_id),
    )
    assert revisions.candidate(first.candidate_id).state == ProcedureRevisionState.PROMOTED
    superseded = revisions.candidate(sibling.candidate_id)
    assert superseded.state == ProcedureRevisionState.SUPERSEDED
    assert first.candidate_id in superseded.terminal_reason
