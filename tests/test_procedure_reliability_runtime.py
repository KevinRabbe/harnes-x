from __future__ import annotations

from pathlib import Path

from harness_x.coding.procedure_reliability import (
    ProcedureReliabilityStatus,
    ProcedureReliabilityStore,
)
from harness_x.coding.procedure_reliability_runtime import (
    ProcedureReliabilityVerifiedRepositoryCodingTaskRuntime,
)
from harness_x.coding.project_memory import ProjectMemoryEntryState, ProjectMemoryStore
from harness_x.coding.verification import FileContainsVerificationCheck, VerificationPlan
from harness_x.reasoning import (
    RawActionProposal,
    RawProposal,
    RawReasoningOutput,
    ReasoningCoreInfo,
)


PROCEDURE_PAYLOAD = {
    "kind": "project_memory_update",
    "candidates": [
        {
            "kind": "procedure",
            "key": "targeted-tests-first",
            "statement": "Run the targeted test before the full suite for small Python changes",
            "steps": [
                "Run the targeted pytest file",
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


class DeclaredFailureCore:
    def __init__(self, *, name: str) -> None:
        self._info = ReasoningCoreInfo(
            name=name,
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )
        self.seen_entry_id: str | None = None

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        selected = context.payload["sections"]["project_memory"]["data"]["selected_active_memory"]
        procedure = next(row for row in selected if row["key"] == "targeted-tests-first")
        self.seen_entry_id = str(procedure["entry_id"])
        return RawReasoningOutput(
            status="blocked",
            proposals=(
                RawProposal(
                    summary="record procedure reuse before this task failed",
                    payload={
                        "kind": "project_memory_update",
                        "used_procedure_ids": [self.seen_entry_id],
                    },
                ),
            ),
        )


class SuppressedCore:
    def __init__(self) -> None:
        self._info = ReasoningCoreInfo(
            name="m29-suppressed",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )
        self.checked = False

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        selected = context.payload["sections"]["project_memory"]["data"]["selected_active_memory"]
        assert not any(row["key"] == "targeted-tests-first" for row in selected)
        reliability = context.payload["sections"]["procedure_reliability"]["data"]
        assert reliability["suspended_count"] == 1
        self.checked = True
        return RawReasoningOutput(status="blocked")


class RecoveredReuseCore:
    def __init__(self) -> None:
        self._info = ReasoningCoreInfo(
            name="m29-recovered-reuse",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )
        self.turn = 0
        self.seen_entry_id: str | None = None

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        self.turn += 1
        selected = context.payload["sections"]["project_memory"]["data"]["selected_active_memory"]
        procedure = next(row for row in selected if row["key"] == "targeted-tests-first")
        self.seen_entry_id = str(procedure["entry_id"])
        if self.turn == 1:
            return RawReasoningOutput(
                status="continue",
                proposals=(
                    RawProposal(
                        summary="record recovered procedure reuse",
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
                            "path": "f.txt",
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
                        summary="support the reusable targeted-test procedure",
                        payload=PROCEDURE_PAYLOAD,
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
    runtime = ProcedureReliabilityVerifiedRepositoryCodingTaskRuntime(
        workspace,
        core,
        output_root,
        verification_plan=_plan(path, replacement),
        project_memory_root=memory_root,
        project_key="demo-project",
        baseline_verification=False,
    )
    return runtime.run(task)


def _failure_run(
    workspace: Path,
    memory_root: Path,
    output_root: Path,
    *,
    name: str,
):
    core = DeclaredFailureCore(name=name)
    runtime = ProcedureReliabilityVerifiedRepositoryCodingTaskRuntime(
        workspace,
        core,
        output_root,
        verification_plan=_plan("failure.txt", "ok"),
        project_memory_root=memory_root,
        project_key="demo-project",
        baseline_verification=False,
    )
    return core, runtime.run(f"Fail after applying the targeted-test procedure: {name}")


def test_verified_reuse_failures_suspend_then_fresh_support_revalidates_across_tasks(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("a.txt", "b.txt", "d.txt", "e.txt", "f.txt", "failure.txt"):
        workspace.joinpath(name).write_text("bad\n", encoding="utf-8")
    memory_root = tmp_path / "project-memory"

    first = _candidate_run(
        workspace,
        memory_root,
        tmp_path / "run-1",
        path="a.txt",
        replacement="first",
        task="Support targeted testing on the first fixture",
    )
    second = _candidate_run(
        workspace,
        memory_root,
        tmp_path / "run-2",
        path="b.txt",
        replacement="second",
        task="Support targeted testing on the second fixture",
    )
    assert first.succeeded is True
    assert second.succeeded is True
    assert second.project_memory_active_entries == 1

    failure_core_1, failed_1 = _failure_run(
        workspace,
        memory_root,
        tmp_path / "run-3",
        name="failure-1",
    )
    assert failed_1.succeeded is False
    assert failure_core_1.seen_entry_id is not None
    assert failed_1.procedure_reliability_suspended_count == 0

    failure_core_2, failed_2 = _failure_run(
        workspace,
        memory_root,
        tmp_path / "run-4",
        name="failure-2",
    )
    assert failed_2.succeeded is False
    assert failure_core_2.seen_entry_id == failure_core_1.seen_entry_id
    assert failed_2.procedure_reliability_suspended_count == 1
    procedure_id = failure_core_2.seen_entry_id
    assert procedure_id is not None

    historical = ProjectMemoryStore(memory_root, project_key="demo-project")
    entry = next(item for item in historical.state.entries if item.entry_id == procedure_id)
    assert entry.state == ProjectMemoryEntryState.ACTIVE
    assert entry.support_count == 2
    assert entry.failure_count == 2

    suppressed_core = SuppressedCore()
    suppressed_runtime = ProcedureReliabilityVerifiedRepositoryCodingTaskRuntime(
        workspace,
        suppressed_core,
        tmp_path / "run-5",
        verification_plan=_plan("failure.txt", "ok"),
        project_memory_root=memory_root,
        project_key="demo-project",
        baseline_verification=False,
    )
    suppressed = suppressed_runtime.run("Confirm degraded procedure is no longer reusable")
    assert suppressed.succeeded is False
    assert suppressed_core.checked is True
    assert suppressed_runtime.project_memory_store.recall(query="targeted tests") == ()

    third_support = _candidate_run(
        workspace,
        memory_root,
        tmp_path / "run-6",
        path="d.txt",
        replacement="third",
        task="Fresh successful support after suspension one",
    )
    assert third_support.succeeded is True
    assert third_support.procedure_reliability_suspended_count == 1

    fourth_support = _candidate_run(
        workspace,
        memory_root,
        tmp_path / "run-7",
        path="e.txt",
        replacement="fourth",
        task="Fresh successful support after suspension two",
    )
    assert fourth_support.succeeded is True
    assert fourth_support.procedure_reliability_suspended_count == 0

    recovered_core = RecoveredReuseCore()
    recovered_runtime = ProcedureReliabilityVerifiedRepositoryCodingTaskRuntime(
        workspace,
        recovered_core,
        tmp_path / "run-8",
        verification_plan=_plan("f.txt", "reused"),
        project_memory_root=memory_root,
        project_key="demo-project",
        baseline_verification=False,
    )
    recovered = recovered_runtime.run("Reuse the revalidated targeted-test procedure")
    assert recovered.succeeded is True
    assert recovered_core.seen_entry_id == procedure_id
    assert recovered.procedure_reliability_suspended_count == 0

    reliability = ProcedureReliabilityStore(memory_root, project_id=historical.project_id)
    record = reliability.record_for(procedure_id)
    assert record is not None
    assert record.status == ProcedureReliabilityStatus.ELIGIBLE
    assert record.usage_count == 3
    assert record.success_count == 1
    assert record.failure_count == 2
    assert reliability.state.usage_total == 3

    final_memory = ProjectMemoryStore(memory_root, project_key="demo-project")
    final_entry = next(item for item in final_memory.state.entries if item.entry_id == procedure_id)
    assert final_entry.state == ProjectMemoryEntryState.ACTIVE
    assert final_entry.support_count == 4
    assert final_entry.usage_count == 3
    assert final_entry.success_count == 1
    assert final_entry.failure_count == 2
