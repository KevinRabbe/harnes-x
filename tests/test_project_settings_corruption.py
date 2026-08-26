from __future__ import annotations

from pathlib import Path

import pytest

from harness_x.app_server.project_settings_execution import ProjectSettingsExecutionStore
from harness_x.product import ProjectChatStore, ProjectSettingsStore


def test_project_settings_reject_valid_json_with_wrong_top_level_shape(tmp_path: Path) -> None:
    product = ProjectChatStore(tmp_path / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(name="Project", workspace_root=workspace)
    store = ProjectSettingsStore(product)
    path = product.projects_root / project.project_id / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected a JSON object"):
        store.settings(project.project_id)


def test_execution_settings_ledger_rejects_non_object_rows_deterministically(tmp_path: Path) -> None:
    root = tmp_path / "execution"
    store = ProjectSettingsExecutionStore(root)
    store.path.write_text("[]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid project settings execution snapshot line 1"):
        ProjectSettingsExecutionStore(root)
