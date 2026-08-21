"""Operator CLI for the Harness X coding runtime."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path
from typing import Sequence

from harness_x.reasoning import (
    OpenAICompatibleReasoningCore,
    OpenAICompatibleSettings,
    TransformersLocalSettings,
)
from harness_x.reasoning.adapters.repository_coding_transformers import (
    RepositoryCodingTransformersReasoningCore,
)

from .isolation import IsolationRetention
from .verification import (
    CommandVerificationCheck,
    VerificationPlan,
    load_verification_plan,
)
from .verified_runtime import (
    VerifiedIsolatedRepositoryCodingTaskRuntime,
    VerifiedRepositoryCodingTaskRuntime,
)


_DEFAULT_QWEN_MODEL = "Qwen/Qwen3-4B-Instruct-2507"
_PYTHON_ALIASES = frozenset({"python", "python.exe", "python3", "python3.exe"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-x-code",
        description=(
            "Run a bounded repository-aware Harness X coding task in an isolated task workspace."
        ),
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--task", required=True)
    parser.add_argument(
        "--verify",
        action="append",
        default=[],
        help=(
            "Required verification command; repeat for multiple commands. Commands are "
            "compiled into the M25 typed verification plan."
        ),
    )
    parser.add_argument(
        "--verification-plan",
        type=Path,
        default=None,
        help=(
            "Optional JSON VerificationPlan with typed required/advisory checks and "
            "changed-file targeting. Repeatable --verify commands are appended as required "
            "command checks when both inputs are supplied."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=("transformers", "openai"),
        default="transformers",
        help="Reasoning backend; transformers runs the model in this process.",
    )
    parser.add_argument("--model", default=_DEFAULT_QWEN_MODEL)
    parser.add_argument(
        "--revision",
        default=None,
        help="Optional exact Hugging Face model revision for the transformers backend.",
    )
    parser.add_argument(
        "--generation-max-new-tokens",
        type=int,
        default=4096,
        help="Maximum generated tokens for one local Transformers reasoning turn.",
    )
    parser.add_argument(
        "--no-4bit",
        action="store_true",
        help="Disable bitsandbytes 4-bit loading for the transformers backend.",
    )
    parser.add_argument(
        "--local-files-only",
        action="store_true",
        help="Require the transformers backend to use the existing local HF cache.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--max-reasoning-steps", type=int, default=32)
    parser.add_argument("--max-tool-actions", type=int, default=48)
    parser.add_argument("--max-output-tokens", type=int, default=65536)
    parser.add_argument(
        "--no-baseline-verify",
        action="store_true",
        help="Skip the controller-owned baseline verification before the first model turn.",
    )
    parser.add_argument(
        "--max-idle-turns",
        type=int,
        default=3,
        help=(
            "Protocol fallback: fail after this many consecutive no-action continue turns."
        ),
    )
    parser.add_argument(
        "--max-inspection-streak",
        type=int,
        default=6,
        help=(
            "Controller threshold for consecutive inspection actions before an "
            "implementation intervention."
        ),
    )
    parser.add_argument(
        "--max-no-progress-streak",
        type=int,
        default=4,
        help=(
            "Controller threshold for consecutive steps producing no new evidence or "
            "workspace state change."
        ),
    )
    parser.add_argument(
        "--max-same-failure-count",
        type=int,
        default=3,
        help=(
            "Controller threshold for the same verification failure before replanning."
        ),
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help=(
            "Disable M24 task-workspace isolation and operate directly on the supplied "
            "workspace. This is an explicit compatibility/debug escape hatch."
        ),
    )
    parser.add_argument(
        "--isolation-root",
        type=Path,
        default=None,
        help=(
            "Parent directory for isolated task workspaces. Defaults to the OS temporary "
            "directory outside the source checkout."
        ),
    )
    parser.add_argument(
        "--retain-workspace",
        choices=tuple(item.value for item in IsolationRetention),
        default=IsolationRetention.ALWAYS.value,
        help=(
            "Isolated workspace retention policy. Changes are exported to the output "
            "directory before cleanup regardless of this setting."
        ),
    )
    parser.add_argument(
        "--isolation-copy-path",
        action="append",
        default=[],
        help=(
            "Copy an ignored/support path from a Git source into the isolated workspace, "
            "for example --isolation-copy-path node_modules. Repeat as needed."
        ),
    )
    parser.add_argument(
        "--output", type=Path, default=Path(".harness-x/coding-run")
    )
    return parser


def _split_command(command: str) -> tuple[str, ...]:
    parts = tuple(shlex.split(command, posix=os.name != "nt"))
    if not parts:
        raise ValueError("verification command cannot be empty")
    return _normalize_python_argv(parts)


def _normalize_python_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    if argv and Path(argv[0]).name.casefold() in _PYTHON_ALIASES:
        return (sys.executable, *argv[1:])
    return argv


def _normalize_plan_commands(plan: VerificationPlan) -> VerificationPlan:
    checks = []
    changed = False
    for check in plan.checks:
        if isinstance(check, CommandVerificationCheck):
            argv = _normalize_python_argv(check.argv)
            if argv != check.argv:
                check = check.model_copy(update={"argv": argv})
                changed = True
        checks.append(check)
    if not changed:
        return plan
    return VerificationPlan(
        name=plan.name,
        checks=tuple(checks),
        fail_fast_required=plan.fail_fast_required,
    )


def _build_verification_inputs(
    args: argparse.Namespace,
) -> tuple[VerificationPlan, tuple[tuple[str, ...], ...]]:
    commands = tuple(_split_command(item) for item in args.verify)
    plan = (
        _normalize_plan_commands(load_verification_plan(args.verification_plan))
        if args.verification_plan is not None
        else None
    )
    if plan is None and not commands:
        raise ValueError("provide at least one --verify command or --verification-plan")

    if plan is None:
        checks = tuple(
            CommandVerificationCheck(
                check_id=f"command_{index:03d}",
                name=" ".join(command),
                argv=command,
            )
            for index, command in enumerate(commands, 1)
        )
        return VerificationPlan(checks=checks), commands

    if not commands:
        return plan, commands

    used = {item.check_id for item in plan.checks}
    appended = []
    next_index = 1
    for command in commands:
        while f"cli_command_{next_index:03d}" in used:
            next_index += 1
        check_id = f"cli_command_{next_index:03d}"
        used.add(check_id)
        appended.append(
            CommandVerificationCheck(
                check_id=check_id,
                name=" ".join(command),
                argv=command,
            )
        )
        next_index += 1
    merged = VerificationPlan(
        name=plan.name,
        checks=(*plan.checks, *appended),
        fail_fast_required=plan.fail_fast_required,
    )
    return merged, commands


def _build_core(args: argparse.Namespace):
    if args.backend == "transformers":
        return RepositoryCodingTransformersReasoningCore(
            TransformersLocalSettings(
                model=args.model,
                revision=args.revision,
                max_new_tokens=args.generation_max_new_tokens,
                load_in_4bit=not args.no_4bit,
                local_files_only=args.local_files_only,
            )
        )
    return OpenAICompatibleReasoningCore(
        OpenAICompatibleSettings(
            base_url=args.base_url,
            model=args.model,
            api_key_env=args.api_key_env,
            allow_remote_endpoint=args.allow_remote,
        )
    )


def _runtime(args: argparse.Namespace, core, verification_plan: VerificationPlan):
    common = dict(
        verification_plan=verification_plan,
        max_reasoning_steps=args.max_reasoning_steps,
        max_tool_actions=args.max_tool_actions,
        max_output_tokens=args.max_output_tokens,
        baseline_verification=not args.no_baseline_verify,
        max_idle_turns=args.max_idle_turns,
        max_inspection_streak=args.max_inspection_streak,
        max_no_progress_streak=args.max_no_progress_streak,
        max_same_failure_count=args.max_same_failure_count,
    )
    if args.in_place:
        return VerifiedRepositoryCodingTaskRuntime(
            args.workspace,
            core,
            args.output,
            **common,
        )
    return VerifiedIsolatedRepositoryCodingTaskRuntime(
        args.workspace,
        core,
        args.output,
        isolation_root=args.isolation_root,
        retention=IsolationRetention(args.retain_workspace),
        support_paths=tuple(args.isolation_copy_path),
        **common,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        verification_plan, verification_commands = _build_verification_inputs(args)
    except ValueError as exc:
        parser.error(str(exc))

    core = _build_core(args)
    runtime = _runtime(args, core, verification_plan)
    try:
        report = runtime.run(
            args.task,
            verification_commands=verification_commands,
        )
    finally:
        close = getattr(core, "close", None)
        if callable(close):
            close()
    print(report.model_dump_json(indent=2))
    return 0 if report.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
