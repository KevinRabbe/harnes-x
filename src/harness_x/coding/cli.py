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

from .repository_runtime import RepositoryAwareAutonomousCodingTaskRuntime


_DEFAULT_QWEN_MODEL = "Qwen/Qwen3-4B-Instruct-2507"
_PYTHON_ALIASES = frozenset({"python", "python.exe", "python3", "python3.exe"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-x-code",
        description="Run a bounded repository-aware Harness X coding task against a local workspace.",
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--task", required=True)
    parser.add_argument(
        "--verify",
        action="append",
        required=True,
        help=(
            "Verification command; repeat for multiple commands "
            '(for example: --verify "npm run build")'
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
        "--output", type=Path, default=Path(".harness-x/coding-run")
    )
    return parser


def _split_command(command: str) -> tuple[str, ...]:
    parts = tuple(shlex.split(command, posix=os.name != "nt"))
    if not parts:
        raise ValueError("verification command cannot be empty")
    # A verifier belongs to the Harness X runtime environment. On Windows,
    # CreateProcess can otherwise resolve a bare `python` to the system install even
    # when harness-x-code itself is running from an activated virtual environment.
    # Preserve explicit interpreter paths, but bind ordinary aliases to the exact
    # interpreter executing Harness X so installed pytest/lint dependencies match.
    if parts[0].casefold() in _PYTHON_ALIASES:
        return (sys.executable, *parts[1:])
    return parts


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        verification_commands = tuple(_split_command(item) for item in args.verify)
    except ValueError as exc:
        parser.error(str(exc))

    core = _build_core(args)
    runtime = RepositoryAwareAutonomousCodingTaskRuntime(
        args.workspace,
        core,
        args.output,
        max_reasoning_steps=args.max_reasoning_steps,
        max_tool_actions=args.max_tool_actions,
        max_output_tokens=args.max_output_tokens,
        baseline_verification=not args.no_baseline_verify,
        max_idle_turns=args.max_idle_turns,
        max_inspection_streak=args.max_inspection_streak,
        max_no_progress_streak=args.max_no_progress_streak,
        max_same_failure_count=args.max_same_failure_count,
    )
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
