"""Deterministic repository orientation and symbol fallback indexing."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable

from .contracts import (
    DependencyEdge,
    RepositoryFile,
    RepositoryIdentity,
    RepositoryInstruction,
    RepositorySnapshot,
    SymbolPrecision,
    SymbolRecord,
    SymbolReference,
    SymbolSearchResult,
)

_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".next",
        ".turbo",
        "dist",
        "build",
        "coverage",
        ".harness-x",
    }
)
_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".vue": "vue",
    ".svelte": "svelte",
}
_MANIFEST_NAMES = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "package.json",
        "pnpm-workspace.yaml",
        "yarn.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "composer.json",
        "gemfile",
    }
)
_INSTRUCTION_NAMES = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "CLAUDE.md",
    "README.md",
    "README.rst",
    "README.txt",
)
_JS_SYMBOL_PATTERNS = (
    ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)")),
    ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)")),
    ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*=")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>")),
)
_JS_IMPORT_PATTERN = re.compile(
    r"^\s*(?:import\s+.*?\s+from\s+|import\s*\(|require\s*\()\s*['\"]([^'\"]+)['\"]"
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _run_git(root: Path, *args: str) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return completed.returncode, (completed.stdout or "").strip()


def _is_test_path(path: str) -> bool:
    lowered = path.casefold()
    name = Path(path).name.casefold()
    return (
        "/tests/" in f"/{lowered}/"
        or "/test/" in f"/{lowered}/"
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def _is_probable_source_root(part: str) -> bool:
    return part.casefold() in {
        "src",
        "lib",
        "app",
        "apps",
        "packages",
        "server",
        "client",
        "backend",
        "frontend",
        "web",
    }


def _python_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args: list[str] = []
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults_start = len(positional) - len(node.args.defaults)
    for index, arg in enumerate(positional):
        value = arg.arg
        if index >= defaults_start:
            value += "=?"
        args.append(value)
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    elif node.args.kwonlyargs:
        args.append("*")
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        args.append(arg.arg + ("=?" if default is not None else ""))
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(args)})"


def _python_index(path: str, text: str) -> tuple[list[SymbolRecord], list[DependencyEdge]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], []
    symbols: list[SymbolRecord] = []
    dependencies: list[DependencyEdge] = []

    def visit(nodes: Iterable[ast.stmt], prefix: tuple[str, ...] = ()) -> None:
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                qualified = ".".join((*prefix, node.name))
                symbols.append(
                    SymbolRecord(
                        path=path,
                        language="python",
                        name=node.name,
                        qualified_name=qualified,
                        kind="class",
                        line=node.lineno,
                        end_line=getattr(node, "end_lineno", None),
                        signature=f"class {node.name}",
                        precision=SymbolPrecision.EXACT_AST,
                    )
                )
                visit(node.body, (*prefix, node.name))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = ".".join((*prefix, node.name))
                symbols.append(
                    SymbolRecord(
                        path=path,
                        language="python",
                        name=node.name,
                        qualified_name=qualified,
                        kind=("method" if prefix else "function"),
                        line=node.lineno,
                        end_line=getattr(node, "end_lineno", None),
                        signature=_python_signature(node),
                        precision=SymbolPrecision.EXACT_AST,
                    )
                )
        # Imports are indexed only from the module-level body. Nested imports still
        # appear in text search/LSP later but do not define repository topology here.

    visit(tree.body)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                dependencies.append(
                    DependencyEdge(
                        source_path=path,
                        target=alias.name,
                        precision=SymbolPrecision.EXACT_AST,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            dependencies.append(
                DependencyEdge(
                    source_path=path,
                    target=module,
                    precision=SymbolPrecision.EXACT_AST,
                )
            )
    return symbols, dependencies


def _javascript_index(
    path: str, text: str, *, language: str
) -> tuple[list[SymbolRecord], list[DependencyEdge]]:
    symbols: list[SymbolRecord] = []
    dependencies: list[DependencyEdge] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for kind, pattern in _JS_SYMBOL_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            name = match.group(1)
            suffix = ""
            if match.lastindex and match.lastindex >= 2 and match.group(2) is not None:
                suffix = f"({match.group(2).strip()})"
            symbols.append(
                SymbolRecord(
                    path=path,
                    language=language,
                    name=name,
                    qualified_name=name,
                    kind=kind,
                    line=line_no,
                    signature=(line.strip()[:240] if not suffix else f"{name}{suffix}"),
                    precision=SymbolPrecision.HEURISTIC,
                )
            )
            break
        import_match = _JS_IMPORT_PATTERN.match(line)
        if import_match:
            dependencies.append(
                DependencyEdge(
                    source_path=path,
                    target=import_match.group(1),
                    precision=SymbolPrecision.HEURISTIC,
                )
            )
    return symbols, dependencies


class RepositoryIntelligenceService:
    """Bounded repository map, symbol index, and progressive instruction discovery."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_files: int = 6000,
        max_index_file_bytes: int = 512_000,
        max_symbols: int = 4000,
        max_dependencies: int = 4000,
        instruction_preview_chars: int = 2400,
        compact_map_chars: int = 9000,
    ) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError("repository intelligence root must be an existing directory")
        self.max_files = max_files
        self.max_index_file_bytes = max_index_file_bytes
        self.max_symbols = max_symbols
        self.max_dependencies = max_dependencies
        self.instruction_preview_chars = instruction_preview_chars
        self.compact_map_chars = compact_map_chars
        self._snapshot: RepositorySnapshot | None = None
        self._files: tuple[RepositoryFile, ...] = ()

    def snapshot(self, *, refresh: bool = False) -> RepositorySnapshot:
        if self._snapshot is not None and not refresh:
            return self._snapshot

        identity = self._identity()
        files, inventory_truncated = self._inventory()
        instructions = self._instructions(files)
        symbols: list[SymbolRecord] = []
        dependencies: list[DependencyEdge] = []
        indexed_files = 0

        for item in files:
            if item.language not in {"python", "javascript", "typescript"}:
                continue
            target = self.root / item.path
            if item.size_bytes > self.max_index_file_bytes:
                continue
            try:
                text = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            indexed_files += 1
            if item.language == "python":
                found_symbols, found_dependencies = _python_index(item.path, text)
            else:
                found_symbols, found_dependencies = _javascript_index(
                    item.path, text, language=item.language
                )
            remaining_symbols = max(0, self.max_symbols - len(symbols))
            remaining_dependencies = max(0, self.max_dependencies - len(dependencies))
            symbols.extend(found_symbols[:remaining_symbols])
            dependencies.extend(found_dependencies[:remaining_dependencies])
            if len(symbols) >= self.max_symbols and len(dependencies) >= self.max_dependencies:
                break

        languages = tuple(sorted({item.language for item in files if item.language}))
        manifests = tuple(item.path for item in files if item.is_manifest)
        test_roots = self._roots(files, test=True)
        source_roots = self._roots(files, test=False)
        material = {
            "identity": identity.model_dump(mode="json"),
            "files": [
                {
                    "path": item.path,
                    "size": item.size_bytes,
                    "language": item.language,
                    "manifest": item.is_manifest,
                    "test": item.is_test,
                }
                for item in files
            ],
            "instructions": [item.path for item in instructions],
            "symbols": [
                (item.path, item.qualified_name, item.kind, item.line, item.precision.value)
                for item in symbols
            ],
        }
        fingerprint = hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()
        compact_map = self._render_compact_map(
            identity=identity,
            files=files,
            languages=languages,
            manifests=manifests,
            instructions=instructions,
            symbols=tuple(symbols),
            test_roots=test_roots,
            source_roots=source_roots,
        )
        self._files = files
        self._snapshot = RepositorySnapshot(
            fingerprint=fingerprint,
            identity=identity,
            file_count=len(files),
            indexed_file_count=indexed_files,
            languages=languages,
            manifests=manifests,
            test_roots=test_roots,
            source_roots=source_roots,
            instructions=instructions,
            symbols=tuple(symbols),
            dependencies=tuple(dependencies),
            compact_map=compact_map,
            truncated=(
                inventory_truncated
                or len(symbols) >= self.max_symbols
                or len(dependencies) >= self.max_dependencies
            ),
        )
        return self._snapshot

    def symbol_search(self, query: str, *, limit: int = 30) -> SymbolSearchResult:
        normalized = query.strip().casefold()
        if not normalized:
            raise ValueError("symbol query cannot be blank")
        snapshot = self.snapshot()
        scored: list[tuple[int, str, int, SymbolRecord]] = []
        for item in snapshot.symbols:
            name = item.name.casefold()
            qualified = item.qualified_name.casefold()
            if normalized == name:
                score = 0
            elif normalized == qualified:
                score = 1
            elif name.startswith(normalized):
                score = 2
            elif normalized in name:
                score = 3
            elif normalized in qualified:
                score = 4
            else:
                continue
            scored.append((score, item.path, item.line, item))
        scored.sort(key=lambda row: (row[0], row[1], row[2], row[3].qualified_name))
        matches = tuple(row[3] for row in scored[:limit])
        return SymbolSearchResult(
            query=query.strip(),
            matches=matches,
            truncated=len(scored) > limit,
        )

    def file_outline(self, path: str) -> tuple[SymbolRecord, ...]:
        normalized = Path(path).as_posix().lstrip("./")
        return tuple(
            item for item in self.snapshot().symbols if item.path == normalized
        )

    def symbol_references(
        self,
        name: str,
        *,
        limit: int = 80,
    ) -> tuple[SymbolReference, ...]:
        normalized = name.strip()
        if not normalized:
            raise ValueError("symbol reference name cannot be blank")
        pattern = re.compile(rf"\b{re.escape(normalized)}\b")
        references: list[SymbolReference] = []
        files = self._files or self._inventory()[0]
        for item in files:
            if not item.language or item.size_bytes > self.max_index_file_bytes:
                continue
            try:
                text = (self.root / item.path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if not pattern.search(line):
                    continue
                references.append(
                    SymbolReference(
                        path=item.path,
                        line=line_no,
                        text=line.strip()[:500],
                    )
                )
                if len(references) >= limit:
                    return tuple(references)
        return tuple(references)

    def _identity(self) -> RepositoryIdentity:
        rc, inside = _run_git(self.root, "rev-parse", "--is-inside-work-tree")
        if rc != 0 or inside.casefold() != "true":
            return RepositoryIdentity(
                root=str(self.root),
                is_git_repository=False,
            )
        _, head = _run_git(self.root, "rev-parse", "HEAD")
        branch_rc, branch = _run_git(self.root, "branch", "--show-current")
        if branch_rc != 0 or not branch:
            branch = None
        status_rc, status = _run_git(self.root, "status", "--porcelain=v1")
        return RepositoryIdentity(
            root=str(self.root),
            is_git_repository=True,
            head_sha=head or None,
            branch=branch,
            dirty=(status_rc == 0 and bool(status)),
        )

    def _inventory(self) -> tuple[tuple[RepositoryFile, ...], bool]:
        files: list[RepositoryFile] = []
        truncated = False
        for current, dirs, names in os.walk(self.root):
            dirs[:] = sorted(
                name
                for name in dirs
                if name not in _IGNORED_DIRS and not name.startswith(".git")
            )
            base = Path(current)
            for name in sorted(names):
                path = base / name
                try:
                    relative = _relative(self.root, path)
                    size = path.stat().st_size
                except OSError:
                    continue
                language = _LANGUAGE_BY_SUFFIX.get(path.suffix.casefold())
                files.append(
                    RepositoryFile(
                        path=relative,
                        size_bytes=size,
                        language=language,
                        is_manifest=name.casefold() in _MANIFEST_NAMES,
                        is_test=_is_test_path(relative),
                    )
                )
                if len(files) >= self.max_files:
                    truncated = True
                    return tuple(files), truncated
        return tuple(files), truncated

    def _instructions(
        self, files: tuple[RepositoryFile, ...]
    ) -> tuple[RepositoryInstruction, ...]:
        by_name: dict[str, list[RepositoryFile]] = {}
        for item in files:
            by_name.setdefault(Path(item.path).name.casefold(), []).append(item)
        found: list[RepositoryInstruction] = []
        for desired in _INSTRUCTION_NAMES:
            candidates = sorted(
                by_name.get(desired.casefold(), ()),
                key=lambda item: (len(Path(item.path).parts), item.path),
            )
            for item in candidates[:4]:
                try:
                    text = (self.root / item.path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                preview = text[: self.instruction_preview_chars]
                found.append(
                    RepositoryInstruction(
                        path=item.path,
                        kind=desired.casefold(),
                        size_chars=len(text),
                        preview=preview,
                        truncated=len(text) > len(preview),
                    )
                )
        return tuple(found[:12])

    @staticmethod
    def _roots(
        files: tuple[RepositoryFile, ...], *, test: bool
    ) -> tuple[str, ...]:
        roots: set[str] = set()
        for item in files:
            if test and not item.is_test:
                continue
            if not test and (item.is_test or item.language is None):
                continue
            parts = Path(item.path).parts
            if not parts:
                continue
            first = parts[0]
            if test:
                if first.casefold() in {"test", "tests", "spec", "specs"}:
                    roots.add(first)
                elif len(parts) > 1 and parts[1].casefold() in {"test", "tests"}:
                    roots.add("/".join(parts[:2]))
            elif _is_probable_source_root(first):
                roots.add(first)
        return tuple(sorted(roots))

    def _render_compact_map(
        self,
        *,
        identity: RepositoryIdentity,
        files: tuple[RepositoryFile, ...],
        languages: tuple[str, ...],
        manifests: tuple[str, ...],
        instructions: tuple[RepositoryInstruction, ...],
        symbols: tuple[SymbolRecord, ...],
        test_roots: tuple[str, ...],
        source_roots: tuple[str, ...],
    ) -> str:
        lines = ["REPOSITORY MAP"]
        if identity.is_git_repository:
            lines.append(
                f"git: head={identity.head_sha or 'unknown'} branch={identity.branch or '(detached)'} dirty={identity.dirty}"
            )
        else:
            lines.append("git: not a repository")
        if languages:
            lines.append("languages: " + ", ".join(languages))
        if source_roots:
            lines.append("source roots: " + ", ".join(source_roots))
        if test_roots:
            lines.append("test roots: " + ", ".join(test_roots))
        if manifests:
            lines.append("manifests: " + ", ".join(manifests[:20]))
        if instructions:
            lines.append(
                "instructions: " + ", ".join(item.path for item in instructions[:12])
            )

        lines.append("files:")
        important_files = sorted(
            files,
            key=lambda item: (
                0 if item.is_manifest else 1,
                0 if item.language else 1,
                len(Path(item.path).parts),
                item.path,
            ),
        )[:220]
        for item in important_files:
            suffix = f" [{item.language}]" if item.language else ""
            marker = " test" if item.is_test else ""
            lines.append(f"- {item.path}{suffix}{marker}")

        lines.append("symbols:")
        for item in symbols[:180]:
            lines.append(
                f"- {item.path}:{item.line} {item.kind} {item.qualified_name} :: {item.signature[:180]} ({item.precision.value})"
            )

        text = "\n".join(lines)
        if len(text) <= self.compact_map_chars:
            return text
        suffix = "\n... repository map truncated; use repository/symbol tools for details"
        return text[: max(0, self.compact_map_chars - len(suffix))] + suffix
