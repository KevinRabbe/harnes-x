from __future__ import annotations

from pathlib import Path

from harness_x.coding.project_memory import ProjectMemoryEntryState, ProjectMemoryStore
from harness_x.coding.project_memory_runtime import (
    ProjectMemoryVerifiedRepositoryCodingTaskRuntime,
)
from harness_x.coding.verification import FileContainsVerificationCheck, VerificationPlan
from harness_x.reasoning import (
    RawActionProposal,
    RawProposal,
    RawReasoningOutput,
    ReasoningCoreInfo,
)


_PROCEDURE_PAYLOAD = {
    "kind": "project_memory_update",
    "candidates": [
        {
            "kind": "procedure",
            "key": "python-test-convention",
            "statement": "For small Python changes, run the targeted test before the full suite",
            "steps": [
                "Run the targeted pytest file for the changed behavior",
                "Run the full pytest suite before completion",
            ],
            "task_categories": ["python", "testing"],
        }
    ],
}


class SequenceCore:
    def __init__(self, outputs: list[RawReasoningOutput], *, name: str) -> None:
        self.outputs = list(outputs)
        self.contexts = []
        self._info = ReasoningCoreInfo(
            name=name,
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        self.contexts.append(context)
        if not self.outputs:
            raise RuntimeError("sequence exhausted")
        return self.outputs.pop(0)


class ReuseCore:
    def __init__(self) -> None:
        self.turn = 0
        self.seen_entry_id: str | None = None
        self._info = ReasoningCoreInfo(
            name="m28-reuse",
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
        project = context.payload["sections"]["project_memory"]["data"]
        selected = project["selected_active_memory"]
        assert selected, "third task did not receive promoted project memory"
        procedure = next(row for row in selected if row["key"] == "python-test-convention")
        self.seen_entry_id = str(procedure["entry_id"])
        assert procedure["state"] == "active"
        assert procedure["support_count"] >= 2
        if self.turn == 1:
            return RawReasoningOutput(
                status="continue",
                proposals=(
                    RawProposal(
                        summary="record actual project procedure reuse",
                        payload={
                            "kind": "project_memory_update",
                            "used_procedure_ids": [self.seen_entry_id],
                        },
                    ),
                ),
                actions=(
                    RawActionProposal(
                        tool_name="workspace_patch",
                        arguments={
                            "mode": "exact",
                            "path": "c.txt",
                            "old_text": "bad",
                            "new_text": "reused",
                        },
                    ),
                ),
            )
        return RawReasoningOutput(status="complete")


def _plan(path: str, needle: str) -> VerificationPlan:
    return VerificationPlan(
        checks=(
            FileContainsVerificationCheck(
                check_id=f"{path.replace('.', '_')}_contains",
                name=f"{path} contains {needle}",
                path=path,
                needle=needle,
            ),
        )
    )


def _candidate_run(
    workspace: Path,
    memory_root: Path,
    output_root: Path,
    *,
    path: str,
    replacement: str,
    task: str,
):
    core = SequenceCore(
        [
            RawReasoningOutput(
                status="continue",
                proposals=(
                    RawProposal(
                        summary="candidate reusable testing procedure",
                        payload=_PROCEDURE_PAYLOAD,
                    ),
                ),
                actions=(
                    RawActionProposal(
                        tool_name="workspace_patch",
                        arguments={
                            "mode": "exact",
                            "path": path,
                            "old_text": "bad",
                            "new_text": replacement,
                        },
                    ),
                ),
            ),
            RawReasoningOutput(status="complete"),
        ],
        name=f"candidate-{path}",
    )
    runtime = ProjectMemoryVerifiedRepositoryCodingTaskRuntime(
        workspace,
        core,
        output_root,
        verification_plan=_plan(path, replacement),
        project_memory_root=memory_root,
        project_key="demo-project",
        baseline_verification=False,
    )
    return runtime.run(task)


def test_three_independent_tasks_promote_then_reuse_verified_project_procedure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        workspace.joinpath(name).write_text("bad\n", encoding="utf-8")
    memory_root = tmp_path / "project-memory"

    first = _candidate_run(
        workspace,
        memory_root,
        tmp_path / "run-1",
        path="a.txt",
        replacement="first",
        task="Fix the first Python testing fixture",
    )
    assert first.succeeded is True
    assert first.project_memory_candidate_entries == 1
    assert first.project_memory_active_entries == 0

    second = _candidate_run(
        workspace,
        memory_root,
        tmp_path / "run-2",
        path="b.txt",
        replacement="second",
        task="Fix the second Python testing fixture",
    )
    assert second.succeeded is True
    assert second.project_memory_active_entries == 1
    assert second.project_memory_candidate_entries == 0

    reuse_core = ReuseCore()
    third_runtime = ProjectMemoryVerifiedRepositoryCodingTaskRuntime(
        workspace,
        reuse_core,
        tmp_path / "run-3",
        verification_plan=_plan("c.txt", "reused"),
        project_memory_root=memory_root,
        project_key="demo-project",
        baseline_verification=False,
    )
    assert "project_memory_recall" in third_runtime.allowed_tools
    third = third_runtime.run(
        "Apply the Python test convention while fixing the third testing fixture"
    )

    assert third.succeeded is True
    assert reuse_core.seen_entry_id is not None
    store = ProjectMemoryStore(memory_root, project_key="demo-project")
    entry = next(item for item in store.state.entries if item.entry_id == reuse_core.seen_entry_id)
    assert entry.state == ProjectMemoryEntryState.ACTIVE
    assert entry.support_count == 2
    assert entry.usage_count == 1
    assert entry.success_count == 1
    assert entry.failure_count == 0
    assert store.state.episode_count == 3


def test_failed_runtime_discards_staged_candidate_and_records_failed_episode(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("result.txt").write_text("bad\n", encoding="utf-8")
    memory_root = tmp_path / "project-memory"
    core = SequenceCore(
        [
            RawReasoningOutput(
                status="blocked",
                proposals=(
                    RawProposal(
                        summary="unverified candidate must not survive",
                        payload=_PROCEDURE_PAYLOAD,
                    ),
                ),
            )
        ],
        name="failed-candidate",
    )
    runtime = ProjectMemoryVerifiedRepositoryCodingTaskRuntime(
        workspace,
        core,
        tmp_path / "failed-run",
        verification_plan=_plan("result.txt", "ok"),
        project_memory_root=memory_root,
        project_key="demo-project",
        baseline_verification=False,
    )

    report = runtime.run("Attempt a failing Python testing task")

    assert report.succeeded is False
    assert report.project_memory_admitted_entry_ids == ()
    store = ProjectMemoryStore(memory_root, project_key="demo-project")
    assert store.state.entries == ()
    assert store.state.episode_count == 1
