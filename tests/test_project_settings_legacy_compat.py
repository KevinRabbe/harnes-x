from __future__ import annotations

from pathlib import Path

import pytest

from harness_x.app_server.project_settings_execution import compile_project_settings
from harness_x.product import ProjectChatStore, ProjectSettingsStore


def test_legacy_project_with_pre_m73_profile_name_still_loads_settings(tmp_path: Path) -> None:
    product = ProjectChatStore(tmp_path / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(
        name="Legacy project",
        workspace_root=workspace,
        default_model_profile="Legacy Custom/Profile 2025",
    )

    store = ProjectSettingsStore(product)
    settings = store.settings(project.project_id)

    assert settings.schema_version == "project-settings-v1"
    assert settings.project_id == project.project_id
    assert settings.model_profile == "legacy custom/profile 2025"
    assert settings.revision == 1
    assert settings.updated_at == project.created_at
    assert store.persisted(project.project_id) is False

    # Backward-compatible loading does not make a legacy/custom identifier authoritative.
    # Execution still fails closed until the operator selects a current registered profile.
    with pytest.raises(ValueError, match="unknown model profile"):
        compile_project_settings("exec_" + "f" * 32, settings)


def test_legacy_builtin_profile_is_normalized_and_remains_executable(tmp_path: Path) -> None:
    product = ProjectChatStore(tmp_path / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(
        name="Legacy built-in",
        workspace_root=workspace,
        default_model_profile=" MAIN ",
    )

    settings = ProjectSettingsStore(product).settings(project.project_id)
    assert settings.model_profile == "main"
    snapshot = compile_project_settings("exec_" + "e" * 32, settings)
    assert snapshot.model_profile == "main"
