from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from harness_x.coding.profile_comparison_cli import build_parser as build_compare_parser
from harness_x.coding.profile_run_cli import _validate_profile_run_args, build_parser


def _profile_args(tmp_path: Path, *extra: str):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return build_parser().parse_args(
        [
            str(workspace),
            "--task",
            "Implement feature",
            "--verify",
            "python -m pytest",
            "--output",
            str(tmp_path / "run"),
            *extra,
        ]
    )


def test_profile_run_requires_explicit_profile(tmp_path: Path) -> None:
    args = _profile_args(tmp_path)
    with pytest.raises(ValueError, match="requires explicit --model-profile"):
        _validate_profile_run_args(args)


def test_profile_run_forbids_in_place_execution(tmp_path: Path) -> None:
    args = _profile_args(tmp_path, "--model-profile", "main", "--in-place")
    with pytest.raises(ValueError, match="isolated execution"):
        _validate_profile_run_args(args)


def test_memory_seed_requires_explicit_target_root(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    args = _profile_args(
        tmp_path,
        "--model-profile",
        "main",
        "--comparison-memory-seed",
        str(seed),
    )
    with pytest.raises(ValueError, match="explicit fresh --project-memory-root"):
        _validate_profile_run_args(args)


def test_compare_parser_is_two_run_offline_surface() -> None:
    args = build_compare_parser().parse_args(["left", "right"])
    assert args.left == Path("left")
    assert args.right == Path("right")
    assert args.allow_incomparable is False


def test_installed_m33_commands_expose_help() -> None:
    for command in ("harness-x-profile-run", "harness-x-compare-runs"):
        executable = shutil.which(command)
        assert executable is not None, f"installed command missing: {command}"
        completed = subprocess.run(
            [executable, "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert completed.returncode == 0, completed.stderr
        assert "usage:" in completed.stdout
