from __future__ import annotations

import sys
from pathlib import Path

import pytest

from harness_x.tools import (
    ProcessRunInput,
    WorkspaceListInput,
    WorkspacePatchInput,
    WorkspaceReadInput,
    WorkspaceSearchInput,
    WorkspaceWriteInput,
    build_coding_registry,
    process_run_definition,
    workspace_list_definition,
    workspace_patch_definition,
    workspace_read_definition,
    workspace_search_definition,
    workspace_write_definition,
)


def test_coding_registry_declares_expected_tools(tmp_path: Path) -> None:
    registry = build_coding_registry(tmp_path)
    assert tuple(spec.name for spec in registry.specs()) == (
        "process_run",
        "workspace_list",
        "workspace_patch",
        "workspace_read",
        "workspace_search",
        "workspace_write",
    )


def test_workspace_tools_refuse_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    definition = workspace_read_definition(tmp_path)

    with pytest.raises(ValueError, match="outside its root"):
        definition.handler(WorkspaceReadInput(path="../outside.txt"))


def test_workspace_write_patch_read_search_and_list(tmp_path: Path) -> None:
    write = workspace_write_definition(tmp_path)
    patch = workspace_patch_definition(tmp_path)
    read = workspace_read_definition(tmp_path)
    search = workspace_search_definition(tmp_path)
    listing = workspace_list_definition(tmp_path)

    created = write.handler(
        WorkspaceWriteInput(path="src/app.py", content="value = 'old'\n")
    )
    assert created.created is True
    assert created.path == "src/app.py"

    changed = patch.handler(
        WorkspacePatchInput(
            path="src/app.py",
            old_text="'old'",
            new_text="'new'",
        )
    )
    assert changed.replacements == 1

    loaded = read.handler(WorkspaceReadInput(path="src/app.py"))
    assert loaded.content == "value = 'new'\n"

    matches = search.handler(WorkspaceSearchInput(query="new"))
    assert [(match.path, match.line) for match in matches.matches] == [
        ("src/app.py", 1)
    ]

    entries = listing.handler(WorkspaceListInput(path="src"))
    assert [entry.path for entry in entries.entries] == ["src/app.py"]


def test_workspace_patch_requires_exact_expected_occurrence_count(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("same same", encoding="utf-8")
    patch = workspace_patch_definition(tmp_path)

    with pytest.raises(ValueError, match="expected 1 exact occurrences, found 2"):
        patch.handler(
            WorkspacePatchInput(path="file.txt", old_text="same", new_text="different")
        )

    assert target.read_text(encoding="utf-8") == "same same"


def test_process_run_uses_argv_without_shell_and_returns_output(tmp_path: Path) -> None:
    process = process_run_definition(tmp_path)
    result = process.handler(
        ProcessRunInput(
            argv=(sys.executable, "-c", "print('coding-runtime-ok')"),
            timeout_seconds=10,
        )
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "coding-runtime-ok"


def test_process_run_rejects_mutating_git_and_package_install(tmp_path: Path) -> None:
    process = process_run_definition(tmp_path)

    with pytest.raises(PermissionError, match="read-only git"):
        process.handler(ProcessRunInput(argv=("git", "push")))

    with pytest.raises(PermissionError, match="permits only"):
        process.handler(ProcessRunInput(argv=("npm", "install")))
