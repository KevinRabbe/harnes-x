"""Typed, bounded repository-intelligence contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SymbolPrecision(StrEnum):
    EXACT_AST = "exact_ast"
    HEURISTIC = "heuristic"
    LSP = "lsp"


class RepositoryFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    size_bytes: int = Field(ge=0)
    language: str | None = None
    is_manifest: bool = False
    is_test: bool = False


class RepositoryIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "repository-identity-v1"
    root: str
    is_git_repository: bool
    head_sha: str | None = None
    branch: str | None = None
    dirty: bool = False


class RepositoryInstruction(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    kind: str
    size_chars: int = Field(ge=0)
    preview: str
    truncated: bool = False


class SymbolRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    language: str
    name: str
    qualified_name: str
    kind: str
    line: int = Field(ge=1)
    end_line: int | None = Field(default=None, ge=1)
    signature: str = ""
    precision: SymbolPrecision


class DependencyEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_path: str
    target: str
    kind: str = "import"
    precision: SymbolPrecision


class RepositorySnapshot(BaseModel):
    """Bounded startup projection; complete raw repository content is never embedded."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "repository-snapshot-v1"
    fingerprint: str = Field(min_length=64, max_length=64)
    identity: RepositoryIdentity
    file_count: int = Field(ge=0)
    indexed_file_count: int = Field(ge=0)
    languages: tuple[str, ...] = ()
    manifests: tuple[str, ...] = ()
    test_roots: tuple[str, ...] = ()
    source_roots: tuple[str, ...] = ()
    instructions: tuple[RepositoryInstruction, ...] = ()
    symbols: tuple[SymbolRecord, ...] = ()
    dependencies: tuple[DependencyEdge, ...] = ()
    compact_map: str = ""
    truncated: bool = False


class SymbolSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    matches: tuple[SymbolRecord, ...]
    truncated: bool = False


class SymbolReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    line: int = Field(ge=1)
    text: str
    precision: SymbolPrecision = SymbolPrecision.HEURISTIC
