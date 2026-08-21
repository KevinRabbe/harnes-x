from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from harness_x.coding.cli import (
    _build_verification_inputs,
    _split_command,
    build_parser,
)
from harness_x.coding.verification import CommandVerificationCheck


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
    assert args.verification_plan is None
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


def test_verify_commands_compile_to_required_typed_plan() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            ".",
            "--task",
            "Build",
            "--verify",
            "python -m pytest -q",
            "--verify",
            "npm run build",
        ]
    )

    plan, commands = _build_verification_inputs(args)

    assert commands[0] == (sys.executable, "-m", "pytest", "-q")
    assert commands[1] == ("npm", "run", "build")
    assert [item.check_id for item in plan.checks] == ["command_001", "command_002"]
    assert all(isinstance(item, CommandVerificationCheck) for item in plan.checks)
    assert all(item.requirement.value == "required" for item in plan.checks)


def test_json_plan_can_be_used_without_legacy_verify_commands(tmp_path: Path) -> None:
    plan_path = tmp_path / "verification.json"
    plan_path.write_text(
        json.dumps(
            {
                "name": "project quality",
                "checks": [
                    {
                        "kind": "file_contains",
                        "check_id": "version_marker",
                        "name": "version marker",
                        "path": "app.py",
                        "needle": "VERSION",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        [
            ".",
            "--task",
            "Build",
            "--verification-plan",
            str(plan_path),
        ]
    )

    plan, commands = _build_verification_inputs(args)

    assert commands == ()
    assert plan.name == "project quality"
    assert [item.check_id for item in plan.checks] == ["version_marker"]


def test_cli_verify_commands_append_to_json_plan_without_id_collision(tmp_path: Path) -> None:
    plan_path = tmp_path / "verification.json"
    plan_path.write_text(
        json.dumps(
            {
                "checks": [
                    {
                        "kind": "command",
                        "check_id": "cli_command_001",
                        "name": "existing",
                        "argv": ["python", "-m", "pytest"],
                        "requirement": "advisory",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        [
            ".",
            "--task",
            "Build",
            "--verification-plan",
            str(plan_path),
            "--verify",
            "npm run build",
        ]
    )

    plan, commands = _build_verification_inputs(args)

    assert commands == (("npm", "run", "build"),)
    assert [item.check_id for item in plan.checks] == [
        "cli_command_001",
        "cli_command_002",
    ]
    first = plan.checks[0]
    assert isinstance(first, CommandVerificationCheck)
    assert first.argv[0] == sys.executable
    assert first.requirement.value == "advisory"


def test_cli_requires_some_verification_source() -> None:
    args = build_parser().parse_args([".", "--task", "Build"])
    with pytest.raises(ValueError, match="provide at least one"):
        _build_verification_inputs(args)
