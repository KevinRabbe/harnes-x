"""Operator CLI for bounded M31 procedure-improvement campaigns."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .cli import _build_browser_inputs, _build_core, _build_verification_inputs
from .isolation import IsolationRetention
from .procedure_improvement_browser_campaign import ProcedureImprovementBrowserCampaignRunner
from .procedure_improvement_campaign import (
    ProcedureImprovementCampaignBudget,
    ProcedureImprovementCampaignRunner,
    ProcedureImprovementCampaignStatus,
)


_DEFAULT_QWEN_MODEL = "Qwen/Qwen3-4B-Instruct-2507"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-x-improve-procedure",
        description=(
            "Run one bounded, persistent M31 improvement campaign for an M29-suspended "
            "project procedure using isolated M30 proposal/validation tasks."
        ),
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--parent-procedure-id", required=True)
    parser.add_argument(
        "--task",
        required=True,
        help="Concrete coding/application task used to validate candidate procedure revisions.",
    )
    parser.add_argument(
        "--verify",
        action="append",
        default=[],
        help="Required verification command; repeat for multiple commands.",
    )
    parser.add_argument(
        "--verification-plan",
        type=Path,
        default=None,
        help="Optional JSON VerificationPlan; repeatable --verify commands are appended.",
    )
    parser.add_argument("--application-spec", type=Path, default=None)
    parser.add_argument("--browser-verification-plan", type=Path, default=None)
    parser.add_argument("--browser-headed", action="store_true")
    parser.add_argument("--project-memory-root", type=Path, default=None)
    parser.add_argument("--project-memory-key", default=None)
    parser.add_argument(
        "--max-candidate-proposals",
        type=int,
        default=3,
        help="Maximum isolated candidate-generation tasks for this suspension campaign.",
    )
    parser.add_argument(
        "--max-trial-tasks",
        type=int,
        default=6,
        help="Maximum isolated M30 revision-validation tasks for this suspension campaign.",
    )
    parser.add_argument(
        "--backend",
        choices=("transformers", "openai"),
        default="transformers",
    )
    parser.add_argument("--model", default=_DEFAULT_QWEN_MODEL)
    parser.add_argument("--revision", default=None)
    parser.add_argument("--generation-max-new-tokens", type=int, default=4096)
    parser.add_argument("--no-4bit", action="store_true")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--max-reasoning-steps", type=int, default=32)
    parser.add_argument("--max-tool-actions", type=int, default=48)
    parser.add_argument("--max-output-tokens", type=int, default=65536)
    parser.add_argument("--no-baseline-verify", action="store_true")
    parser.add_argument("--max-idle-turns", type=int, default=3)
    parser.add_argument("--max-inspection-streak", type=int, default=6)
    parser.add_argument("--max-no-progress-streak", type=int, default=4)
    parser.add_argument("--max-same-failure-count", type=int, default=3)
    parser.add_argument("--isolation-root", type=Path, default=None)
    parser.add_argument(
        "--retain-workspace",
        choices=tuple(item.value for item in IsolationRetention),
        default=IsolationRetention.ALWAYS.value,
    )
    parser.add_argument("--isolation-copy-path", action="append", default=[])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".harness-x/procedure-improvement-campaign"),
    )
    return parser


def _runner(args: argparse.Namespace, core, verification_plan, browser_inputs=None):
    budget = ProcedureImprovementCampaignBudget(
        max_candidate_proposals=args.max_candidate_proposals,
        max_trial_tasks=args.max_trial_tasks,
    )
    common = dict(
        verification_plan=verification_plan,
        project_memory_root=args.project_memory_root,
        project_key=args.project_memory_key,
        budget=budget,
        isolation_root=args.isolation_root,
        retention=IsolationRetention(args.retain_workspace),
        support_paths=tuple(args.isolation_copy_path),
        max_reasoning_steps=args.max_reasoning_steps,
        max_tool_actions=args.max_tool_actions,
        max_output_tokens=args.max_output_tokens,
        baseline_verification=not args.no_baseline_verify,
        max_idle_turns=args.max_idle_turns,
        max_inspection_streak=args.max_inspection_streak,
        max_no_progress_streak=args.max_no_progress_streak,
        max_same_failure_count=args.max_same_failure_count,
    )
    if browser_inputs is None:
        return ProcedureImprovementCampaignRunner(
            args.workspace,
            core,
            args.output,
            **common,
        )
    application, browser_plan, provider_factory = browser_inputs
    return ProcedureImprovementBrowserCampaignRunner(
        args.workspace,
        core,
        args.output,
        application=application,
        browser_verification_plan=browser_plan,
        browser_provider_factory=provider_factory,
        **common,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        verification_plan, _ = _build_verification_inputs(args)
        browser_inputs = _build_browser_inputs(args)
        core = _build_core(args)
        runner = _runner(args, core, verification_plan, browser_inputs)
        try:
            report = runner.run(
                parent_procedure_id=args.parent_procedure_id,
                validation_task=args.task,
            )
        finally:
            close = getattr(core, "close", None)
            if callable(close):
                close()
    except ValueError as exc:
        parser.error(str(exc))

    print(report.model_dump_json(indent=2))
    return (
        0
        if report.campaign.status
        in (
            ProcedureImprovementCampaignStatus.PROMOTED,
            ProcedureImprovementCampaignStatus.SUPERSEDED,
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
