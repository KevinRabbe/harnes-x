from __future__ import annotations

from pathlib import Path

from harness_x.coding.procedure_reliability import (
    ProcedureReliabilityStatus,
    ProcedureReliabilityStore,
)
from harness_x.coding.procedure_reliability_runtime import (
    ReliabilityAwareProjectMemoryStore,
)
from harness_x.coding.project_memory import (
    ProjectMemoryEntryState,
    ProjectMemoryStore,
    ProposedProjectProcedure,
)


PROCEDURE = ProposedProjectProcedure(
    key="targeted-tests-first",
    statement="Run the targeted test before the full suite for small Python changes",
    steps=("Run the targeted pytest file", "Run the full pytest suite before completion"),
    task_categories=("python", "testing"),
)


def _episode(store: ProjectMemoryStore, name: str, *, succeeded: bool = True):
    return store.record_episode(
        task=name,
        succeeded=succeeded,
        source_ref=f"test:{name}",
    )


def _promote(store: ProjectMemoryStore):
    first = _episode(store, "support-1")
    store.support_candidates(first, (PROCEDURE,))
    second = _episode(store, "support-2")
    return store.support_candidates(second, (PROCEDURE,))[0]


def test_two_verified_reuse_failures_suspend_without_erasing_m28_support(
    tmp_path: Path,
) -> None:
    memory = ProjectMemoryStore(tmp_path, project_key="demo")
    procedure = _promote(memory)
    reliability = ProcedureReliabilityStore(tmp_path, project_id=memory.project_id)
    facade = ReliabilityAwareProjectMemoryStore(memory, reliability)

    success_episode = _episode(memory, "reuse-success")
    reliability.record_usage(
        procedure=procedure,
        episode=success_episode,
        success=True,
    )
    first_failure = _episode(memory, "reuse-failure-1", succeeded=False)
    reliability.record_usage(
        procedure=procedure,
        episode=first_failure,
        success=False,
    )
    second_failure = _episode(memory, "reuse-failure-2", succeeded=False)
    suspended = reliability.record_usage(
        procedure=procedure,
        episode=second_failure,
        success=False,
    )

    assert suspended.status == ProcedureReliabilityStatus.SUSPENDED
    assert suspended.consecutive_failures == 2
    assert suspended.usage_count == 3
    assert reliability.state.usage_total == 3
    historical = next(item for item in memory.state.entries if item.entry_id == procedure.entry_id)
    assert historical.state == ProjectMemoryEntryState.ACTIVE
    assert historical.support_count == 2
    assert facade.active_entries() == ()
    assert facade.recall(query="targeted tests") == ()


def test_two_fresh_verified_supports_revalidate_suspended_procedure(tmp_path: Path) -> None:
    memory = ProjectMemoryStore(tmp_path, project_key="demo")
    procedure = _promote(memory)
    reliability = ProcedureReliabilityStore(tmp_path, project_id=memory.project_id)
    facade = ReliabilityAwareProjectMemoryStore(memory, reliability)

    for name in ("failure-1", "failure-2"):
        episode = _episode(memory, name, succeeded=False)
        reliability.record_usage(
            procedure=procedure,
            episode=episode,
            success=False,
        )
    assert reliability.record_for(procedure.entry_id).status == ProcedureReliabilityStatus.SUSPENDED

    third_support = _episode(memory, "support-3")
    third = memory.support_candidates(third_support, (PROCEDURE,))[0]
    one = reliability.observe_verified_support(procedure=third, episode=third_support)
    assert one is not None
    assert one.status == ProcedureReliabilityStatus.SUSPENDED
    assert len(one.revalidation_episode_ids) == 1
    assert facade.active_entries() == ()

    fourth_support = _episode(memory, "support-4")
    fourth = memory.support_candidates(fourth_support, (PROCEDURE,))[0]
    recovered = reliability.observe_verified_support(procedure=fourth, episode=fourth_support)
    assert recovered is not None
    assert recovered.status == ProcedureReliabilityStatus.ELIGIBLE
    assert recovered.revalidation_episode_ids == ()
    assert facade.active_entries()[0].entry_id == procedure.entry_id
    recalled = facade.recall(query="targeted tests")
    assert recalled and recalled[0].entry_id == procedure.entry_id


def test_reliability_state_and_usage_ledger_are_fingerprint_checked(tmp_path: Path) -> None:
    memory = ProjectMemoryStore(tmp_path, project_key="demo")
    procedure = _promote(memory)
    reliability = ProcedureReliabilityStore(tmp_path, project_id=memory.project_id)
    episode = _episode(memory, "reuse")
    reliability.record_usage(procedure=procedure, episode=episode, success=True)

    state_text = reliability.state_path.read_text(encoding="utf-8")
    reliability.state_path.write_text(state_text.replace('"usage_total": 1', '"usage_total": 9'), encoding="utf-8")
    try:
        ProcedureReliabilityStore(tmp_path, project_id=memory.project_id)
    except ValueError as exc:
        assert "fingerprint mismatch" in str(exc)
    else:
        raise AssertionError("tampered reliability state was accepted")
