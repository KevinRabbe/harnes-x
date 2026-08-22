from __future__ import annotations

import os
from pathlib import Path

import pytest

from harness_x.coding.model_selection import ResolvedModelSelection
from harness_x.coding.run_manifest import (
    build_coding_run_manifest,
    clone_comparison_memory_seed,
    directory_fingerprint,
    harness_package_fingerprint,
    load_coding_run_manifest,
    write_coding_run_manifest,
)


def _selection() -> ResolvedModelSelection:
    return ResolvedModelSelection(
        source="profile",
        profile_id="main",
        role="main",
        backend="openai",
        model="Qwen/Qwen3.8-27B",
        base_url="http://127.0.0.1:8000/v1",
        max_output_tokens=32768,
    )


def test_missing_memory_roots_share_exact_empty_fingerprint(tmp_path: Path) -> None:
    assert directory_fingerprint(tmp_path / "a") == directory_fingerprint(tmp_path / "b")


def test_memory_seed_copy_is_exact_and_target_must_be_fresh(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    seed.joinpath("project-memory.json").write_text('{"revision": 7}\n', encoding="utf-8")
    nested = seed / "nested"
    nested.mkdir()
    nested.joinpath("ledger.jsonl").write_text("one\ntwo\n", encoding="utf-8")
    target = tmp_path / "target"

    copied = clone_comparison_memory_seed(seed, target)

    assert copied == directory_fingerprint(seed)
    assert copied == directory_fingerprint(target)
    with pytest.raises(ValueError, match="absent or empty"):
        clone_comparison_memory_seed(seed, target)


def test_memory_seed_rejects_symlink_when_supported(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = seed / "linked.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    with pytest.raises(ValueError, match="symlink"):
        clone_comparison_memory_seed(seed, tmp_path / "target")


def test_run_manifest_records_starting_memory_harness_and_round_trips(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory = tmp_path / "memory"
    memory.mkdir()
    memory.joinpath("state.json").write_text("stable\n", encoding="utf-8")
    output = tmp_path / "output"

    manifest = build_coding_run_manifest(
        task="Implement the feature",
        workspace_root=workspace,
        output_root=output,
        isolated=True,
        verification_plan_fingerprint="a" * 64,
        browser_verification_plan_fingerprint=None,
        application_spec_fingerprint=None,
        browser_headed=False,
        project_memory_root=memory,
        project_memory_key="logical-project",
        model_selection=_selection(),
        max_reasoning_steps=32,
        max_tool_actions=48,
        max_output_tokens=65536,
        baseline_verification=True,
        max_idle_turns=3,
        max_inspection_streak=6,
        max_no_progress_streak=4,
        max_same_failure_count=3,
        isolation_retention="always",
        isolation_support_paths=("fixtures",),
    )
    path = write_coding_run_manifest(manifest, output)
    loaded = load_coding_run_manifest(path)

    assert loaded == manifest
    assert loaded.starting_project_memory_fingerprint == directory_fingerprint(memory)
    assert loaded.project_memory_root == str(memory.resolve())
    assert loaded.model_selection.profile_id == "main"
    assert loaded.harness_package_fingerprint == harness_package_fingerprint()
    assert len(loaded.harness_package_fingerprint) == 64


def test_manifest_rejects_incomplete_browser_condition_binding(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError, match="present together"):
        build_coding_run_manifest(
            task="Browser task",
            workspace_root=workspace,
            output_root=tmp_path / "output",
            isolated=True,
            verification_plan_fingerprint="a" * 64,
            browser_verification_plan_fingerprint="b" * 64,
            application_spec_fingerprint=None,
            browser_headed=False,
            project_memory_root=tmp_path / "memory",
            project_memory_key="logical-project",
            model_selection=_selection(),
            max_reasoning_steps=32,
            max_tool_actions=48,
            max_output_tokens=65536,
            baseline_verification=True,
            max_idle_turns=3,
            max_inspection_streak=6,
            max_no_progress_streak=4,
            max_same_failure_count=3,
            isolation_retention="always",
        )
