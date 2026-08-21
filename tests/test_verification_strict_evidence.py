from __future__ import annotations

from pathlib import Path

from harness_x.coding.verification import (
    FileContainsVerificationCheck,
    FileExistsVerificationCheck,
    VerificationCheckStatus,
    VerificationPlan,
)
from harness_x.coding.verified_runtime import VerifiedRepositoryCodingTaskRuntime
from harness_x.reasoning import RawReasoningOutput, ReasoningCoreInfo


class SequenceCore:
    def __init__(self, outputs: list[RawReasoningOutput]) -> None:
        self.outputs = list(outputs)
        self._info = ReasoningCoreInfo(
            name="m25-strict-test-core",
            version="m25-strict-v1",
            model="deterministic-sequence",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        if not self.outputs:
            raise RuntimeError("sequence core ran out of outputs")
        return self.outputs.pop(0)


def test_truncated_absence_is_indeterminate_for_positive_assertion(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("large.txt").write_text(
        "a" * 1500 + "NEEDLE\n", encoding="utf-8"
    )
    plan = VerificationPlan(
        checks=(
            FileContainsVerificationCheck(
                check_id="needle_present",
                name="needle is present",
                path="large.txt",
                needle="NEEDLE",
                max_bytes=1024,
            ),
        )
    )
    runtime = VerifiedRepositoryCodingTaskRuntime(
        workspace,
        SequenceCore(
            [
                RawReasoningOutput(status="complete"),
                RawReasoningOutput(status="blocked"),
            ]
        ),
        tmp_path / "run",
        verification_plan=plan,
        baseline_verification=False,
    )

    report = runtime.run("Verify the large file")

    assert report.succeeded is False
    result = report.verification_runs[0].results[0]
    assert result.status == VerificationCheckStatus.ERROR
    assert result.failure_code == "file_content_indeterminate_truncated"
    assert result.evidence["read_truncated"] is True
    assert result.evidence["matched"] is False


def test_truncated_absence_is_indeterminate_for_negative_assertion(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("large.txt").write_text(
        "a" * 1500 + "FORBIDDEN\n", encoding="utf-8"
    )
    plan = VerificationPlan(
        checks=(
            FileContainsVerificationCheck(
                check_id="forbidden_absent",
                name="forbidden marker is absent",
                path="large.txt",
                needle="FORBIDDEN",
                should_contain=False,
                max_bytes=1024,
            ),
        )
    )
    runtime = VerifiedRepositoryCodingTaskRuntime(
        workspace,
        SequenceCore(
            [
                RawReasoningOutput(status="complete"),
                RawReasoningOutput(status="blocked"),
            ]
        ),
        tmp_path / "run",
        verification_plan=plan,
        baseline_verification=False,
    )

    report = runtime.run("Verify the large file")

    assert report.succeeded is False
    result = report.verification_runs[0].results[0]
    assert result.status == VerificationCheckStatus.ERROR
    assert result.failure_code == "file_content_indeterminate_truncated"


def test_truncated_read_remains_conclusive_when_needle_is_observed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("large.txt").write_text(
        "NEEDLE\n" + "a" * 1500, encoding="utf-8"
    )
    plan = VerificationPlan(
        checks=(
            FileContainsVerificationCheck(
                check_id="needle_present",
                name="needle is present",
                path="large.txt",
                needle="NEEDLE",
                max_bytes=1024,
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

    report = runtime.run("Verify the large file")

    assert report.succeeded is True
    result = report.verification_runs[0].results[0]
    assert result.status == VerificationCheckStatus.PASSED
    assert result.evidence["read_truncated"] is True
    assert result.evidence["matched"] is True


def test_m22_failure_identity_uses_typed_m25_signature(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan = VerificationPlan(
        checks=(
            FileExistsVerificationCheck(
                check_id="required_file",
                name="required file exists",
                path="missing.txt",
            ),
        )
    )
    runtime = VerifiedRepositoryCodingTaskRuntime(
        workspace,
        SequenceCore(
            [
                RawReasoningOutput(status="complete"),
                RawReasoningOutput(status="blocked"),
            ]
        ),
        tmp_path / "run",
        verification_plan=plan,
        baseline_verification=False,
    )

    report = runtime.run("Verify required file")
    run = report.verification_runs[0]

    assert run.failure_signature is not None
    assert runtime._verification_failure_signature(report.verification) == run.failure_signature


def test_completion_evidence_binds_exact_typed_run_and_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("ok.txt").write_text("ok\n", encoding="utf-8")
    plan = VerificationPlan(
        checks=(
            FileExistsVerificationCheck(
                check_id="ok_file",
                name="ok file exists",
                path="ok.txt",
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

    report = runtime.run("Confirm ok.txt")
    run = report.verification_runs[0]
    refs = runtime._completion_evidence_refs(
        report.verification, context_fingerprint="context-fingerprint"
    )

    assert report.succeeded is True
    assert f"verification-run:{run.run_fingerprint}" in refs
    assert f"verification-plan:{run.plan_fingerprint}" in refs
    assert f"workspace:{run.workspace_fingerprint_after}" in refs
    assert "reasoning:context-fingerprint" in refs
