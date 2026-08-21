from __future__ import annotations

from pathlib import Path

from harness_x.coding.procedure_improvement_campaign import (
    ProcedureImprovementCampaignBudget,
    ProcedureImprovementCampaignRunner,
    ProcedureImprovementCampaignStatus,
    ProcedureImprovementCampaignStore,
)
from harness_x.coding.procedure_reliability import ProcedureReliabilityStatus, ProcedureReliabilityStore
from harness_x.coding.procedure_revision import ProcedureRevisionState, ProcedureRevisionStore
from harness_x.coding.project_memory import ProjectMemoryStore, ProposedProjectProcedure
from harness_x.coding.verification import FileContainsVerificationCheck, VerificationPlan
from harness_x.reasoning import RawActionProposal, RawProposal, RawReasoningOutput, ReasoningCoreInfo


PARENT = ProposedProjectProcedure(
    key="campaign-targeted-tests",
    statement="Run the targeted test before the full suite",
    steps=("Run targeted pytest", "Run the full suite"),
    task_categories=("python", "testing"),
)


class CampaignCore:
    def __init__(self) -> None:
        self._info = ReasoningCoreInfo(
            name="m31-campaign",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )
        self.proposals = 0
        self.trials = 0

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        instruction = str(context.payload.get("instruction", ""))
        revision = context.payload["sections"]["procedure_revision"]
        if "proposal step" in instruction:
            assert revision["trial_allowed"] is False
            self.proposals += 1
            parent = revision["suspended_parents"][0]
            if self.proposals == 1:
                return RawReasoningOutput(
                    status="continue",
                    proposals=(
                        RawProposal(
                            summary="propose campaign revision",
                            payload={
                                "kind": "procedure_revision_update",
                                "candidates": [
                                    {
                                        "parent_procedure_id": parent["procedure_id"],
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
                                            "Repeated verified reuse failures show that the old "
                                            "procedure skipped failure classification."
                                        ),
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
            return RawReasoningOutput(status="complete")

        assert "validation step" in instruction
        assert revision["trial_allowed"] is True
        self.trials += 1
        candidate = revision["data"]["open_candidates"][0]
        return RawReasoningOutput(
            status="continue" if self.trials in (1, 2) else "complete",
            proposals=(
                RawProposal(
                    summary="trial campaign revision",
                    payload={
                        "kind": "procedure_revision_update",
                        "used_revision_candidate_ids": [candidate["candidate_id"]],
                    },
                ),
            ) if self.trials in (1, 2) else (),
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
            ) if self.trials in (1, 2) else (),
        )


class NoCandidateCore:
    def __init__(self) -> None:
        self._info = ReasoningCoreInfo(
            name="m31-no-candidate",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        return RawReasoningOutput(status="complete")


def _episode(store: ProjectMemoryStore, name: str, *, succeeded: bool = True):
    return store.record_episode(task=name, succeeded=succeeded, source_ref=f"m31:{name}")


def _seed_suspended(root: Path):
    memory = ProjectMemoryStore(root, project_key="campaign-project")
    first = _episode(memory, "support-1")
    memory.support_candidates(first, (PARENT,))
    second = _episode(memory, "support-2")
    parent = memory.support_candidates(second, (PARENT,))[0]
    reliability = ProcedureReliabilityStore(root, project_id=memory.project_id)
    for index in range(2):
        failed = _episode(memory, f"reuse-failed-{index}", succeeded=False)
        reliability.record_usage(procedure=parent, episode=failed, success=False)
    record = reliability.record_for(parent.entry_id)
    assert record is not None and record.status == ProcedureReliabilityStatus.SUSPENDED
    return memory, reliability, parent


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


def test_campaign_autonomously_proposes_trials_and_stops_on_m30_promotion(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("trial.txt").write_text("bad\n", encoding="utf-8")
    memory_root = source / ".harness-x" / "project-memory"
    memory, _, parent = _seed_suspended(memory_root)
    core = CampaignCore()

    report = ProcedureImprovementCampaignRunner(
        source,
        core,
        tmp_path / "campaign-run",
        verification_plan=_plan("good"),
        project_memory_root=memory_root,
        project_key="campaign-project",
        isolation_root=tmp_path / "isolated",
        retention="never",
        baseline_verification=False,
        max_reasoning_steps=4,
        max_tool_actions=8,
    ).run(
        parent_procedure_id=parent.entry_id,
        validation_task="Fix trial.txt from bad to good and pass verification",
    )

    assert report.campaign.status == ProcedureImprovementCampaignStatus.PROMOTED
    assert report.campaign.proposal_attempts == 1
    assert report.campaign.trial_attempts == 2
    assert report.campaign.promoted_candidate_id is not None
    assert len(report.step_reports) == 3
    assert core.proposals == 1
    assert core.trials == 2
    assert source.joinpath("trial.txt").read_text(encoding="utf-8") == "bad\n"

    revisions = ProcedureRevisionStore(memory_root, project_id=memory.project_id)
    promoted = revisions.candidate(report.campaign.promoted_candidate_id)
    assert promoted.state == ProcedureRevisionState.PROMOTED
    assert promoted.success_count == 2
    assert promoted.replacement_entry_id is not None
    campaigns = ProcedureImprovementCampaignStore(memory_root, project_id=memory.project_id)
    persisted = campaigns.campaign(report.campaign.campaign_id)
    assert persisted.status == ProcedureImprovementCampaignStatus.PROMOTED
    assert persisted.pending_step is None


def test_campaign_exhausts_proposal_budget_when_model_never_admits_candidate(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("trial.txt").write_text("bad\n", encoding="utf-8")
    memory_root = source / ".harness-x" / "project-memory"
    _, _, parent = _seed_suspended(memory_root)

    report = ProcedureImprovementCampaignRunner(
        source,
        NoCandidateCore(),
        tmp_path / "empty-campaign",
        verification_plan=_plan("bad"),
        project_memory_root=memory_root,
        project_key="campaign-project",
        budget=ProcedureImprovementCampaignBudget(
            max_candidate_proposals=2,
            max_trial_tasks=3,
        ),
        isolation_root=tmp_path / "isolated-empty",
        retention="never",
        baseline_verification=False,
        max_reasoning_steps=2,
        max_tool_actions=4,
    ).run(
        parent_procedure_id=parent.entry_id,
        validation_task="Keep trial.txt valid",
    )

    assert report.campaign.status == ProcedureImprovementCampaignStatus.EXHAUSTED
    assert report.campaign.proposal_attempts == 2
    assert report.campaign.trial_attempts == 0
    assert report.campaign.candidate_ids == ()
    assert report.campaign.terminal_reason == "proposal_budget_exhausted_without_open_candidate"
    assert len(report.step_reports) == 2


def test_campaign_is_superseded_when_parent_revalidates_before_more_experiments(tmp_path: Path) -> None:
    memory, reliability, parent = _seed_suspended(tmp_path)
    revisions = ProcedureRevisionStore(tmp_path, project_id=memory.project_id)
    campaigns = ProcedureImprovementCampaignStore(tmp_path, project_id=memory.project_id)
    campaign = campaigns.open_campaign(
        parent=parent,
        reliability_store=reliability,
        revision_store=revisions,
    )

    for index in range(2):
        support = _episode(memory, f"fresh-support-{index}")
        updated_parent = memory.support_candidates(support, (PARENT,))[0]
        reliability.observe_verified_support(procedure=updated_parent, episode=support)
    assert reliability.is_eligible(parent.entry_id) is True

    reconciled = campaigns.reconcile(
        campaign.campaign_id,
        reliability_store=reliability,
        revision_store=revisions,
    )
    assert reconciled.status == ProcedureImprovementCampaignStatus.SUPERSEDED
    assert reconciled.proposal_attempts == 0
    assert reconciled.trial_attempts == 0


def test_pending_trial_recovery_consumes_budget_without_replaying_after_m30_delta(tmp_path: Path) -> None:
    memory, reliability, parent = _seed_suspended(tmp_path)
    revisions = ProcedureRevisionStore(tmp_path, project_id=memory.project_id)
    failed = _episode(memory, "revision-origin", succeeded=False)
    candidate = revisions.propose(
        proposal={
            "parent_procedure_id": parent.entry_id,
            "statement": "Run targeted tests, inspect failure, then run the full suite",
            "steps": ["Run targeted pytest", "Inspect failure", "Run the full suite"],
            "rationale": "Address repeated verified reuse failures",
        },
        parent=parent,
        origin_episode=failed,
        reliability=reliability.record_for(parent.entry_id),
    )
    campaigns = ProcedureImprovementCampaignStore(tmp_path, project_id=memory.project_id)
    campaign = campaigns.open_campaign(
        parent=parent,
        reliability_store=reliability,
        revision_store=revisions,
    )
    assert candidate.candidate_id in campaign.candidate_ids
    pending_campaign = campaigns.begin_trial(
        campaign.campaign_id,
        candidate=candidate,
        revision_store=revisions,
    )
    assert pending_campaign.trial_attempts == 1
    assert pending_campaign.pending_step is not None

    validation = _episode(memory, "durable-trial-outcome")
    revisions.record_validation(
        candidate_id=candidate.candidate_id,
        episode=validation,
        success=False,
        failure_mode="simulated crash after M30 closeout",
    )

    reopened = ProcedureImprovementCampaignStore(tmp_path, project_id=memory.project_id)
    recovered = reopened.reconcile(
        campaign.campaign_id,
        reliability_store=reliability,
        revision_store=revisions,
    )
    assert recovered.status == ProcedureImprovementCampaignStatus.ACTIVE
    assert recovered.pending_step is None
    assert recovered.trial_attempts == 1
    assert revisions.candidate(candidate.candidate_id).failure_count == 1
