from __future__ import annotations

from pathlib import Path

import pytest

from harness_x.product import ProjectChatStore


def test_creation_requires_directory_but_existing_project_loads_when_workspace_is_offline(
    tmp_path: Path,
) -> None:
    store = ProjectChatStore(tmp_path / "state")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = store.create_project(name="Project", workspace_root=workspace)

    workspace.rmdir()
    loaded = ProjectChatStore(store.root)
    assert loaded.project(project.project_id) == project
    assert loaded.project_for_workspace(workspace) == project

    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="existing directory"):
        loaded.create_project(name="Missing", workspace_root=missing)


def test_archived_project_continues_to_reserve_canonical_workspace_identity(
    tmp_path: Path,
) -> None:
    store = ProjectChatStore(tmp_path / "state")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = store.create_project(name="Project", workspace_root=workspace)
    store.archive_project(project.project_id)

    with pytest.raises(ValueError, match="already registered"):
        store.create_project(name="Duplicate", workspace_root=workspace)
