from __future__ import annotations

import socket
import sys
from pathlib import Path

from harness_x.browser import ApplicationServerSpec, FakeBrowserProvider
from harness_x.coding.browser_verification import (
    BrowserPageVerificationCheck,
    BrowserVerificationPlan,
)
from harness_x.coding.procedure_reliability import ProcedureReliabilityStore
from harness_x.coding.procedure_reliability_runtime import (
    ProcedureReliabilityBrowserIsolatedRepositoryCodingTaskRuntime,
)
from harness_x.coding.project_memory import ProjectMemoryStore, ProposedProjectProcedure
from harness_x.coding.verification import FileContainsVerificationCheck, VerificationPlan
from harness_x.reasoning import (
    RawActionProposal,
    RawReasoningOutput,
    ReasoningCoreInfo,
)


PROCEDURE = ProposedProjectProcedure(
    key="ui-ready-procedure",
    statement="Keep the local browser page aligned with the verified Ready state",
    steps=("Update the page source", "Run code verification", "Run browser verification"),
    task_categories=("browser", "ui"),
)


class SuppressedBrowserCore:
    def __init__(self) -> None:
        self._info = ReasoningCoreInfo(
            name="m29-browser-suppressed",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )
        self.turn = 0
        self.checked = False

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        self.turn += 1
        selected = context.payload["sections"]["project_memory"]["data"]["selected_active_memory"]
        assert not any(row["key"] == "ui-ready-procedure" for row in selected)
        reliability = context.payload["sections"]["procedure_reliability"]["data"]
        assert reliability["suspended_count"] == 1
        self.checked = True
        if self.turn == 1:
            return RawReasoningOutput(
                status="continue",
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
    return store.record_episode(task=name, succeeded=succeeded, source_ref=f"seed:{name}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_suspended_procedure_is_filtered_in_browser_isolated_task_and_state_stays_source_scoped(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("index.html").write_text("Broken\n", encoding="utf-8")
    memory_root = source / ".harness-x" / "project-memory"
    memory = ProjectMemoryStore(memory_root, project_key="browser-project")
    first = _episode(memory, "support-1")
    memory.support_candidates(first, (PROCEDURE,))
    second = _episode(memory, "support-2")
    procedure = memory.support_candidates(second, (PROCEDURE,))[0]
    reliability = ProcedureReliabilityStore(memory_root, project_id=memory.project_id)
    for name in ("reuse-failure-1", "reuse-failure-2"):
        failed = _episode(memory, name, succeeded=False)
        reliability.record_usage(
            procedure=procedure,
            episode=failed,
            success=False,
        )
    assert not reliability.is_eligible(procedure.entry_id)

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

    core = SuppressedBrowserCore()
    report = ProcedureReliabilityBrowserIsolatedRepositoryCodingTaskRuntime(
        source,
        core,
        tmp_path / "run",
        application=application,
        browser_verification_plan=browser_plan,
        browser_provider_factory=provider_factory,
        verification_plan=code_plan,
        project_memory_root=memory_root,
        project_key="browser-project",
        isolation_root=tmp_path / "isolated",
        retention="never",
        baseline_verification=False,
        max_reasoning_steps=6,
        max_tool_actions=20,
    ).run("Make the isolated page report Ready without reusing the suspended procedure")

    assert report.succeeded is True
    assert core.checked is True
    assert report.procedure_reliability_suspended_count == 1
    assert report.procedure_reliability_usage_total == 2
    assert Path(report.procedure_reliability_state_path).parent == memory_root
    assert Path(report.procedure_reliability_usage_path).parent == memory_root
    assert source.joinpath("index.html").read_text(encoding="utf-8") == "Broken\n"
    exported = Path(report.isolation.changed_files_root) / "index.html"
    assert exported.read_text(encoding="utf-8") == "Ready\n"
    assert report.browser_verification_runs[-1].verdict.value == "pass"
