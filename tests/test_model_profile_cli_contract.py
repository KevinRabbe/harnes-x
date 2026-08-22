from __future__ import annotations

import subprocess
from pathlib import Path

from harness_x.coding.campaign_cli import build_parser as build_campaign_parser
from harness_x.coding.cli import build_parser as build_coding_parser
from harness_x.coding.model_selection import resolve_model_selection
from harness_x.reasoning import load_model_profile_registry


def test_installed_coding_cli_help_exposes_explicit_profile_selection() -> None:
    result = subprocess.run(
        ["harness-x-code", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--model-profile" in result.stdout
    assert "--model-profile-file" in result.stdout
    assert "--reasoning-effort" in result.stdout


def test_installed_campaign_cli_help_exposes_same_profile_selection() -> None:
    result = subprocess.run(
        ["harness-x-improve-procedure", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--model-profile" in result.stdout
    assert "--model-profile-file" in result.stdout
    assert "--reasoning-effort" in result.stdout


def test_campaign_cli_accepts_reasoning_profile_without_automatic_routing() -> None:
    args = build_campaign_parser().parse_args(
        [
            ".",
            "--parent-procedure-id",
            "pmem_parent",
            "--task",
            "independently reason about the failure",
            "--verify",
            "python -m pytest",
            "--model-profile",
            "reasoning",
        ]
    )
    selection = resolve_model_selection(args)
    assert selection.profile_id == "reasoning"
    assert selection.role == "reasoning"
    assert selection.model == "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"
    assert selection.prompt_mode == "user_prefix"


def test_personal_example_registry_loads_as_exact_four_profile_shortlist() -> None:
    path = Path("configs/model_profiles.personal.example.json")
    registry = load_model_profile_registry(path)
    assert tuple(item.profile_id for item in registry.profiles) == (
        "main",
        "coder",
        "reasoning",
        "api",
    )


def test_direct_openai_flags_preserve_pre_m32_defaults() -> None:
    args = build_coding_parser().parse_args(
        [
            ".",
            "--task",
            "repair the repository",
            "--verify",
            "python -m pytest",
            "--backend",
            "openai",
            "--model",
            "legacy-local-model",
        ]
    )
    selection = resolve_model_selection(args)
    assert selection.source == "direct_flags"
    assert selection.profile_id is None
    assert selection.base_url == "http://127.0.0.1:8080/v1"
    assert selection.model == "legacy-local-model"
    assert selection.max_output_tokens == 2048
    assert selection.temperature == 0.0
    assert selection.top_p is None
    assert selection.reasoning_effort is None
