from __future__ import annotations

import subprocess
from pathlib import Path

from harness_x.coding.campaign_cli import _runner, build_parser
from harness_x.coding.procedure_improvement_campaign import ProcedureImprovementCampaignRunner
from harness_x.coding.verification import FileExistsVerificationCheck, VerificationPlan
from harness_x.reasoning import RawReasoningOutput, ReasoningCoreInfo


class NoopCore:
    @property
    def info(self) -> ReasoningCoreInfo:
        return ReasoningCoreInfo(
            name="m31-cli-test",
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


def test_installed_campaign_cli_help_smoke() -> None:
    result = subprocess.run(
        ["harness-x-improve-procedure", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "bounded" in result.stdout.lower()
    assert "--parent-procedure-id" in result.stdout
    assert "--max-trial-tasks" in result.stdout


def test_campaign_cli_requires_explicit_parent_and_validation_task() -> None:
    args = build_parser().parse_args(
        [
            ".",
            "--parent-procedure-id",
            "pmem_parent",
            "--task",
            "Repair the feature and pass verification",
            "--verify",
            "python -m pytest",
        ]
    )
    assert args.parent_procedure_id == "pmem_parent"
    assert args.task == "Repair the feature and pass verification"
    assert args.max_candidate_proposals == 3
    assert args.max_trial_tasks == 6
    assert args.retain_workspace == "always"
    assert args.backend == "transformers"


def test_campaign_cli_builds_bounded_isolated_runner_with_operator_budget(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_root = tmp_path / "project-memory"
    output = tmp_path / "campaign-output"
    isolation = tmp_path / "isolated"
    args = build_parser().parse_args(
        [
            str(workspace),
            "--parent-procedure-id",
            "pmem_parent",
            "--task",
            "Repair the feature",
            "--verification-plan",
            str(tmp_path / "unused.json"),
            "--project-memory-root",
            str(memory_root),
            "--project-memory-key",
            "logical-project",
            "--max-candidate-proposals",
            "2",
            "--max-trial-tasks",
            "5",
            "--isolation-root",
            str(isolation),
            "--retain-workspace",
            "never",
            "--output",
            str(output),
        ]
    )

    runner = _runner(args, NoopCore(), _plan())
    assert isinstance(runner, ProcedureImprovementCampaignRunner)
    assert runner.source_root == workspace.resolve()
    assert runner.project_memory_root == memory_root.resolve()
    assert runner.project_key == "logical-project"
    assert runner.budget.max_candidate_proposals == 2
    assert runner.budget.max_trial_tasks == 5
    assert runner.isolation_root == isolation
    assert runner.retention.value == "never"
    assert runner.output_root == output.resolve()
