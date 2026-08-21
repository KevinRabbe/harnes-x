from __future__ import annotations

from pathlib import Path

from harness_x.coding.procedure_reliability import ProcedureReliabilityPolicy, ProcedureReliabilityStore
from harness_x.coding.procedure_revision import ProcedureRevisionState, ProcedureRevisionStore
from harness_x.coding.procedure_revision_runtime import (
    ProcedureRevisionIsolatedRepositoryCodingTaskRuntime,
    ProcedureRevisionVerifiedRepositoryCodingTaskRuntime,
)
from harness_x.coding.project_memory import ProjectMemoryStore, ProposedProjectProcedure
from harness_x.coding.verification import FileContainsVerificationCheck, VerificationPlan
from harness_x.reasoning import (
    RawActionProposal,
    RawProposal,
    RawReasoningOutput,
    ReasoningCoreInfo,
)


PARENT = ProposedProjectProcedure(
    key="targeted-tests-first",
    statement="Run the targeted test before the full suite",
    steps=("Run targeted pytest", "Run the full suite"),
    task_categories=("python", "testing"),
)


class RevisionProposalCore:
    def __init__(self, parent_id: str) -> None:
        self.parent_id = parent_id
        self.turn = 0
        self._info = ReasoningCoreInfo(
            name="m30-proposal",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        self.turn += 1
        section = context.payload["sections"]["procedure_revision"]
        assert section["trial_allowed"] is False
        suspended = {row["procedure_id"] for row in section["suspended_parents"]}
        assert self.parent_id in suspended
        if self.turn == 1:
            return RawReasoningOutput(
                status="continue",
                proposals=(
                    RawProposal(
                        summary="propose bounded repair for suspended procedure",
                        payload={
                            "kind": "procedure_revision_update",
                            "candidates": [
                                {
                                    "parent_procedure_id": self.parent_id,
                                    "statement": (
                                        "Run the targeted test, inspect and classify the failure, "
                                        "then run the full suite"
                                    ),
                                    "steps": [
                                        "Run targeted pytest",
                                        "Inspect and classify the targeted failure",
                                        "Run the full suite",
                                    ],
                                    "task_categories": ["python", "testing"],
                                    "rationale": (
                                        "Verified reuse failures show the old procedure skipped "
                                        "failure classification before continuing."
                                    ),
                                }
                            ],
                        },
                    ),
                ),
                actions=(
                    RawActionProposal(
                        tool_name="workspace_read",
                        arguments={"path": "trial.txt"},
                    ),
                ),
            )
        return RawReasoningOutput(status="complete")


class RevisionTrialCore:
    def __init__(self, candidate_id: str) -> None:
        self.candidate_id = candidate_id
        self.turn = 0
        self._info = ReasoningCoreInfo(
            name="m30-trial",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        self.turn += 1
        section = context.payload["sections"]["procedure_revision"]
        assert section["trial_allowed"] is True
        open_ids = {row["candidate_id"] for row in section["data"]["open_candidates"]}
        assert self.candidate_id in open_ids
        selected = context.payload["sections"]["project_memory"]["data"]["selected_active_memory"]
        assert not any(str(row["key"]).startswith("hx-revision/") for row in selected)
        if self.turn == 1:
            return RawReasoningOutput(
                status="continue",
                proposals=(
                    RawProposal(
                        summary="trial the open revision candidate",
                        payload={
                            "kind": "procedure_revision_update",
                            "used_revision_candidate_ids": [self.candidate_id],
                        },
                    ),
                ),
                actions=(
                    RawActionProposal(
                        tool_name="workspace_patch",
                        arguments={
                            "mode": "exact",
                            "path": "trial.txt",
                            "old_text": "bad",
                            "new_text": "good",
                        },
                    ),
                ),
            )
        return RawReasoningOutput(status="complete")


class PromotedRecallCore:
    def __init__(self, parent_id: str) -> None:
        self.parent_id = parent_id
        self.checked = False
        self._info = ReasoningCoreInfo(
            name="m30-promoted-recall",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        selected = context.payload["sections"]["project_memory"]["data"]["selected_active_memory"]
        assert not any(row["entry_id"] == self.parent_id for row in selected)
        replacements = [row for row in selected if str(row["key"]).startswith("hx-revision/")]
        assert len(replacements) == 1
        assert "classify" in replacements[0]["statement"].casefold()
        lineage = context.payload["sections"]["procedure_revision"]["data"]["promoted_lineage"]
        assert len(lineage) == 1
        assert lineage[0]["parent_procedure_id"] == self.parent_id
        self.checked = True
        return RawReasoningOutput(status="complete")


def _plan(needle: str) -> VerificationPlan:
    return VerificationPlan(
        checks=(
            FileContainsVerificationCheck(
                check_id=f"trial_contains_{needle}",
                name=f"trial contains {needle}",
                path="trial.txt",
                needle=needle,
            ),
        )
    )


def _episode(store: ProjectMemoryStore, name: str, *, succeeded: bool = True):
    return store.record_episode(task=name, succeeded=succeeded, source_ref=f"m30:{name}")


def test_suspended_parent_is_revised_only_after_two_isolated_verified_trials(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("trial.txt").write_text("bad\n", encoding="utf-8")
    memory_root = source / ".harness-x" / "project-memory"
    memory = ProjectMemoryStore(memory_root, project_key="revision-project")

    first = _episode(memory, "parent-support-1")
    memory.support_candidates(first, (PARENT,))
    second = _episode(memory, "parent-support-2")
    parent = memory.support_candidates(second, (PARENT,))[0]
    reliability = ProcedureReliabilityStore(
        memory_root,
        project_id=memory.project_id,
        policy=ProcedureReliabilityPolicy(consecutive_failures_to_suspend=1),
    )
    failed = _episode(memory, "verified-parent-reuse-failure", succeeded=False)
    reliability.record_usage(procedure=parent, episode=failed, success=False)
    assert reliability.is_eligible(parent.entry_id) is False

    proposal_report = ProcedureRevisionVerifiedRepositoryCodingTaskRuntime(
        source,
        RevisionProposalCore(parent.entry_id),
        tmp_path / "proposal-run",
        verification_plan=_plan("bad"),
        project_memory_root=memory_root,
        project_key="revision-project",
        baseline_verification=False,
        max_reasoning_steps=4,
        max_tool_actions=8,
    ).run("Propose a bounded revision for the suspended targeted test procedure")

    assert proposal_report.succeeded is True
    assert len(proposal_report.procedure_revision_admitted_candidate_ids) == 1
    candidate_id = proposal_report.procedure_revision_admitted_candidate_ids[0]
    revisions = ProcedureRevisionStore(memory_root, project_id=memory.project_id)
    assert revisions.candidate(candidate_id).state == ProcedureRevisionState.CANDIDATE
    assert revisions.state.validation_total == 0

    first_trial = ProcedureRevisionIsolatedRepositoryCodingTaskRuntime(
        source,
        RevisionTrialCore(candidate_id),
        tmp_path / "trial-run-1",
        verification_plan=_plan("good"),
        project_memory_root=memory_root,
        project_key="revision-project",
        isolation_root=tmp_path / "isolated-1",
        retention="never",
        baseline_verification=False,
        max_reasoning_steps=4,
        max_tool_actions=8,
    ).run("Trial the proposed targeted-test revision in isolation")

    assert first_trial.succeeded is True
    assert first_trial.procedure_revision_validated_candidate_ids == (candidate_id,)
    assert first_trial.procedure_revision_promoted_candidate_ids == ()
    assert source.joinpath("trial.txt").read_text(encoding="utf-8") == "bad\n"
    after_first = ProcedureRevisionStore(memory_root, project_id=memory.project_id)
    assert after_first.candidate(candidate_id).state == ProcedureRevisionState.CANDIDATE
    assert after_first.candidate(candidate_id).success_count == 1

    second_trial = ProcedureRevisionIsolatedRepositoryCodingTaskRuntime(
        source,
        RevisionTrialCore(candidate_id),
        tmp_path / "trial-run-2",
        verification_plan=_plan("good"),
        project_memory_root=memory_root,
        project_key="revision-project",
        isolation_root=tmp_path / "isolated-2",
        retention="never",
        baseline_verification=False,
        max_reasoning_steps=4,
        max_tool_actions=8,
    ).run("Repeat the revision trial in a fresh isolated workspace")

    assert second_trial.succeeded is True
    assert second_trial.procedure_revision_validated_candidate_ids == (candidate_id,)
    assert second_trial.procedure_revision_promoted_candidate_ids == (candidate_id,)
    assert source.joinpath("trial.txt").read_text(encoding="utf-8") == "bad\n"

    promoted_store = ProcedureRevisionStore(memory_root, project_id=memory.project_id)
    promoted = promoted_store.candidate(candidate_id)
    assert promoted.state == ProcedureRevisionState.PROMOTED
    assert promoted.success_count == 2
    assert promoted.replacement_entry_id is not None
    memory_after = ProjectMemoryStore(memory_root, project_key="revision-project")
    replacement = next(
        item for item in memory_after.state.entries if item.entry_id == promoted.replacement_entry_id
    )
    assert replacement.state.value == "active"
    assert replacement.support_count == 2
    assert next(item for item in memory_after.state.entries if item.entry_id == parent.entry_id).state.value == "active"
    assert ProcedureReliabilityStore(memory_root, project_id=memory.project_id).is_eligible(parent.entry_id) is False

    recall_core = PromotedRecallCore(parent.entry_id)
    recall_report = ProcedureRevisionVerifiedRepositoryCodingTaskRuntime(
        source,
        recall_core,
        tmp_path / "recall-run",
        verification_plan=_plan("bad"),
        project_memory_root=memory_root,
        project_key="revision-project",
        baseline_verification=False,
        max_reasoning_steps=2,
        max_tool_actions=4,
    ).run("Use the targeted test failure classification procedure for this project")

    assert recall_report.succeeded is True
    assert recall_core.checked is True
    assert recall_report.procedure_revision_promoted_candidates == 1
