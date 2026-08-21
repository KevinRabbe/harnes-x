from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_x.coding.project_memory import (
    ProjectMemoryEntryState,
    ProjectMemoryStore,
    ProposedProjectFact,
    ProposedProjectProcedure,
)
from harness_x.tools.project_memory import (
    ProjectMemoryRecallInput,
    project_memory_recall_definition,
)


def _episode(store: ProjectMemoryStore, name: str, *, succeeded: bool = True):
    return store.record_episode(
        task=name,
        succeeded=succeeded,
        source_ref=f"test:{name}",
        verification_refs=(f"verification:{name}",),
    )


def _procedure(statement: str = "Run the targeted test before the full suite"):
    return ProposedProjectProcedure(
        key="python-test-convention",
        statement=statement,
        steps=("Run the targeted pytest file", "Run the full pytest suite"),
        task_categories=("python", "testing"),
    )


def test_two_verified_successes_promote_identical_project_procedure(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path / "memory", project_key="project-alpha")
    first = _episode(store, "first")
    first_entry = store.support_candidates(first, (_procedure(),))[0]
    assert first_entry.state == ProjectMemoryEntryState.CANDIDATE
    assert first_entry.support_count == 1
    assert store.active_entries() == ()

    second = _episode(store, "second")
    second_entry = store.support_candidates(second, (_procedure(),))[0]
    assert second_entry.entry_id == first_entry.entry_id
    assert second_entry.state == ProjectMemoryEntryState.ACTIVE
    assert second_entry.support_count == 2
    assert [item.entry_id for item in store.active_entries()] == [first_entry.entry_id]


def test_failed_episode_cannot_support_project_memory_candidate(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path / "memory", project_key="project-alpha")
    failed = _episode(store, "failed", succeeded=False)
    with pytest.raises(ValueError, match="verified successful episode"):
        store.support_candidates(failed, (_procedure(),))
    assert store.state.entries == ()


def test_conflicting_verified_variant_suspends_reuse_for_entire_key(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path / "memory", project_key="project-alpha")
    for name in ("first", "second"):
        store.support_candidates(_episode(store, name), (_procedure(),))
    assert len(store.active_entries()) == 1

    conflicting = ProposedProjectProcedure(
        key="python-test-convention",
        statement="Never run targeted tests; only run the full suite",
        steps=("Run the full pytest suite only",),
        task_categories=("python", "testing"),
    )
    conflict_entry = store.support_candidates(_episode(store, "third"), (conflicting,))[0]

    variants = [item for item in store.state.entries if item.key == "python-test-convention"]
    assert len(variants) == 2
    assert all(item.state == ProjectMemoryEntryState.CONFLICTED for item in variants)
    assert all(item.conflicts_with for item in variants)
    assert conflict_entry.state == ProjectMemoryEntryState.CONFLICTED
    assert store.active_entries() == ()
    assert store.recall(query="python test convention") == ()


def test_project_memory_recall_exposes_only_active_entries_by_default(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path / "memory", project_key="project-alpha")
    candidate = store.support_candidates(
        _episode(store, "one"),
        (
            ProposedProjectFact(
                key="config-location",
                statement="Configuration lives under configs/",
                task_categories=("configuration",),
            ),
        ),
    )[0]
    definition = project_memory_recall_definition(store)

    hidden = definition.handler(ProjectMemoryRecallInput(query="configuration configs"))
    assert hidden.matches == ()

    visible_candidate = definition.handler(
        ProjectMemoryRecallInput(
            query="configuration configs",
            include_candidates=True,
        )
    )
    assert visible_candidate.matches[0].entry_id == candidate.entry_id

    store.support_candidates(
        _episode(store, "two"),
        (
            ProposedProjectFact(
                key="config-location",
                statement="Configuration lives under configs/",
                task_categories=("configuration",),
            ),
        ),
    )
    active = definition.handler(ProjectMemoryRecallInput(query="configuration configs"))
    assert active.matches[0].state == ProjectMemoryEntryState.ACTIVE
    assert active.matches[0].support_count == 2


def test_active_procedure_usage_records_success_and_failure_modes(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path / "memory", project_key="project-alpha")
    for name in ("one", "two"):
        entry = store.support_candidates(_episode(store, name), (_procedure(),))[0]
    assert entry.state == ProjectMemoryEntryState.ACTIVE

    success = store.record_procedure_usage(entry.entry_id, success=True)
    assert success.usage_count == 1
    assert success.success_count == 1
    assert success.failure_count == 0

    failed = store.record_procedure_usage(
        entry.entry_id,
        success=False,
        failure_mode="targeted test missed generated files",
    )
    assert failed.usage_count == 2
    assert failed.success_count == 1
    assert failed.failure_count == 1
    assert "targeted test missed generated files" in failed.known_failure_modes


def test_project_memory_state_tamper_and_project_identity_mismatch_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    store = ProjectMemoryStore(root, project_key="project-alpha")
    store.support_candidates(_episode(store, "one"), (_procedure(),))

    raw = json.loads(store.state_path.read_text(encoding="utf-8"))
    raw["entries"][0]["statement"] = "tampered"
    store.state_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        ProjectMemoryStore(root, project_key="project-alpha")

    clean = ProjectMemoryStore(tmp_path / "other", project_key="project-alpha")
    clean.state_path.exists()
    with pytest.raises(ValueError, match="different project identity"):
        ProjectMemoryStore(tmp_path / "other", project_key="project-beta")
