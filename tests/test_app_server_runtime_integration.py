from __future__ import annotations

import time
from pathlib import Path

from harness_x.app_server.protocol import AppEventKind, AppSessionStatus, CodingSessionRequest
from harness_x.app_server.service import AppServerService
from harness_x.app_server.trace_projection import build_trace_projection_page
from harness_x.coding.verification import FileContainsVerificationCheck, VerificationPlan
from harness_x.reasoning import RawActionProposal, RawReasoningOutput, ReasoningCoreInfo


class _PatchCore:
    def __init__(self) -> None:
        self.turn = 0
        self._info = ReasoningCoreInfo(
            name="m35-full-stack",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        del context
        self.turn += 1
        if self.turn == 1:
            return RawReasoningOutput(
                status="continue",
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


def _wait_terminal(service: AppServerService, session_id: str):
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        snapshot = service.session(session_id)
        if snapshot.status.terminal:
            return snapshot
        time.sleep(0.02)
    raise AssertionError("full-stack app session did not finish")


def test_app_service_runs_real_isolated_m30_stack_and_attaches_authoritative_trace(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("trial.txt").write_text("bad\n", encoding="utf-8")
    plan = VerificationPlan(
        checks=(
            FileContainsVerificationCheck(
                check_id="contains_good",
                name="trial contains good",
                path="trial.txt",
                needle="good",
            ),
        )
    )
    plan_path = tmp_path / "verification.json"
    plan_path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    core = _PatchCore()
    monkeypatch.setattr(
        "harness_x.app_server.service.build_selected_reasoning_core",
        lambda selection: core,
    )

    service = AppServerService(tmp_path / "server")
    try:
        created = service.create_session(
            CodingSessionRequest(
                workspace_root=source,
                task="replace bad with good",
                model_profile="main",
                verification_plan_path=plan_path,
                project_memory_root=tmp_path / "project-memory",
                project_memory_key="m35-full-stack-project",
                baseline_verification=False,
                max_reasoning_steps=6,
                max_tool_actions=10,
            )
        )
        finished = _wait_terminal(service, created.session_id)

        assert finished.status == AppSessionStatus.SUCCEEDED
        assert finished.coding_report_path is not None
        report_path = Path(finished.coding_report_path)
        assert report_path.is_file()
        report_text = report_path.read_text(encoding="utf-8")
        assert '"succeeded": true' in report_text
        assert '"schema_version": "coding-task-report-v21-isolated-procedure-revision"' in report_text
        assert Path(finished.output_root, "model-selection.json").is_file()
        assert source.joinpath("trial.txt").read_text(encoding="utf-8") == "bad\n"
        assert core.turn >= 2

        assert finished.trace_id is not None
        assert finished.trace_path is not None
        trace_path = Path(finished.trace_path)
        assert trace_path.is_file()
        assert trace_path.name == f"{finished.trace_id}.jsonl"
        lifecycle_kinds = tuple(item.kind for item in service.store.events(finished.session_id))
        assert lifecycle_kinds.count(AppEventKind.TRACE_ATTACHED) == 1

        page = build_trace_projection_page(
            session_id=finished.session_id,
            trace_path=finished.trace_path,
            trace_id=finished.trace_id,
            after=0,
            limit=1000,
            terminal=True,
        )
        event_types = {item.event_type for item in page.events}
        assert "reasoning_requested" in event_types
        assert "reasoning_completed" in event_types
        assert "tool_execution_finished" in event_types
        assert "verification_completed" in event_types
        assert "coding_phase_changed" in event_types
        assert page.events[-1].step >= page.events[0].step
    finally:
        service.close()
