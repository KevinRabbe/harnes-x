"""Operator CLI for the Harness X coding runtime."""

from __future__ import annotations

import argparse
import os
import shlex
from pathlib import Path
from typing import Sequence

from harness_x.reasoning import OpenAICompatibleReasoningCore, OpenAICompatibleSettings

from .runtime import CodingTaskRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-x-code",
        description="Run a bounded Harness X coding task against a local workspace.",
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--task", required=True)
    parser.add_argument(
        "--verify",
        action="append",
        required=True,
        help=(
            "Verification command; repeat for multiple commands "
            "(for example: --verify \"npm run build\")"
        ),
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default="local-model")
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--max-reasoning-steps", type=int, default=32)
    parser.add_argument("--max-tool-actions", type=int, default=48)
    parser.add_argument("--max-output-tokens", type=int, default=65536)
    parser.add_argument(
        "--output", type=Path, default=Path(".harness-x/coding-run")
    )
    return parser


def _split_command(command: str) -> tuple[str, ...]:
    parts = tuple(shlex.split(command, posix=os.name != "nt"))
    if not parts:
        raise ValueError("verification command cannot be empty")
    return parts


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        verification_commands = tuple(_split_command(item) for item in args.verify)
    except ValueError as exc:
        parser.error(str(exc))

    core = OpenAICompatibleReasoningCore(
        OpenAICompatibleSettings(
            base_url=args.base_url,
            model=args.model,
            api_key_env=args.api_key_env,
            allow_remote_endpoint=args.allow_remote,
        )
    )
    runtime = CodingTaskRuntime(
        args.workspace,
        core,
        args.output,
        max_reasoning_steps=args.max_reasoning_steps,
        max_tool_actions=args.max_tool_actions,
        max_output_tokens=args.max_output_tokens,
    )
    report = runtime.run(args.task, verification_commands=verification_commands)
    print(report.model_dump_json(indent=2))
    return 0 if report.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
