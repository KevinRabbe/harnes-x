from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from harness_x.coding.verification import (
    CommandVerificationCheck,
    FileContainsVerificationCheck,
    FileExistsVerificationCheck,
    VerificationPlan,
    VerificationRequirement,
    VerificationVerdict,
)
from harness_x.coding.verified_runtime import (
    VerifiedIsolatedRepositoryCodingTaskRuntime,
    VerifiedRepositoryCodingTaskRuntime,
)
from harness_x.reasoning import RawActionProposal, RawReasoningOutput, ReasoningCoreInfo


class SequenceCore:
    def __init__(self, outputs: list[RawReasoningOutput]) -> None:
        self.outputs = list(outputs)
        self.contexts: list[str] = []
        self._info = ReasoningCoreInfo(
            name="m25-sequence-core",
            version="m25-sequence-v1",
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
            raise RuntimeError("sequence core ran out of outputs")
        return self.outputs.pop(0)


def test_verification_plan_fingerprint_is_stable_and_check_ids_are_unique() -> None:
    check = CommandVerificationCheck(
        check_id="tests",
        name="unit tests",
        argv=("python", "-m", "pytest"),
    )
    first = VerificationPlan(name="quality", checks=(check,))
    second = VerificationPlan(name="quality", checks=(check,))

    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    with pytest.raises(ValueError, match="must be unique"):
        VerificationPlan(name="bad", checks=(check, check))


def test_advisory_failure_is_evidence_but_does_not_block_completion(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("app.py").write_text("VALUE = 1\n", encoding="utf-8")
    plan = VerificationPlan(
        checks=(
            FileExistsVerificationCheck(
                check_id="app_exists",
                name="application file exists",
                path="app.py",
            ),
            FileContainsVerificationCheck(
                check_id="style_hint",
                name="preferred marker is present",
                requirement=VerificationRequirement.ADVISORY,
                path="app.py",
                needle="PREFERRED_MARKER",
            ),
        )
    )
    core = SequenceCore([RawReasoningOutput(status="complete")])
    runtime = VerifiedRepositoryCodingTaskRuntime(
        workspace,
        core,
        tmp_path / "run",
        verification_plan=plan,
        baseline_verification=False,
    )

    report = runtime.run("Confirm the current implementation")

    assert report.succeeded is True
    assert len(report.verification_runs) == 1
    run = report.verification_runs[0]
    assert run.verdict == VerificationVerdict.PASS
    assert run.required_failures == ()
    assert run.advisory_failures == ("style_hint",)
    assert all(item.returncode == 0 for item in report.verification)
    assert any("verification_platform" in context for context in core.contexts)
    assert any("style_hint" in context for context in core.contexts)


def test_required_failure_returns_typed_evidence_then_repair_can_pass(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan = VerificationPlan(
        checks=(
            FileContainsVerificationCheck(
                check_id="result_ok",
                name="result contains ok",
                path="result.txt",
                needle="ok",
            ),
        )
    )
    core = SequenceCore(
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
                            "mode": "exact",
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
    runtime = VerifiedRepositoryCodingTaskRuntime(
        workspace,
        core,
        tmp_path / "run",
        verification_plan=plan,
        baseline_verification=False,
    )

    report = runtime.run("Create a verified result file")

    assert report.succeeded is True
    assert [item.verdict for item in report.verification_runs] == [
        VerificationVerdict.FAIL,
        VerificationVerdict.PASS,
    ]
    assert report.verification_runs[0].required_failures == ("result_ok",)
    assert workspace.joinpath("result.txt").read_text(encoding="utf-8") == "ok"
    assert any("content_expectation_failed" in context for context in core.contexts[2:])
    assert tmp_path.joinpath("run/verification-plan.json").is_file()
    stored_runs = json.loads(
        tmp_path.joinpath("run/verification-runs.json").read_text(encoding="utf-8")
    )
    assert len(stored_runs) == 2


def test_when_changed_skips_irrelevant_required_check(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("src").mkdir()
    workspace.joinpath("src/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    plan = VerificationPlan(
        checks=(
            CommandVerificationCheck(
                check_id="src_check",
                name="source-only failing probe",
                argv=(sys.executable, "-c", "raise SystemExit(7)"),
                when_changed=("src/**",),
            ),
        )
    )
    core = SequenceCore(
        [
            RawReasoningOutput(
                status="continue",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_write",
                        arguments={"path": "notes.txt", "content": "docs only"},
                    ),
                ),
            ),
            RawReasoningOutput(status="complete"),
        ]
    )
    runtime = VerifiedRepositoryCodingTaskRuntime(
        workspace,
        core,
        tmp_path / "run",
        verification_plan=plan,
        baseline_verification=False,
    )

    report = runtime.run("Add a note without touching source code")

    assert report.succeeded is True
    run = report.verification_runs[0]
    assert run.verdict == VerificationVerdict.PASS
    assert run.changed_files == ("notes.txt",)
    assert run.results[0].status.value == "skipped"
    assert run.results[0].applicable is False


def test_verifier_source_mutation_invalidates_otherwise_passing_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("app.py").write_text("before\n", encoding="utf-8")
    script = (
        "from pathlib import Path; "
        "Path('app.py').write_text('changed by verifier\\n', encoding='utf-8')"
    )
    plan = VerificationPlan(
        checks=(
            CommandVerificationCheck(
                check_id="mutating_probe",
                name="mutating probe",
                argv=(sys.executable, "-c", script),
            ),
        )
    )
    core = SequenceCore(
        [
            RawReasoningOutput(status="complete"),
            RawReasoningOutput(status="blocked"),
        ]
    )
    runtime = VerifiedRepositoryCodingTaskRuntime(
        workspace,
        core,
        tmp_path / "run",
        verification_plan=plan,
        baseline_verification=False,
    )

    report = runtime.run("Validate the workspace")

    assert report.succeeded is False
    assert len(report.verification_runs) == 1
    run = report.verification_runs[0]
    assert run.verdict == VerificationVerdict.FAIL
    assert run.workspace_stable is False
    assert "__workspace_stability__" in run.required_failures
    assert any(
        item.failure_code == "workspace_mutated_during_verification"
        for item in run.results
    )


def test_pass_freshness_is_bound_to_exact_workspace_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("app.py").write_text("ok\n", encoding="utf-8")
    plan = VerificationPlan(
        checks=(
            FileContainsVerificationCheck(
                check_id="content",
                name="content remains ok",
                path="app.py",
                needle="ok",
            ),
        )
    )
    runtime = VerifiedRepositoryCodingTaskRuntime(
        workspace,
        SequenceCore([RawReasoningOutput(status="complete")]),
        tmp_path / "run",
        verification_plan=plan,
        baseline_verification=False,
    )

    report = runtime.run("Confirm app.py")
    assert report.succeeded is True
    assert runtime.verification_platform.latest_is_fresh() is True

    workspace.joinpath("app.py").write_text("externally changed\n", encoding="utf-8")
    assert runtime.verification_platform.latest_is_fresh() is False


def test_m25_isolated_runtime_exports_typed_verification_and_preserves_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("result.txt").write_text("bad\n", encoding="utf-8")
    plan = VerificationPlan(
        checks=(
            FileContainsVerificationCheck(
                check_id="result_ok",
                name="result is ok",
                path="result.txt",
                needle="ok",
            ),
        )
    )
    core = SequenceCore(
        [
            RawReasoningOutput(
                status="continue",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_patch",
                        arguments={
                            "mode": "exact",
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

    report = VerifiedIsolatedRepositoryCodingTaskRuntime(
        source,
        core,
        tmp_path / "run",
        verification_plan=plan,
        isolation_root=tmp_path / "isolated",
        retention="never",
        baseline_verification=False,
    ).run("Fix result.txt")

    assert report.succeeded is True
    assert source.joinpath("result.txt").read_text(encoding="utf-8") == "bad\n"
    assert report.verification_runs[-1].verdict == VerificationVerdict.PASS
    assert report.isolation.changed_file_count == 1
    assert tmp_path.joinpath("run/isolation/isolated-changes/files/result.txt").read_text(
        encoding="utf-8"
    ) == "ok\n"
