from __future__ import annotations

import sys
from pathlib import Path

from harness_x.coding import AutonomousCodingTaskRuntime
from harness_x.reasoning import (
    RawActionProposal,
    RawReasoningOutput,
    ReasoningCoreInfo,
)


class SequenceReasoningCore:
    def __init__(self, outputs: list[RawReasoningOutput]) -> None:
        self.outputs = list(outputs)
        self.contexts: list[str] = []
        self._info = ReasoningCoreInfo(
            name="autonomous-coding-test-core",
            version="autonomous-coding-test-v1",
            model="deterministic-sequence",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        self.contexts.append(context.serialized)
        if not self.outputs:
            raise RuntimeError("test core ran out of outputs")
        return self.outputs.pop(0)


def _verify_result_file() -> tuple[str, ...]:
    return (
        sys.executable,
        "-c",
        "from pathlib import Path; assert Path('result.txt').read_text(encoding='utf-8') == 'ok'",
    )


def test_baseline_failure_is_visible_before_first_model_action(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    core = SequenceReasoningCore(
        [
            RawReasoningOutput(
                status="continue",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_write",
                        arguments={"path": "result.txt", "content": "ok"},
                    ),
                ),
            ),
            RawReasoningOutput(status="continue"),
            RawReasoningOutput(status="complete"),
        ]
    )

    report = AutonomousCodingTaskRuntime(workspace, core, tmp_path / "run").run(
        "Create result.txt containing ok",
        verification_commands=(_verify_result_file(),),
    )

    assert report.succeeded is True
    assert report.verification_attempts == 2
    assert report.verification[0].returncode == 0
    assert "verification_baseline" in core.contexts[0]
    assert "verification_passed" in core.contexts[2]


def test_no_action_after_mutation_triggers_controller_verification(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    core = SequenceReasoningCore(
        [
            RawReasoningOutput(
                status="continue",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_write",
                        arguments={"path": "result.txt", "content": "ok"},
                    ),
                ),
            ),
            RawReasoningOutput(status="continue"),
            RawReasoningOutput(status="complete"),
        ]
    )

    report = AutonomousCodingTaskRuntime(workspace, core, tmp_path / "run").run(
        "Create a verified result file",
        verification_commands=(_verify_result_file(),),
    )

    assert report.succeeded is True
    assert report.reasoning_steps == 3
    # baseline + controller verification; the final complete turn reuses fresh evidence
    assert report.verification_attempts == 2
    assert report.tool_actions == 3
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "ok"


def test_repeated_no_action_continue_fails_early_as_stalled(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    core = SequenceReasoningCore(
        [
            RawReasoningOutput(status="continue"),
            RawReasoningOutput(status="continue"),
            RawReasoningOutput(status="continue"),
        ]
    )

    report = AutonomousCodingTaskRuntime(
        workspace,
        core,
        tmp_path / "run",
        max_reasoning_steps=24,
        max_idle_turns=3,
        baseline_verification=False,
    ).run(
        "Do something concrete",
        verification_commands=((sys.executable, "-c", "raise SystemExit(0)"),),
    )

    assert report.succeeded is False
    assert report.reasoning_steps == 3
    assert report.failure_reason == "coding_model_stalled_without_action"
    assert any("coding_control_directive" in context for context in core.contexts[1:])


def test_budget_end_preserves_verification_of_final_mutation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    core = SequenceReasoningCore(
        [
            RawReasoningOutput(
                status="continue",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_write",
                        arguments={"path": "result.txt", "content": "ok"},
                    ),
                ),
            )
        ]
    )

    report = AutonomousCodingTaskRuntime(
        workspace,
        core,
        tmp_path / "run",
        max_reasoning_steps=1,
        baseline_verification=False,
    ).run(
        "Create result.txt containing ok",
        verification_commands=(_verify_result_file(),),
    )

    assert report.succeeded is False
    assert report.verification_attempts == 1
    assert report.verification[0].returncode == 0
    assert report.failure_reason == "verification_passed_but_completion_unconfirmed"
