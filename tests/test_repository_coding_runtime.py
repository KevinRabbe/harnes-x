from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from harness_x.coding import RepositoryAwareAutonomousCodingTaskRuntime
from harness_x.reasoning import (
    RawActionProposal,
    RawReasoningOutput,
    ReasoningCoreInfo,
)


class CaptureSequenceCore:
    def __init__(self, outputs: list[RawReasoningOutput]) -> None:
        self.outputs = list(outputs)
        self.contexts: list[str] = []
        self._info = ReasoningCoreInfo(
            name="repository-runtime-test-core",
            version="repository-runtime-test-v1",
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


def _verify_file(path: str, expected: str) -> tuple[str, ...]:
    return (
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            f"assert Path({path!r}).read_text(encoding='utf-8') == {expected!r}"
        ),
    )


def test_repository_orientation_reaches_first_reasoning_turn_without_tool_spend(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(
        "# Rules\nKeep public behavior stable.\n",
        encoding="utf-8",
    )
    (workspace / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    core = CaptureSequenceCore([RawReasoningOutput(status="complete")])

    runtime = RepositoryAwareAutonomousCodingTaskRuntime(
        workspace,
        core,
        tmp_path / "run",
        baseline_verification=False,
    )
    report = runtime.run(
        "Confirm the existing implementation is correct",
        verification_commands=(_verify_file("app.py", "VALUE = 1\n"),),
    )

    assert report.succeeded is True
    assert report.reasoning_steps == 1
    assert len(core.contexts) == 1
    first = core.contexts[0]
    assert len(first) <= 40_000
    payload = json.loads(first)
    repository = payload["sections"]["repository_intelligence"]["data"]
    assert "Keep public behavior stable" in first
    assert "app.py" in repository["compact_map"]
    manifest = repository["aci_manifest"]
    manifest_names = {item["name"] for item in manifest}
    assert manifest_names == {
        "repository_map",
        "file_outline",
        "symbol_search",
        "symbol_definition",
        "symbol_references",
        "git_status",
        "git_diff",
        "workspace_list",
        "workspace_read",
        "workspace_search",
        "workspace_write",
        "workspace_patch",
        "process_run",
    }
    patch = next(item for item in manifest if item["name"] == "workspace_patch")
    assert patch["version"] == "workspace-patch-v2"
    assert patch["properties"]["mode"]["enum"] == ["exact", "range"]


def test_guarded_range_mode_keeps_m22_mutation_and_verification_semantics(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "result.txt"
    target.write_text("bad\n", encoding="utf-8")
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    core = CaptureSequenceCore(
        [
            RawReasoningOutput(
                status="continue",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_patch",
                        arguments={
                            "mode": "range",
                            "path": "result.txt",
                            "start_line": 1,
                            "end_line": 1,
                            "expected_sha256": before,
                            "replacement": "ok\n",
                        },
                    ),
                ),
            ),
            RawReasoningOutput(status="continue"),
            RawReasoningOutput(status="complete"),
        ]
    )

    report = RepositoryAwareAutonomousCodingTaskRuntime(
        workspace,
        core,
        tmp_path / "run",
    ).run(
        "Fix result.txt so verification passes",
        verification_commands=(_verify_file("result.txt", "ok\n"),),
    )

    assert report.succeeded is True
    assert target.read_text(encoding="utf-8") == "ok\n"
    # baseline failure + controller verification after the recognized workspace_patch mutation
    assert report.verification_attempts == 2
    assert report.verification[0].returncode == 0
    assert report.final_coding_phase == "complete"
