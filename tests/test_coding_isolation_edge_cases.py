from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness_x.coding import (
    IsolationRetention,
    IsolationStrategy,
    TaskWorkspaceIsolationManager,
)


def _commit_repo(root: Path) -> None:
    root.joinpath("app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.invalid"], cwd=root, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Harness X CI"], cwd=root, check=True
    )
    subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)


def test_git_clone_owns_its_object_database_without_alternates(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _commit_repo(source)
    manager = TaskWorkspaceIsolationManager(
        source,
        tmp_path / "artifacts",
        isolation_root=tmp_path / "isolated",
        retention=IsolationRetention.ALWAYS,
    )

    prepared = manager.prepare()

    assert prepared.strategy == IsolationStrategy.GIT_CLONE
    assert not prepared.workspace_root.joinpath(".git/objects/info/alternates").exists()
    source_object = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert subprocess.run(
        ["git", "cat-file", "-e", source_object],
        cwd=prepared.workspace_root,
        check=False,
    ).returncode == 0


def test_support_path_cannot_name_entire_source_workspace(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("app.py").write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="entire source workspace"):
        TaskWorkspaceIsolationManager(
            source,
            tmp_path / "artifacts",
            isolation_root=tmp_path / "isolated",
            support_paths=(".",),
        )


def test_unborn_git_repository_falls_back_to_snapshot_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    source.joinpath("app.py").write_text("uncommitted\n", encoding="utf-8")
    manager = TaskWorkspaceIsolationManager(
        source,
        tmp_path / "artifacts",
        isolation_root=tmp_path / "isolated",
        retention=IsolationRetention.NEVER,
    )

    prepared = manager.prepare()
    assert prepared.strategy == IsolationStrategy.SNAPSHOT_COPY
    assert prepared.workspace_root.joinpath("app.py").read_text(
        encoding="utf-8"
    ) == "uncommitted\n"
    prepared.workspace_root.joinpath("app.py").write_text("task\n", encoding="utf-8")
    result = manager.finalize(succeeded=True)

    assert source.joinpath("app.py").read_text(encoding="utf-8") == "uncommitted\n"
    assert [(item.path, item.change_type) for item in result.changes] == [
        ("app.py", "modified")
    ]
    assert result.retained is False
