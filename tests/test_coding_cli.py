from __future__ import annotations

import sys

from harness_x.coding.cli import _split_command, build_parser


def test_coding_cli_exposes_bounded_runtime_options() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            ".",
            "--task",
            "Build the site",
            "--verify",
            "npm run build",
            "--max-reasoning-steps",
            "12",
            "--max-tool-actions",
            "20",
        ]
    )
    assert args.task == "Build the site"
    assert args.verify == ["npm run build"]
    assert args.max_reasoning_steps == 12
    assert args.max_tool_actions == 20


def test_split_command_preserves_non_python_argv_shape() -> None:
    assert _split_command("npm run build") == ("npm", "run", "build")


def test_split_command_binds_python_alias_to_active_harness_interpreter() -> None:
    assert _split_command("python -m pytest -q") == (
        sys.executable,
        "-m",
        "pytest",
        "-q",
    )
