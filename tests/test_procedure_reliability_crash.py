from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from harness_x.coding.procedure_reliability import (
    ProcedureReliabilityStore,
    ProcedureUsageEvidence,
)
from harness_x.coding.project_memory import ProjectMemoryStore, ProposedProjectProcedure


PROCEDURE = ProposedProjectProcedure(
    key="crash-safe-procedure",
    statement="Use the verified project procedure",
    steps=("Apply the bounded procedure",),
)


def _episode(store: ProjectMemoryStore, name: str, *, succeeded: bool = True):
    return store.record_episode(task=name, succeeded=succeeded, source_ref=f"crash:{name}")


def test_retry_after_usage_append_before_state_replacement_applies_outcome_once(
    tmp_path: Path,
) -> None:
    memory = ProjectMemoryStore(tmp_path, project_key="demo")
    first = _episode(memory, "support-1")
    memory.support_candidates(first, (PROCEDURE,))
    second = _episode(memory, "support-2")
    procedure = memory.support_candidates(second, (PROCEDURE,))[0]
    failed = _episode(memory, "reuse-failed", succeeded=False)

    store = ProcedureReliabilityStore(tmp_path, project_id=memory.project_id)
    durable = ProcedureUsageEvidence(
        usage_id="pusage_crash_window",
        project_id=memory.project_id,
        procedure_id=procedure.entry_id,
        episode_id=failed.episode_id,
        success=False,
        failure_mode="simulated_append_before_state_crash",
        created_at=datetime.now(timezone.utc),
    )
    store._append_usage(durable)
    assert store.state.usage_total == 0

    recovered = ProcedureReliabilityStore(tmp_path, project_id=memory.project_id)
    assert recovered.state.usage_total == 1
    assert recovered.record_for(procedure.entry_id) is None

    applied = recovered.record_usage(
        procedure=procedure,
        episode=failed,
        success=False,
        failure_mode="simulated_append_before_state_crash",
    )
    assert applied.usage_count == 1
    assert applied.failure_count == 1
    assert applied.last_episode_id == failed.episode_id
    assert recovered.state.usage_total == 1

    retried = recovered.record_usage(
        procedure=procedure,
        episode=failed,
        success=False,
        failure_mode="simulated_append_before_state_crash",
    )
    assert retried == applied
    assert recovered.state.usage_total == 1
    lines = [line for line in recovered.usage_path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1
