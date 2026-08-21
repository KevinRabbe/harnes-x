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
            "--max-inspection-streak",
            "5",
            "--max-no-progress-streak",
            "4",
            "--max-same-failure-count",
            "2",
            "--retain-workspace",
            "on_failure",
            "--isolation-copy-path",
            "node_modules",
        ]
    )
    assert args.task == "Build the site"
    assert args.verify == ["npm run build"]
    assert args.max_reasoning_steps == 12
    assert args.max_tool_actions == 20
    assert args.max_inspection_streak == 5
    assert args.max_no_progress_streak == 4
    assert args.max_same_failure_count == 2
    assert args.retain_workspace == "on_failure"
    assert args.isolation_copy_path == ["node_modules"]


def test_coding_cli_controller_and_isolation_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [".", "--task", "Build the site", "--verify", "npm run build"]
    )
    assert args.max_inspection_streak == 6
    assert args.max_no_progress_streak == 4
    assert args.max_same_failure_count == 3
    assert args.max_idle_turns == 3
    assert args.in_place is False
    assert args.isolation_root is None
    assert args.retain_workspace == "always"
    assert args.isolation_copy_path == []


def test_split_command_preserves_non_python_argv_shape() -> None:
    assert _split_command("npm run build") == ("npm", "run", "build")


def test_split_command_binds_python_alias_to_active_harness_interpreter() -> None:
    assert _split_command("python -m pytest -q") == (
        sys.executable,
        "-m",
        "pytest",
        "-q",
    )
