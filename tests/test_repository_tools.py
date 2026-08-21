from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from harness_x.repository import (
    RepositoryIntelligenceService,
    SymbolPrecision,
    SymbolRecord,
    SymbolReference,
    SymbolSearchResult,
)
from harness_x.tools.coding_repository import build_repository_coding_registry
from harness_x.tools.repository import (
    FileOutlineInput,
    GitDiffInput,
    GitStatusInput,
    RepositoryMapInput,
    SymbolReferencesInput,
    SymbolSearchInput,
    WorkspacePatchRangeInput,
)


def _write(root: Path, path: str, content: str) -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


class FakeSemanticProvider:
    name = "fake-lsp"

    def symbol_search(self, query: str, *, limit: int):
        if query != "semantic":
            return None
        return SymbolSearchResult(
            query=query,
            matches=(
                SymbolRecord(
                    path="src/app.py",
                    language="python",
                    name="semantic",
                    qualified_name="semantic",
                    kind="function",
                    line=1,
                    end_line=1,
                    signature="def semantic()",
                    precision=SymbolPrecision.LSP,
                ),
            ),
        )

    def file_outline(self, path: str):
        return None

    def symbol_references(self, name: str, *, limit: int):
        if name != "semantic":
            return None
        return (
            SymbolReference(
                path="src/app.py",
                line=1,
                text="def semantic(): ...",
                precision=SymbolPrecision.LSP,
            ),
        )


def test_repository_registry_extends_coding_tools_without_replacing_them(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "def run():\n    return 1\n")
    service = RepositoryIntelligenceService(tmp_path)
    registry = build_repository_coding_registry(
        tmp_path,
        repository_service=service,
        semantic_provider=FakeSemanticProvider(),
    )
    names = {spec.name for spec in registry.specs()}

    assert {
        "workspace_list",
        "workspace_read",
        "workspace_search",
        "workspace_write",
        "workspace_patch",
        "process_run",
    } <= names
    assert {
        "repository_map",
        "file_outline",
        "symbol_search",
        "symbol_definition",
        "symbol_references",
        "git_status",
        "git_diff",
        "workspace_patch_range",
    } <= names
    assert len(names) == 14

    search_def = registry.require("symbol_search")
    answer = search_def.handler(SymbolSearchInput(query="semantic"))
    assert answer.source == "fake-lsp"
    assert answer.matches[0].precision == SymbolPrecision.LSP

    refs_def = registry.require("symbol_references")
    refs = refs_def.handler(SymbolReferencesInput(name="semantic"))
    assert refs.source == "fake-lsp"
    assert refs.references[0].precision == SymbolPrecision.LSP


def test_repository_map_and_file_outline_are_bounded_structured_reads(tmp_path: Path) -> None:
    target = _write(
        tmp_path,
        "src/app.py",
        "class App:\n    def run(self):\n        return 1\n",
    )
    registry = build_repository_coding_registry(tmp_path)

    repo_map = registry.require("repository_map").handler(RepositoryMapInput())
    assert repo_map.file_count == 1
    assert "src/app.py" in repo_map.compact_map

    outline = registry.require("file_outline").handler(FileOutlineInput(path="src/app.py"))
    assert outline.path == "src/app.py"
    assert outline.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert [item.qualified_name for item in outline.symbols] == ["App", "App.run"]

    with pytest.raises(ValueError, match="outside"):
        registry.require("file_outline").handler(FileOutlineInput(path="../escape.py"))


def test_hash_guarded_range_patch_refuses_stale_file_and_preserves_line_boundary(tmp_path: Path) -> None:
    target = _write(tmp_path, "app.py", "alpha\nbeta\ngamma\n")
    registry = build_repository_coding_registry(tmp_path)
    definition = registry.require("workspace_patch_range")
    before = hashlib.sha256(target.read_bytes()).hexdigest()

    output = definition.handler(
        WorkspacePatchRangeInput(
            path="app.py",
            start_line=2,
            end_line=2,
            expected_sha256=before,
            replacement="BETA",
        )
    )
    assert target.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"
    assert output.sha256_before == before
    assert output.sha256_after != before

    with pytest.raises(ValueError, match="stale file"):
        definition.handler(
            WorkspacePatchRangeInput(
                path="app.py",
                start_line=2,
                end_line=2,
                expected_sha256=before,
                replacement="again",
            )
        )


def test_structured_git_status_and_diff_do_not_mutate_repository(tmp_path: Path) -> None:
    target = _write(tmp_path, "app.py", "VALUE = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Harness X CI"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    target.write_text("VALUE = 2\n", encoding="utf-8")

    registry = build_repository_coding_registry(tmp_path)
    status = registry.require("git_status").handler(GitStatusInput())
    assert status.identity.is_git_repository is True
    assert status.identity.dirty is True
    assert any(item.path == "app.py" and item.worktree_status == "M" for item in status.entries)

    diff = registry.require("git_diff").handler(GitDiffInput(path="app.py"))
    assert "-VALUE = 1" in diff.diff
    assert "+VALUE = 2" in diff.diff
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"
