from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_x.coding.control import (
    CodingControlController,
    CodingPhase,
    CommitmentStatus,
    HorizonMode,
    InterventionKind,
)
from harness_x.core import TaskId


def _controller(tmp_path: Path, **kwargs) -> CodingControlController:
    return CodingControlController(
        task_id=TaskId.new(),
        task="Implement the requested change",
        constraints=("Preserve the public API",),
        acceptance_requirements=("software-owned verification passes",),
        reasoning_limit=kwargs.pop("reasoning_limit", 20),
        tool_action_limit=kwargs.pop("tool_action_limit", 30),
        plan_path=tmp_path / "coding-plan.json",
        **kwargs,
    )


def test_plan_artifact_and_root_commitment_are_durable(tmp_path: Path) -> None:
    controller = _controller(tmp_path)

    plan_path = tmp_path / "coding-plan.json"
    payload = json.loads(plan_path.read_text(encoding="utf-8"))

    assert payload["plan_id"]["value"].startswith("codingplan_")
    assert payload["phase"] == "orient"
    assert len(payload["commitments"]) == 1
    assert payload["commitments"][0]["status"] == "active"
    assert controller.plan.pending_commitments[0].commitment_id == controller.root_commitment_id

    with pytest.raises(ValueError, match="requires evidence"):
        controller.mark_root_satisfied(step=1, evidence_refs=())

    satisfied = controller.mark_root_satisfied(
        step=2,
        evidence_refs=("verification:all_passed", "model:semantic_complete"),
    )
    assert satisfied.status == CommitmentStatus.SATISFIED
    refreshed = json.loads(plan_path.read_text(encoding="utf-8"))
    assert refreshed["commitments"][0]["status"] == "satisfied"


def test_phase_transitions_are_software_owned_and_checked(tmp_path: Path) -> None:
    controller = _controller(tmp_path)

    controller.transition_phase(
        CodingPhase.DIAGNOSE,
        reason="repository inspected",
        step=1,
    )
    controller.transition_phase(
        CodingPhase.IMPLEMENT,
        reason="diagnosis is actionable",
        step=2,
    )
    controller.begin_verification(step=3, reason="implementation mutated workspace")
    controller.record_verification(
        passed=True,
        failure_signature=None,
        step=4,
    )
    controller.transition_phase(
        CodingPhase.COMPLETE,
        reason="review accepted",
        step=5,
    )

    assert controller.plan.phase == CodingPhase.COMPLETE

    with pytest.raises(ValueError, match="illegal coding phase transition"):
        controller.transition_phase(
            CodingPhase.IMPLEMENT,
            reason="cannot reopen a completed plan",
            step=6,
        )


