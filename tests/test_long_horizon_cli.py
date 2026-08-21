from __future__ import annotations

from pathlib import Path

import pytest

from harness_x.coding.cli import _runtime, _validate_resume_args, build_parser
from harness_x.coding.procedure_reliability_runtime import (
    ProcedureReliabilityIsolatedRepositoryCodingTaskRuntime,
    ProcedureReliabilityVerifiedRepositoryCodingTaskRuntime,
)
from harness_x.coding.verification import FileExistsVerificationCheck, VerificationPlan
from harness_x.reasoning import RawReasoningOutput, ReasoningCoreInfo


class NoopCore:
    @property
    def info(self) -> ReasoningCoreInfo:
        return ReasoningCoreInfo(
            name="m29-cli-test",
            version="1",
            model="noop",
            transport="in_process",
            model_inference=False,
        )

    def generate(self, context) -> RawReasoningOutput:
        return RawReasoningOutput(status="blocked")


def _plan() -> VerificationPlan:
    return VerificationPlan(
        checks=(
            FileExistsVerificationCheck(
                check_id="readme",
                name="README exists",
                path="README.md",
            ),
        )
    )


def test_long_horizon_cli_defaults_are_new_state_isolated_and_project_scoped() -> None:
    args = build_parser().parse_args(
        [".", "--task", "Build", "--verify", "python -m pytest"]
    )
    assert args.resume_long_horizon_state is None
    assert args.resume_allow_workspace_drift is False
    assert args.project_memory_root is None
    assert args.project_memory_key is None
    assert args.in_place is False


def test_project_memory_cli_accepts_explicit_root_and_logical_key(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            ".",
            "--task",
            "Build",
            "--verify",
            "python -m pytest",
            "--project-memory-root",
            str(tmp_path / "memory"),
            "--project-memory-key",
            "logical-project",
        ]
    )
    assert args.project_memory_root == tmp_path / "memory"
    assert args.project_memory_key == "logical-project"


def test_resume_requires_in_place_retained_workspace(tmp_path: Path) -> None:
    state = tmp_path / "long-horizon-state.json"
    args = build_parser().parse_args(
        [
            ".",
            "--task",
            "Build",
            "--verify",
            "python -m pytest",
            "--resume-long-horizon-state",
            str(state),
        ]
    )
    with pytest.raises(ValueError, match="requires --in-place"):
        _validate_resume_args(args)


def test_workspace_drift_escape_requires_resume_state() -> None:
    args = build_parser().parse_args(
        [
            ".",
            "--task",
            "Build",
            "--verify",
            "python -m pytest",
            "--in-place",
            "--resume-allow-workspace-drift",
        ]
    )
    with pytest.raises(ValueError, match="requires --resume-long-horizon-state"):
        _validate_resume_args(args)


def test_runtime_selection_uses_m29_by_default(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("README.md").write_text("ok\n", encoding="utf-8")

    isolated_args = build_parser().parse_args(
        [
            str(workspace),
            "--task",
            "Build",
            "--verification-plan",
            str(tmp_path / "unused.json"),
            "--output",
            str(tmp_path / "isolated-run"),
            "--project-memory-root",
            str(tmp_path / "project-memory"),
            "--project-memory-key",
            "logical-project",
        ]
    )
    isolated = _runtime(isolated_args, NoopCore(), _plan())
    assert isinstance(isolated, ProcedureReliabilityIsolatedRepositoryCodingTaskRuntime)

    in_place_args = build_parser().parse_args(
        [
            str(workspace),
            "--task",
            "Build",
            "--verification-plan",
            str(tmp_path / "unused.json"),
            "--in-place",
            "--output",
            str(tmp_path / "in-place-run"),
            "--project-memory-root",
            str(tmp_path / "project-memory"),
            "--project-memory-key",
            "logical-project",
        ]
    )
    direct = _runtime(in_place_args, NoopCore(), _plan())
    assert isinstance(direct, ProcedureReliabilityVerifiedRepositoryCodingTaskRuntime)
