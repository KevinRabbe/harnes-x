from __future__ import annotations

import sys
from pathlib import Path

from harness_x.coding import CodingTaskRuntime
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
            name="coding-test-core",
            version="coding-test-v1",
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


def test_coding_runtime_edits_workspace_and_requires_verification(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "run"
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
            RawReasoningOutput(status="complete"),
        ]
    )

    report = CodingTaskRuntime(workspace, core, output).run(
        "Create result.txt containing ok",
        verification_commands=(_verify_result_file(),),
    )

    assert report.succeeded is True
    assert report.status == "complete"
    assert report.reasoning_steps == 2
    assert report.tool_actions == 2
    assert report.verification_attempts == 1
    assert report.verification[0].returncode == 0
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "ok"
    assert Path(report.trace_path).exists()
    assert (output / "coding-task-report.json").exists()
    assert any("workspace_write" in context for context in core.contexts)


def test_failed_verification_returns_evidence_to_model_for_repair(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "run"
    core = SequenceReasoningCore(
        [
            RawReasoningOutput(
                status="continue",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_write",
                        arguments={"path": "result.txt", "content": "bad"},
                    ),
                ),
            ),
            RawReasoningOutput(status="complete"),
            RawReasoningOutput(
                status="continue",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_patch",
                        arguments={
                            "path": "result.txt",
                            "old_text": "bad",
                            "new_text": "ok",
                        },
                    ),
                ),
            ),
            RawReasoningOutput(status="complete"),
        ]
    )

    report = CodingTaskRuntime(workspace, core, output).run(
        "Create a verified result file",
        verification_commands=(_verify_result_file(),),
    )

    assert report.succeeded is True
    assert report.reasoning_steps == 4
    assert report.verification_attempts == 2
    assert report.tool_actions == 4
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "ok"
    assert any("verification_failure" in context for context in core.contexts[2:])
