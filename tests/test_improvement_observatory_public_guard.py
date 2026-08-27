from __future__ import annotations

from pathlib import Path

import pytest

from harness_x.app_server.improvement_observatory_guard import build_public_improvement_observatory
from harness_x.app_server.improvement_observatory import ObservatorySourceStatus


def test_public_guard_truncates_before_deep_reader_when_unrelated_tree_is_too_large(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".harness-x"
    root.mkdir(parents=True)
    for index in range(513):
        (root / f"unrelated-{index:04d}.txt").write_text("not observatory evidence\n", encoding="utf-8")

    before = sorted(path.name for path in root.iterdir())
    projection = build_public_improvement_observatory(
        project_id="project_fixture",
        workspace_root=workspace,
    )
    after = sorted(path.name for path in root.iterdir())

    assert before == after
    assert projection.observatory_root_present is True
    assert projection.scan_truncated is True
    assert projection.versions == ()
    assert projection.weaknesses == ()
    assert projection.candidates == ()
    assert projection.experiments == ()
    assert projection.promotions == ()
    assert projection.campaigns == ()
    assert len(projection.sources) == 1
    assert projection.sources[0].status == ObservatorySourceStatus.SCAN_TRUNCATED
    assert projection.sources[0].detail == "tree-entry limit reached"


def test_public_guard_does_not_follow_symlinked_directories_during_preflight(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".harness-x"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    for index in range(600):
        (outside / f"outside-{index:04d}.txt").write_text("outside\n", encoding="utf-8")
    try:
        (root / "linked-outside").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this runner")

    projection = build_public_improvement_observatory(
        project_id="project_fixture",
        workspace_root=workspace,
    )
    assert projection.scan_truncated is False
    assert projection.observatory_root_present is True


def test_public_guard_refuses_workspace_root_replaced_by_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.rmdir()
    replacement = tmp_path / "replacement-workspace"
    (replacement / ".harness-x").mkdir(parents=True)
    try:
        workspace.symlink_to(replacement, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this runner")

    projection = build_public_improvement_observatory(
        project_id="project_fixture",
        workspace_root=workspace,
    )

    assert projection.observatory_root_present is False
    assert projection.scan_truncated is False
    assert projection.versions == ()
    assert projection.weaknesses == ()
    assert projection.candidates == ()
    assert projection.experiments == ()
    assert projection.promotions == ()
    assert projection.campaigns == ()
    assert len(projection.sources) == 1
    assert projection.sources[0].record_kind == "observatory_root"
    assert projection.sources[0].status == ObservatorySourceStatus.SYMLINK_REJECTED
