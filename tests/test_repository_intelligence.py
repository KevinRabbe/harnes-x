from __future__ import annotations

import subprocess
from pathlib import Path

from harness_x.repository import RepositoryIntelligenceService, SymbolPrecision


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_repository_snapshot_is_bounded_and_precision_is_explicit(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", "[project]\nname='demo'\n")
    _write(
        tmp_path,
        "AGENTS.md",
        "# Agent rules\nRun tests before completion.\n",
    )
    _write(
        tmp_path,
        "src/app.py",
        "import json\n\nclass Service:\n    def run(self, value=1):\n        return json.dumps(value)\n\ndef helper(x):\n    return x\n",
    )
    _write(
        tmp_path,
        "web/main.ts",
        "import { api } from './api'\nexport function boot(name: string) { return api(name) }\nexport interface Config { name: string }\n",
    )
    _write(tmp_path, "tests/test_app.py", "from src.app import helper\n")
    _write(tmp_path, "node_modules/pkg/index.js", "function ignored() {}\n")

    service = RepositoryIntelligenceService(tmp_path, compact_map_chars=4000)
    snapshot = service.snapshot()

    assert snapshot.file_count == 5
    assert set(snapshot.languages) == {"python", "typescript"}
    assert snapshot.manifests == ("pyproject.toml",)
    assert "src" in snapshot.source_roots
    assert "tests" in snapshot.test_roots
    assert snapshot.instructions[0].path == "AGENTS.md"
    assert "Run tests before completion" in snapshot.instructions[0].preview
    assert "node_modules" not in snapshot.compact_map

    service_symbol = next(item for item in snapshot.symbols if item.name == "Service")
    boot_symbol = next(item for item in snapshot.symbols if item.name == "boot")
    assert service_symbol.precision == SymbolPrecision.EXACT_AST
    assert boot_symbol.precision == SymbolPrecision.HEURISTIC
    assert any(edge.target == "json" for edge in snapshot.dependencies)
    assert any(edge.target == "./api" for edge in snapshot.dependencies)


def test_symbol_search_outline_and_references_use_bounded_fallback(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/math_utils.py",
        "def add(a, b):\n    return a + b\n\ndef use():\n    return add(1, 2)\n",
    )
    _write(tmp_path, "tests/test_math.py", "from src.math_utils import add\nassert add(2, 3) == 5\n")
    service = RepositoryIntelligenceService(tmp_path)

    search = service.symbol_search("add")
    assert search.matches[0].qualified_name == "add"
    outline = service.file_outline("src/math_utils.py")
    assert [item.name for item in outline] == ["add", "use"]
    references = service.symbol_references("add", limit=10)
    assert {(item.path, item.line) for item in references} >= {
        ("src/math_utils.py", 1),
        ("src/math_utils.py", 5),
        ("tests/test_math.py", 1),
    }


def test_repository_identity_tracks_git_head_branch_and_dirty_state(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", "VALUE = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Harness X CI"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    service = RepositoryIntelligenceService(tmp_path)
    clean = service.snapshot()
    assert clean.identity.is_git_repository is True
    assert clean.identity.head_sha is not None and len(clean.identity.head_sha) == 40
    assert clean.identity.dirty is False

    _write(tmp_path, "app.py", "VALUE = 2\n")
    dirty = service.snapshot(refresh=True)
    assert dirty.identity.dirty is True
    assert dirty.fingerprint != clean.fingerprint
