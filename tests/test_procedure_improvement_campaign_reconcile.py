from __future__ import annotations

from pathlib import Path

from harness_x.coding.procedure_improvement_campaign import (
    ProcedureImprovementCampaignRunner,
    ProcedureImprovementCampaignStatus,
    ProcedureImprovementCampaignStore,
)
from harness_x.coding.procedure_reliability import ProcedureReliabilityStore
from harness_x.coding.procedure_revision import (
    ProcedureRevisionProposal,
    ProcedureRevisionState,
    ProcedureRevisionStore,
)
from harness_x.coding.project_memory import ProjectMemoryStore, ProposedProjectProcedure
from harness_x.coding.verification import FileContainsVerificationCheck, VerificationPlan
from harness_x.reasoning import ReasoningCoreInfo


PARENT = ProposedProjectProcedure(
    key="campaign-reconcile-parent",
    statement="Run the targeted test before the full suite",
    steps=("Run targeted pytest", "Run the full suite"),
    task_categories=("python", "testing"),
)


class NoCallCore:
    def __init__(self) -> None:
        self._info = ReasoningCoreInfo(
            name="m31-no-call",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context):  # pragma: no cover - a call is the regression
        raise AssertionError("campaign recovery must not launch another model task")


def _episode(store: ProjectMemoryStore, name: str, *, succeeded: bool = True):
    return store.record_episode(task=name, succeeded=succeeded, source_ref=f"m31-reconcile:{name}")


def test_ready_revision_is_m30_reconciled_before_m31_spends_another_step(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("trial.txt").write_text("bad\n", encoding="utf-8")
    memory_root = source / ".harness-x" / "project-memory"

    memory = ProjectMemoryStore(memory_root, project_key="campaign-reconcile-project")
    parent = memory.support_candidates(_episode(memory, "support-1"), (PARENT,))[0]
    parent = memory.support_candidates(_episode(memory, "support-2"), (PARENT,))[0]

    reliability = ProcedureReliabilityStore(memory_root, project_id=memory.project_id)
    for index in range(2):
        failed = _episode(memory, f"reuse-failed-{index}", succeeded=False)
        reliability.record_usage(procedure=parent, episode=failed, success=False)
    record = reliability.record_for(parent.entry_id)
    assert record is not None and not reliability.is_eligible(parent.entry_id)

    revisions = ProcedureRevisionStore(memory_root, project_id=memory.project_id)
    origin = _episode(memory, "revision-origin", succeeded=False)
    candidate = revisions.propose(
        proposal=ProcedureRevisionProposal(
            parent_procedure_id=parent.entry_id,
            statement="Run the targeted test, classify the failure, then run the full suite",
            steps=("Run targeted pytest", "Classify the failure", "Run the full suite"),
            task_categories=("python", "testing"),
            rationale="Repeated verified failures show that classification is missing.",
        ),
        parent=parent,
        origin_episode=origin,
        reliability=record,
    )

    first_trial = _episode(memory, "trial-success-1")
    candidate = revisions.record_validation(
        candidate_id=candidate.candidate_id,
        episode=first_trial,
        success=True,
    )
    replacement = ProposedProjectProcedure(
        key=candidate.replacement_memory_key,
        statement=candidate.statement,
        steps=candidate.steps,
        task_categories=candidate.task_categories,
    )
    memory.support_candidates(first_trial, (replacement,))

    campaigns = ProcedureImprovementCampaignStore(memory_root, project_id=memory.project_id)
    campaign = campaigns.open_campaign(
        parent=parent,
        reliability_store=reliability,
        revision_store=revisions,
    )
    campaign = campaigns.begin_trial(
        campaign.campaign_id,
        candidate=candidate,
        revision_store=revisions,
    )
    assert campaign.trial_attempts == 1 and campaign.pending_step is not None

    # Simulate a crash after M30 persisted the second successful validation and M28 support,
    # but before M30 persisted its READY -> PROMOTED transition.
    second_trial = _episode(memory, "trial-success-2")
    ready = revisions.record_validation(
        candidate_id=candidate.candidate_id,
        episode=second_trial,
        success=True,
    )
    assert ready.state == ProcedureRevisionState.READY
    replacement_entry = memory.support_candidates(second_trial, (replacement,))[0]
    assert replacement_entry.state.value == "active"
    assert revisions.promoted_candidates() == ()

    report = ProcedureImprovementCampaignRunner(
        source,
        NoCallCore(),
        tmp_path / "resume",
        verification_plan=VerificationPlan(
            checks=(
                FileContainsVerificationCheck(
                    check_id="trial_still_bad",
                    name="trial remains source baseline",
                    path="trial.txt",
                    needle="bad",
                ),
            )
        ),
        project_memory_root=memory_root,
        project_key="campaign-reconcile-project",
        isolation_root=tmp_path / "isolated",
        retention="never",
        baseline_verification=False,
        max_reasoning_steps=2,
        max_tool_actions=4,
    ).run(
        parent_procedure_id=parent.entry_id,
        validation_task="Fix trial.txt and pass verification",
    )

    assert report.campaign.status == ProcedureImprovementCampaignStatus.PROMOTED
    assert report.campaign.promoted_candidate_id == candidate.candidate_id
    assert report.campaign.trial_attempts == 1
    assert report.campaign.proposal_attempts == 0
    assert report.step_reports == ()
    assert source.joinpath("trial.txt").read_text(encoding="utf-8") == "bad\n"

    reopened = ProcedureRevisionStore(memory_root, project_id=memory.project_id)
    promoted = reopened.candidate(candidate.candidate_id)
    assert promoted.state == ProcedureRevisionState.PROMOTED
    assert promoted.replacement_entry_id == replacement_entry.entry_id