def test_commitment_dependencies_cannot_be_silently_skipped(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    first = controller.create_commitment(
        "Add the backend contract",
        target="src/api.py",
        step=1,
    )
    second = controller.create_commitment(
        "Wire the frontend to the backend contract",
        target="web/api.ts",
        depends_on=(first.commitment_id,),
        step=2,
    )

    with pytest.raises(ValueError, match="unresolved dependencies"):
        controller.transition_commitment(
            second.commitment_id,
            CommitmentStatus.SATISFIED,
            reason="frontend finished",
            step=3,
        )

    controller.transition_commitment(
        first.commitment_id,
        CommitmentStatus.SATISFIED,
        reason="backend finished",
        step=4,
    )
    completed = controller.transition_commitment(
        second.commitment_id,
        CommitmentStatus.SATISFIED,
        reason="frontend finished",
        step=5,
    )

    assert completed.status == CommitmentStatus.SATISFIED
    assert controller.plan.revision >= 5


def test_analysis_treadmill_forces_implementation_phase(tmp_path: Path) -> None:
    controller = _controller(tmp_path, max_inspection_streak=4)

    for index in range(4):
        controller.record_tool_result(
            tool_name="workspace_read",
            arguments={"path": f"file_{index}.py"},
            succeeded=True,
            output={"path": f"file_{index}.py", "content": f"value={index}"},
            step=index + 1,
        )

    intervention = controller.assess_intervention(
        reasoning_used=4,
        tool_actions_used=4,
    )
    assert intervention.kind == InterventionKind.FORCE_IMPLEMENTATION
    assert controller.plan.phase == CodingPhase.DIAGNOSE

    controller.apply_intervention(intervention, step=5)
    assert controller.plan.phase == CodingPhase.IMPLEMENT
    assert controller.progress_snapshot().new_evidence_count == 4


def test_exact_repeat_tool_loop_requests_a_different_approach(tmp_path: Path) -> None:
    controller = _controller(tmp_path, max_inspection_streak=10)

    for step in range(1, 4):
        controller.record_tool_result(
            tool_name="workspace_read",
            arguments={"path": "app.py"},
            succeeded=True,
            output={"path": "app.py", "content": "x = 1"},
            step=step,
        )

    progress = controller.progress_snapshot()
    intervention = controller.assess_intervention(
        reasoning_used=3,
        tool_actions_used=3,
    )

    assert progress.repeat_streak == 3
    assert progress.duplicate_actions == 2
    assert intervention.kind == InterventionKind.CHANGE_APPROACH
    assert "Do not repeat" in intervention.directive


def test_repeated_same_verifier_failure_forces_replan(tmp_path: Path) -> None:
    controller = _controller(tmp_path, max_same_failure_count=3)
    controller.transition_phase(CodingPhase.DIAGNOSE, reason="oriented", step=1)
    controller.transition_phase(CodingPhase.IMPLEMENT, reason="ready", step=2)

    for index in range(3):
        controller.begin_verification(step=3 + index * 2, reason="repair attempt")
        controller.record_verification(
            passed=False,
            failure_signature="test_auth::refresh race still reproduces",
            step=4 + index * 2,
        )

    assert controller.progress_snapshot().same_failure_count == 3
    intervention = controller.assess_intervention(
        reasoning_used=9,
        tool_actions_used=6,
    )
    assert intervention.kind == InterventionKind.REPLAN

    controller.apply_intervention(intervention, step=10)
    assert controller.plan.phase == CodingPhase.PLAN


def test_horizon_pressure_changes_controller_posture(tmp_path: Path) -> None:
    controller = _controller(
        tmp_path,
        reasoning_limit=20,
        tool_action_limit=20,
    )

    assert controller.horizon_snapshot(
        reasoning_used=2, tool_actions_used=2
    ).mode == HorizonMode.EXPLORE
    assert controller.horizon_snapshot(
        reasoning_used=11, tool_actions_used=4
    ).mode == HorizonMode.NORMAL
    assert controller.horizon_snapshot(
        reasoning_used=13, tool_actions_used=4
    ).mode == HorizonMode.CONVERGE
    assert controller.horizon_snapshot(
        reasoning_used=17, tool_actions_used=4
    ).mode == HorizonMode.ENDGAME
    assert controller.horizon_snapshot(
        reasoning_used=19, tool_actions_used=4
    ).mode == HorizonMode.CLOSEOUT


def test_closeout_with_workspace_mutation_forces_verification(tmp_path: Path) -> None:
    controller = _controller(
        tmp_path,
        reasoning_limit=10,
        tool_action_limit=10,
    )
    controller.record_tool_result(
        tool_name="workspace_patch",
        arguments={
            "path": "app.py",
            "old_text": "return a - b",
            "new_text": "return a + b",
        },
        succeeded=True,
        output={"path": "app.py", "replacements": 1},
        step=1,
    )

    intervention = controller.assess_intervention(
        reasoning_used=10,
        tool_actions_used=2,
    )
    assert intervention.kind == InterventionKind.FORCE_VERIFICATION
    controller.apply_intervention(intervention, step=2)
    assert controller.plan.phase == CodingPhase.VERIFY
    assert controller.plan.changed_files == ("app.py",)


def test_snapshot_keeps_pending_commitments_and_progress_visible(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.record_tool_result(
        tool_name="workspace_search",
        arguments={"query": "refresh_token"},
        succeeded=True,
        output={"matches": [{"path": "auth.py", "line": 10}]},
        step=1,
    )

    snapshot = controller.snapshot(reasoning_used=1, tool_actions_used=1)
    payload = snapshot.model_dump(mode="json")

    assert payload["plan"]["phase"] == "diagnose"
    assert len(payload["plan"]["commitments"]) == 1
    assert payload["progress"]["new_evidence_count"] == 1
    assert payload["horizon"]["mode"] == "explore"
