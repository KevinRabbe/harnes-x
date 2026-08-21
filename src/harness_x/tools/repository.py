"""Structured repository-navigation and guarded-edit tools for coding tasks."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.repository import (
    RepositoryIdentity,
    RepositoryIntelligenceService,
    RepositorySemanticProvider,
    SymbolRecord,
    SymbolReference,
)

from .base import SideEffectLevel, ToolDefinition, ToolSpec


def _inside(root: Path, relative_path: str) -> Path:
    relative_path = relative_path.strip() or "."
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("repository tool refuses paths outside its root") from exc
    return candidate


def _relative(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    return "." if not relative.parts else relative.as_posix()


def _read_utf8(path: Path) -> tuple[bytes, str]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("repository coding tools only support UTF-8 text files") from exc
    return raw, text


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _run_git(root: Path, argv: tuple[str, ...]) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            ["git", *argv],
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15.0,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"
    return completed.returncode, completed.stdout or "", completed.stderr or ""


class RepositoryMapInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    refresh: bool = False


class RepositoryMapOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    fingerprint: str
    identity: RepositoryIdentity
    file_count: int = Field(ge=0)
    indexed_file_count: int = Field(ge=0)
    languages: tuple[str, ...] = ()
    manifests: tuple[str, ...] = ()
    source_roots: tuple[str, ...] = ()
    test_roots: tuple[str, ...] = ()
    instruction_paths: tuple[str, ...] = ()
    compact_map: str
    truncated: bool = False


class FileOutlineInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)


class FileOutlineOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    sha256: str
    line_count: int = Field(ge=0)
    symbols: tuple[SymbolRecord, ...] = ()
    source: str


class SymbolSearchInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1, max_length=240)
    limit: int = Field(default=20, ge=1, le=50)


class SymbolSearchOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    matches: tuple[SymbolRecord, ...] = ()
    truncated: bool = False
    source: str


class SymbolDefinitionInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=240)
    path: str | None = None
    limit: int = Field(default=6, ge=1, le=20)
    context_lines: int = Field(default=4, ge=0, le=20)


class SymbolDefinitionMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: SymbolRecord
    file_sha256: str
    excerpt_start_line: int = Field(ge=1)
    excerpt_end_line: int = Field(ge=1)
    excerpt: str


class SymbolDefinitionOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    matches: tuple[SymbolDefinitionMatch, ...] = ()
    truncated: bool = False
    source: str


class SymbolReferencesInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=240)
    limit: int = Field(default=50, ge=1, le=120)


class SymbolReferencesOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    references: tuple[SymbolReference, ...] = ()
    truncated: bool = False
    source: str


class GitStatusInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    refresh_repository_identity: bool = True


class GitStatusEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    index_status: str
    worktree_status: str


class GitStatusOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity: RepositoryIdentity
    entries: tuple[GitStatusEntry, ...] = ()
    truncated: bool = False


class GitDiffInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str | None = None
    staged: bool = False
    context_lines: int = Field(default=3, ge=0, le=20)
    max_chars: int = Field(default=16000, ge=1000, le=50000)


class GitDiffOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str | None = None
    staged: bool = False
    diff: str
    truncated: bool = False


class WorkspacePatchRangeInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    expected_sha256: str = Field(min_length=64, max_length=64)
    replacement: str = Field(max_length=500000)
    preserve_trailing_newline: bool = True

    @field_validator("expected_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("expected_sha256 must be a lowercase/uppercase SHA-256 hex digest")
        return normalized


class WorkspacePatchRangeOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    start_line: int
    end_line: int
    sha256_before: str
    sha256_after: str
    bytes_written: int = Field(ge=0)


def _semantic_search(
    service: RepositoryIntelligenceService,
    provider: RepositorySemanticProvider | None,
    query: str,
    *,
    limit: int,
) -> tuple[tuple[SymbolRecord, ...], bool, str]:
    if provider is not None:
        answer = provider.symbol_search(query, limit=limit)
        if answer is not None:
            return answer.matches, answer.truncated, provider.name
    answer = service.symbol_search(query, limit=limit)
    return answer.matches, answer.truncated, "fallback_index"


def repository_map_definition(
    service: RepositoryIntelligenceService,
) -> ToolDefinition:
    def handler(request: RepositoryMapInput) -> RepositoryMapOutput:
        snapshot = service.snapshot(refresh=request.refresh)
        return RepositoryMapOutput(
            fingerprint=snapshot.fingerprint,
            identity=snapshot.identity,
            file_count=snapshot.file_count,
            indexed_file_count=snapshot.indexed_file_count,
            languages=snapshot.languages,
            manifests=snapshot.manifests,
            source_roots=snapshot.source_roots,
            test_roots=snapshot.test_roots,
            instruction_paths=tuple(item.path for item in snapshot.instructions),
            compact_map=snapshot.compact_map,
            truncated=snapshot.truncated,
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="repository_map",
            version="repository-map-v1",
            input_schema=RepositoryMapInput.model_json_schema(),
            output_schema=RepositoryMapOutput.model_json_schema(),
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=15.0,
            idempotent=True,
        ),
        input_model=RepositoryMapInput,
        output_model=RepositoryMapOutput,
        handler=handler,
    )


def file_outline_definition(
    root: Path,
    service: RepositoryIntelligenceService,
    provider: RepositorySemanticProvider | None,
) -> ToolDefinition:
    def handler(request: FileOutlineInput) -> FileOutlineOutput:
        target = _inside(root, request.path)
        if not target.is_file():
            raise FileNotFoundError(request.path)
        raw, text = _read_utf8(target)
        symbols: tuple[SymbolRecord, ...] | None = None
        source = "fallback_index"
        if provider is not None:
            symbols = provider.file_outline(_relative(root, target))
            if symbols is not None:
                source = provider.name
        if symbols is None:
            symbols = service.file_outline(_relative(root, target))
        return FileOutlineOutput(
            path=_relative(root, target),
            sha256=_sha256(raw),
            line_count=len(text.splitlines()),
            symbols=symbols,
            source=source,
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="file_outline",
            version="file-outline-v1",
            input_schema=FileOutlineInput.model_json_schema(),
            output_schema=FileOutlineOutput.model_json_schema(),
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=10.0,
            idempotent=True,
        ),
        input_model=FileOutlineInput,
        output_model=FileOutlineOutput,
        handler=handler,
    )


def symbol_search_definition(
    service: RepositoryIntelligenceService,
    provider: RepositorySemanticProvider | None,
) -> ToolDefinition:
    def handler(request: SymbolSearchInput) -> SymbolSearchOutput:
        matches, truncated, source = _semantic_search(
            service, provider, request.query, limit=request.limit
        )
        return SymbolSearchOutput(
            query=request.query,
            matches=matches,
            truncated=truncated,
            source=source,
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="symbol_search",
            version="symbol-search-v1",
            input_schema=SymbolSearchInput.model_json_schema(),
            output_schema=SymbolSearchOutput.model_json_schema(),
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=10.0,
            idempotent=True,
        ),
        input_model=SymbolSearchInput,
        output_model=SymbolSearchOutput,
        handler=handler,
    )


def symbol_definition_definition(
    root: Path,
    service: RepositoryIntelligenceService,
    provider: RepositorySemanticProvider | None,
) -> ToolDefinition:
    def handler(request: SymbolDefinitionInput) -> SymbolDefinitionOutput:
        matches, truncated, source = _semantic_search(
            service, provider, request.name, limit=max(request.limit * 3, request.limit)
        )
        if request.path is not None:
            wanted = _relative(root, _inside(root, request.path))
            matches = tuple(item for item in matches if item.path == wanted)
        selected = matches[: request.limit]
        rows: list[SymbolDefinitionMatch] = []
        for symbol in selected:
            target = _inside(root, symbol.path)
            if not target.is_file():
                continue
            raw, text = _read_utf8(target)
            lines = text.splitlines()
            symbol_end = symbol.end_line or symbol.line
            start = max(1, symbol.line - request.context_lines)
            end = min(len(lines), symbol_end + request.context_lines)
            excerpt = "\n".join(
                f"{line_no}: {lines[line_no - 1]}" for line_no in range(start, end + 1)
            )
            rows.append(
                SymbolDefinitionMatch(
                    symbol=symbol,
                    file_sha256=_sha256(raw),
                    excerpt_start_line=start,
                    excerpt_end_line=end,
                    excerpt=excerpt[:12000],
                )
            )
        return SymbolDefinitionOutput(
            name=request.name,
            matches=tuple(rows),
            truncated=truncated or len(matches) > request.limit,
            source=source,
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="symbol_definition",
            version="symbol-definition-v1",
            input_schema=SymbolDefinitionInput.model_json_schema(),
            output_schema=SymbolDefinitionOutput.model_json_schema(),
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=10.0,
            idempotent=True,
        ),
        input_model=SymbolDefinitionInput,
        output_model=SymbolDefinitionOutput,
        handler=handler,
    )


def symbol_references_definition(
    service: RepositoryIntelligenceService,
    provider: RepositorySemanticProvider | None,
) -> ToolDefinition:
    def handler(request: SymbolReferencesInput) -> SymbolReferencesOutput:
        source = "fallback_index"
        references: tuple[SymbolReference, ...] | None = None
        if provider is not None:
            references = provider.symbol_references(request.name, limit=request.limit)
            if references is not None:
                source = provider.name
        if references is None:
            references = service.symbol_references(request.name, limit=request.limit + 1)
        return SymbolReferencesOutput(
            name=request.name,
            references=references[: request.limit],
            truncated=len(references) > request.limit,
            source=source,
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="symbol_references",
            version="symbol-references-v1",
            input_schema=SymbolReferencesInput.model_json_schema(),
            output_schema=SymbolReferencesOutput.model_json_schema(),
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="medium",
            timeout_seconds=15.0,
            idempotent=True,
        ),
        input_model=SymbolReferencesInput,
        output_model=SymbolReferencesOutput,
        handler=handler,
    )


def git_status_definition(
    root: Path,
    service: RepositoryIntelligenceService,
) -> ToolDefinition:
    def handler(request: GitStatusInput) -> GitStatusOutput:
        identity = service.snapshot(refresh=request.refresh_repository_identity).identity
        if not identity.is_git_repository:
            return GitStatusOutput(identity=identity)
        rc, stdout, stderr = _run_git(root, ("status", "--porcelain=v1"))
        if rc != 0:
            raise RuntimeError(stderr.strip() or "git status failed")
        entries: list[GitStatusEntry] = []
        truncated = False
        for line in stdout.splitlines():
            if len(line) < 3:
                continue
            if len(entries) >= 200:
                truncated = True
                break
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            entries.append(
                GitStatusEntry(
                    path=path,
                    index_status=line[0],
                    worktree_status=line[1],
                )
            )
        return GitStatusOutput(
            identity=identity,
            entries=tuple(entries),
            truncated=truncated,
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="git_status",
            version="git-status-v1",
            input_schema=GitStatusInput.model_json_schema(),
            output_schema=GitStatusOutput.model_json_schema(),
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=10.0,
            idempotent=True,
        ),
        input_model=GitStatusInput,
        output_model=GitStatusOutput,
        handler=handler,
    )


def git_diff_definition(root: Path) -> ToolDefinition:
    def handler(request: GitDiffInput) -> GitDiffOutput:
        argv: list[str] = ["diff", "--no-ext-diff", f"--unified={request.context_lines}"]
        if request.staged:
            argv.append("--cached")
        normalized_path: str | None = None
        if request.path is not None:
            target = _inside(root, request.path)
            normalized_path = _relative(root, target)
            argv.extend(("--", normalized_path))
        rc, stdout, stderr = _run_git(root, tuple(argv))
        if rc != 0:
            raise RuntimeError(stderr.strip() or "git diff failed")
        truncated = len(stdout) > request.max_chars
        text = stdout[: request.max_chars]
        if truncated:
            text += "\n... git diff truncated"
        return GitDiffOutput(
            path=normalized_path,
            staged=request.staged,
            diff=text,
            truncated=truncated,
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="git_diff",
            version="git-diff-v1",
            input_schema=GitDiffInput.model_json_schema(),
            output_schema=GitDiffOutput.model_json_schema(),
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=15.0,
            idempotent=True,
        ),
        input_model=GitDiffInput,
        output_model=GitDiffOutput,
        handler=handler,
    )


def workspace_patch_range_definition(root: Path) -> ToolDefinition:
    def handler(request: WorkspacePatchRangeInput) -> WorkspacePatchRangeOutput:
        target = _inside(root, request.path)
        if not target.is_file():
            raise FileNotFoundError(request.path)
        raw, text = _read_utf8(target)
        before = _sha256(raw)
        if before != request.expected_sha256:
            raise ValueError(
                "workspace_patch_range refused stale file: expected_sha256 does not match"
            )
        lines = text.splitlines(keepends=True)
        if request.start_line > request.end_line:
            raise ValueError("start_line cannot exceed end_line")
        if request.end_line > len(lines):
            raise ValueError(
                f"end_line {request.end_line} exceeds file line count {len(lines)}"
            )
        replacement = request.replacement
        selected_last = lines[request.end_line - 1]
        if (
            request.preserve_trailing_newline
            and replacement
            and request.end_line < len(lines)
            and not replacement.endswith(("\n", "\r"))
        ):
            if selected_last.endswith("\r\n"):
                replacement += "\r\n"
            elif selected_last.endswith("\n"):
                replacement += "\n"
        updated = "".join(
            [*lines[: request.start_line - 1], replacement, *lines[request.end_line :]]
        )
        payload = updated.encode("utf-8")
        target.write_bytes(payload)
        return WorkspacePatchRangeOutput(
            path=_relative(root, target),
            start_line=request.start_line,
            end_line=request.end_line,
            sha256_before=before,
            sha256_after=_sha256(payload),
            bytes_written=len(payload),
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="workspace_patch_range",
            version="workspace-patch-range-v1",
            input_schema=WorkspacePatchRangeInput.model_json_schema(),
            output_schema=WorkspacePatchRangeOutput.model_json_schema(),
            permissions=("workspace.write",),
            side_effect_level=SideEffectLevel.PERSISTENT,
            cost_class="medium",
            timeout_seconds=10.0,
            idempotent=False,
        ),
        input_model=WorkspacePatchRangeInput,
        output_model=WorkspacePatchRangeOutput,
        handler=handler,
    )


def repository_tool_definitions(
    root: str | Path,
    *,
    service: RepositoryIntelligenceService | None = None,
    semantic_provider: RepositorySemanticProvider | None = None,
) -> tuple[ToolDefinition, ...]:
    workspace_root = Path(root).resolve()
    if not workspace_root.is_dir():
        raise ValueError("repository tool root must be an existing directory")
    repository_service = service or RepositoryIntelligenceService(workspace_root)
    return (
        repository_map_definition(repository_service),
        file_outline_definition(workspace_root, repository_service, semantic_provider),
        symbol_search_definition(repository_service, semantic_provider),
        symbol_definition_definition(workspace_root, repository_service, semantic_provider),
        symbol_references_definition(repository_service, semantic_provider),
        git_status_definition(workspace_root, repository_service),
        git_diff_definition(workspace_root),
        workspace_patch_range_definition(workspace_root),
    )
