from __future__ import annotations

import socket
import sys
from pathlib import Path

from harness_x.browser import ApplicationServerSpec, FakeBrowserProvider
from harness_x.coding.browser_verification import BrowserPageVerificationCheck, BrowserVerificationPlan
from harness_x.coding.procedure_reliability import ProcedureReliabilityPolicy, ProcedureReliabilityStore
from harness_x.coding.procedure_revision import (
    ProcedureRevisionProposal,
    ProcedureRevisionState,
    ProcedureRevisionStore,
)
from harness_x.coding.procedure_revision_runtime import (
    ProcedureRevisionBrowserIsolatedRepositoryCodingTaskRuntime,
)
from harness_x.coding.project_memory import ProjectMemoryStore, ProposedProjectProcedure
from harness_x.coding.verification import FileContainsVerificationCheck, VerificationPlan
from harness_x.reasoning import RawActionProposal, RawProposal, RawReasoningOutput, ReasoningCoreInfo


PARENT = ProposedProjectProcedure(
    key="browser-ready-procedure",
    statement="Update the page source and verify Ready",
    steps=("Update the page source", "Run browser verification"),
    task_categories=("browser", "ui"),
)


class BrowserRevisionTrialCore:
    def __init__(self, candidate_id: str) -> None:
        self.candidate_id = candidate_id
        self.turn = 0
        self.checked = False
        self._info = ReasoningCoreInfo(
            name="m30-browser-revision-trial",
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
        revision = context.payload["sections"]["procedure_revision"]
        assert revision["trial_allowed"] is True
        open_ids = {row["candidate_id"] for row in revision["data"]["open_candidates"]}
        assert self.candidate_id in open_ids
        assert context.payload["sections"]["procedure_reliability"]["data"]["suspended_count"] == 1
        self.checked = True
        if self.turn == 1:
            return RawReasoningOutput(
                status="continue",
                proposals=(
                    RawProposal(
                        summary="trial browser revision candidate",
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
                            "path": "index.html",
                            "old_text": "Broken",
                            "new_text": "Ready",
                        },
                    ),
                ),
            )
        return RawReasoningOutput(status="complete")


def _episode(store: ProjectMemoryStore, name: str, *, succeeded: bool = True):
    return store.record_episode(task=name, succeeded=succeeded, source_ref=f"m30-browser:{name}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_second_revision_validation_can_promote_through_isolated_browser_runtime(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("index.html").write_text("Broken\n", encoding="utf-8")
    memory_root = source / ".harness-x" / "project-memory"
    memory = ProjectMemoryStore(memory_root, project_key="browser-revision-project")

    first = _episode(memory, "support-1")
    memory.support_candidates(first, (PARENT,))
    second = _episode(memory, "support-2")
    parent = memory.support_candidates(second, (PARENT,))[0]
    reliability = ProcedureReliabilityStore(
        memory_root,
        project_id=memory.project_id,
        policy=ProcedureReliabilityPolicy(consecutive_failures_to_suspend=1),
    )
    failed = _episode(memory, "reuse-failed", succeeded=False)
    suspended = reliability.record_usage(procedure=parent, episode=failed, success=False)

    revisions = ProcedureRevisionStore(memory_root, project_id=memory.project_id)
    candidate = revisions.propose(
        proposal=ProcedureRevisionProposal(
            parent_procedure_id=parent.entry_id,
            statement="Update the page source, inspect the rendered page, and verify Ready",
            steps=(
                "Update the page source",
                "Inspect the rendered browser page",
                "Run browser verification",
            ),
            task_categories=("browser", "ui"),
            rationale="The old browser procedure did not require inspecting rendered output.",
        ),
        parent=parent,
        origin_episode=failed,
        reliability=suspended,
    )

    validation_1 = _episode(memory, "manual-isolated-validation-1")
    revisions.record_validation(candidate_id=candidate.candidate_id, episode=validation_1, success=True)
    candidate = revisions.candidate(candidate.candidate_id)
    replacement = ProposedProjectProcedure(
        key=candidate.replacement_memory_key,
        statement=candidate.statement,
        steps=candidate.steps,
        task_categories=candidate.task_categories,
    )
    first_replacement = memory.support_candidates(validation_1, (replacement,))[0]
    assert first_replacement.support_count == 1
    assert revisions.candidate(candidate.candidate_id).state == ProcedureRevisionState.CANDIDATE

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

    core = BrowserRevisionTrialCore(candidate.candidate_id)
    report = ProcedureRevisionBrowserIsolatedRepositoryCodingTaskRuntime(
        source,
        core,
        tmp_path / "browser-trial-run",
        application=application,
        browser_verification_plan=browser_plan,
        browser_provider_factory=provider_factory,
        verification_plan=code_plan,
        project_memory_root=memory_root,
        project_key="browser-revision-project",
        isolation_root=tmp_path / "isolated",
        retention="never",
        baseline_verification=False,
        max_reasoning_steps=5,
        max_tool_actions=12,
    ).run("Validate the browser revision in a fresh isolated application workspace")

    assert report.succeeded is True
    assert core.checked is True
    assert report.procedure_revision_validated_candidate_ids == (candidate.candidate_id,)
    assert report.procedure_revision_promoted_candidate_ids == (candidate.candidate_id,)
    assert report.browser_verification_runs[-1].verdict.value == "pass"
    assert source.joinpath("index.html").read_text(encoding="utf-8") == "Broken\n"
    promoted = ProcedureRevisionStore(memory_root, project_id=memory.project_id).candidate(candidate.candidate_id)
    assert promoted.state == ProcedureRevisionState.PROMOTED
    assert promoted.replacement_entry_id is not None
    assert Path(report.procedure_revision_state_path).parent == memory_root
    assert Path(report.procedure_revision_validation_path).parent == memory_root
