from __future__ import annotations

import socket
import sys
from pathlib import Path

from harness_x.browser import ApplicationServerSpec, FakeBrowserProvider
from harness_x.coding.browser_verification import BrowserPageVerificationCheck, BrowserVerificationPlan
from harness_x.coding.procedure_improvement_browser_campaign import (
    ProcedureImprovementBrowserCampaignRunner,
)
from harness_x.coding.procedure_improvement_campaign import ProcedureImprovementCampaignStatus
from harness_x.coding.procedure_reliability import ProcedureReliabilityStore
from harness_x.coding.project_memory import ProjectMemoryStore, ProposedProjectProcedure
from harness_x.coding.verification import FileContainsVerificationCheck, VerificationPlan
from harness_x.reasoning import RawActionProposal, RawProposal, RawReasoningOutput, ReasoningCoreInfo


PARENT = ProposedProjectProcedure(
    key="browser-campaign-parent",
    statement="Update the page source and verify the rendered page",
    steps=("Update page source", "Verify rendered page"),
    task_categories=("browser", "ui"),
)


class BrowserCampaignCore:
    def __init__(self) -> None:
        self._info = ReasoningCoreInfo(
            name="m31-browser-campaign",
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
        if "proposal step" in instruction:
            key = ("proposal", int(revision["data"]["candidate_counts"].get("candidate", 0)))
            if key in self.emitted:
                return RawReasoningOutput(status="complete")
            self.emitted.add(key)
            parent = revision["suspended_parents"][0]
            return RawReasoningOutput(
                status="continue",
                proposals=(
                    RawProposal(
                        summary="propose browser-aware repair",
                        payload={
                            "kind": "procedure_revision_update",
                            "candidates": [
                                {
                                    "parent_procedure_id": parent["procedure_id"],
                                    "statement": (
                                        "Update the page source, inspect the rendered page, "
                                        "then verify the rendered result"
                                    ),
                                    "steps": [
                                        "Update page source",
                                        "Inspect rendered page",
                                        "Verify rendered result",
                                    ],
                                    "task_categories": ["browser", "ui"],
                                    "rationale": (
                                        "The suspended procedure did not require explicit rendered-page inspection."
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
                            "path": "index.html",
                            "old_text": "Broken",
                            "new_text": "Ready",
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
        if key in self.emitted:
            return RawReasoningOutput(status="complete")
        self.emitted.add(key)
        return RawReasoningOutput(
            status="continue",
            proposals=(
                RawProposal(
                    summary="trial browser campaign revision",
                    payload={
                        "kind": "procedure_revision_update",
                        "used_revision_candidate_ids": [candidate["candidate_id"]],
                    },
                ),
            ),
            actions=(
                RawActionProposal(
                    tool_name="workspace_patch",
                    arguments={
                        "mode": "exact",
                        "path": "index.html",
                        "old_text": "Broken",
                        "new_text": "Ready",
                    },
                ),
            ),
        )


def _episode(store: ProjectMemoryStore, name: str, *, succeeded: bool = True):
    return store.record_episode(task=name, succeeded=succeeded, source_ref=f"m31-browser:{name}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_browser_campaign_requires_isolated_code_and_browser_verified_trials(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("index.html").write_text("Broken\n", encoding="utf-8")
    memory_root = source / ".harness-x" / "project-memory"
    memory = ProjectMemoryStore(memory_root, project_key="browser-campaign-project")
    first = _episode(memory, "support-1")
    memory.support_candidates(first, (PARENT,))
    second = _episode(memory, "support-2")
    parent = memory.support_candidates(second, (PARENT,))[0]
    reliability = ProcedureReliabilityStore(memory_root, project_id=memory.project_id)
    for index in range(2):
        failed = _episode(memory, f"reuse-failed-{index}", succeeded=False)
        reliability.record_usage(procedure=parent, episode=failed, success=False)

    port = _free_port()
    application = ApplicationServerSpec(
        argv=(sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"),
        base_url=f"http://127.0.0.1:{port}",
    )
    browser_plan = BrowserVerificationPlan(
        checks=(
            BrowserPageVerificationCheck(
                check_id="page_ready",
                name="page reports Ready",
                snapshot_contains=("Ready",),
            ),
        )
    )
    code_plan = VerificationPlan(
        checks=(
            FileContainsVerificationCheck(
                check_id="source_ready",
                name="source contains Ready",
                path="index.html",
                needle="Ready",
            ),
        )
    )

    def provider_factory(base_url: str, artifact_root: Path):
        return FakeBrowserProvider(
            base_url,
            artifact_root,
            pages={"/": '- heading "Ready" [level=1]'},
        )

    report = ProcedureImprovementBrowserCampaignRunner(
        source,
        BrowserCampaignCore(),
        tmp_path / "browser-campaign",
        application=application,
        browser_verification_plan=browser_plan,
        browser_provider_factory=provider_factory,
        verification_plan=code_plan,
        project_memory_root=memory_root,
        project_key="browser-campaign-project",
        isolation_root=tmp_path / "isolated",
        retention="never",
        baseline_verification=False,
        max_reasoning_steps=4,
        max_tool_actions=10,
    ).run(
        parent_procedure_id=parent.entry_id,
        validation_task="Change the page from Broken to Ready and verify the rendered application",
    )

    assert report.campaign.status == ProcedureImprovementCampaignStatus.PROMOTED
    assert report.campaign.proposal_attempts == 1
    assert report.campaign.trial_attempts == 2
    assert all(step.task_succeeded for step in report.step_reports)
    assert source.joinpath("index.html").read_text(encoding="utf-8") == "Broken\n"
    for step in report.step_reports:
        coding_report = Path(step.output_root) / "coding-task-report.json"
        assert coding_report.exists()
        payload = coding_report.read_text(encoding="utf-8")
        assert "browser_verification_runs" in payload
