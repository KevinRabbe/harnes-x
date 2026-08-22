from __future__ import annotations

from pathlib import Path

from harness_x.coding.procedure_improvement_campaign import (
    ProcedureImprovementCampaignRunner,
    ProcedureImprovementCampaignStatus,
)
from harness_x.coding.procedure_reliability import ProcedureReliabilityStore
from harness_x.coding.procedure_revision import ProcedureRevisionState, ProcedureRevisionStore
from harness_x.coding.project_memory import ProjectMemoryStore, ProposedProjectProcedure
from harness_x.coding.verification import FileContainsVerificationCheck, VerificationPlan
from harness_x.reasoning import RawActionProposal, RawProposal, RawReasoningOutput, ReasoningCoreInfo


PARENT = ProposedProjectProcedure(
    key="fallback-parent",
    statement="Run the targeted check then the suite",
    steps=("Run targeted check", "Run suite"),
    task_categories=("python", "testing"),
)


class FallbackCore:
    def __init__(self) -> None:
        self._info = ReasoningCoreInfo(
            name="m31-fallback",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )
        self.emitted: set[tuple[object, ...]] = set()

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        instruction = str(context.payload.get("instruction", ""))
        revision = context.payload["sections"]["procedure_revision"]
        counts = revision["data"]["candidate_counts"]
        if "proposal step" in instruction:
            rejected = int(counts.get("rejected", 0))
            key = ("proposal", rejected)
            if key in self.emitted:
                return RawReasoningOutput(status="complete")
            self.emitted.add(key)
            parent = revision["suspended_parents"][0]
            if rejected == 0:
                statement = "Run the targeted check and immediately retry without diagnosis"
                steps = ["Run targeted check", "Retry immediately", "Run suite"]
                rationale = "First bounded hypothesis: a direct retry may recover the failure."
            else:
                statement = "Run the targeted check, classify the failure, fix it, then run the suite"
                steps = [
                    "Run targeted check",
                    "Classify the failure",
                    "Apply the bounded fix",
                    "Run suite",
                ]
                rationale = "The first revision failed twice; add explicit failure classification."
            return RawReasoningOutput(
                status="continue",
                proposals=(
                    RawProposal(
                        summary="propose next bounded revision",
                        payload={
                            "kind": "procedure_revision_update",
                            "candidates": [
                                {
                                    "parent_procedure_id": parent["procedure_id"],
                                    "statement": statement,
                                    "steps": steps,
                                    "task_categories": ["python", "testing"],
                                    "rationale": rationale,
                                }
                            ],
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

        candidate = revision["data"]["open_candidates"][0]
        key = (
            "trial",
            candidate["candidate_id"],
            int(candidate["success_count"]),
            int(candidate["failure_count"]),
        )
        good_revision = "classify" in str(candidate["statement"]).casefold()
        if key in self.emitted:
            return RawReasoningOutput(status="complete" if good_revision else "blocked")
        self.emitted.add(key)
        action = (
            RawActionProposal(
                tool_name="workspace_patch",
                arguments={
                    "mode": "exact",
                    "path": "trial.txt",
                    "old_text": "bad",
                    "new_text": "good",
                },
            )
            if good_revision
            else RawActionProposal(tool_name="workspace_read", arguments={"path": "trial.txt"})
        )
        return RawReasoningOutput(
            status="continue",
            proposals=(
                RawProposal(
                    summary="trial selected campaign candidate",
                    payload={
                        "kind": "procedure_revision_update",
                        "used_revision_candidate_ids": [candidate["candidate_id"]],
                    },
                ),
            ),
            actions=(action,),
        )


def _episode(store: ProjectMemoryStore, name: str, *, succeeded: bool = True):
    return store.record_episode(task=name, succeeded=succeeded, source_ref=f"m31-fallback:{name}")


def test_rejected_revision_causes_bounded_next_candidate_then_promotion(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("trial.txt").write_text("bad\n", encoding="utf-8")
    memory_root = source / ".harness-x" / "project-memory"
    memory = ProjectMemoryStore(memory_root, project_key="fallback-project")
    first = _episode(memory, "support-1")
    memory.support_candidates(first, (PARENT,))
    second = _episode(memory, "support-2")
    parent = memory.support_candidates(second, (PARENT,))[0]
    reliability = ProcedureReliabilityStore(memory_root, project_id=memory.project_id)
    for index in range(2):
        failed = _episode(memory, f"reuse-failed-{index}", succeeded=False)
        reliability.record_usage(procedure=parent, episode=failed, success=False)

    plan = VerificationPlan(
        checks=(
            FileContainsVerificationCheck(
                check_id="trial_good",
                name="trial becomes good",
                path="trial.txt",
                needle="good",
            ),
        )
    )
    report = ProcedureImprovementCampaignRunner(
        source,
        FallbackCore(),
        tmp_path / "campaign",
        verification_plan=plan,
        project_memory_root=memory_root,
        project_key="fallback-project",
        isolation_root=tmp_path / "isolated",
        retention="never",
        baseline_verification=False,
        max_reasoning_steps=4,
        max_tool_actions=8,
    ).run(
        parent_procedure_id=parent.entry_id,
        validation_task="Repair trial.txt and satisfy the configured verification",
    )

    assert report.campaign.status == ProcedureImprovementCampaignStatus.PROMOTED
    assert report.campaign.proposal_attempts == 2
    assert report.campaign.trial_attempts == 4
    assert len(report.campaign.candidate_ids) == 2
    revisions = ProcedureRevisionStore(memory_root, project_id=memory.project_id)
    states = {item.candidate_id: item.state for item in revisions.state.candidates}
    first_id, second_id = report.campaign.candidate_ids
    assert states[first_id] == ProcedureRevisionState.REJECTED
    assert states[second_id] == ProcedureRevisionState.PROMOTED
    assert report.campaign.promoted_candidate_id == second_id
    assert source.joinpath("trial.txt").read_text(encoding="utf-8") == "bad\n"
