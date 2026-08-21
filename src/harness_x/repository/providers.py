"""Replaceable semantic-navigation seam for compiler/LSP-backed providers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import SymbolRecord, SymbolReference, SymbolSearchResult


@runtime_checkable
class RepositorySemanticProvider(Protocol):
    """Optional higher-precision provider layered over deterministic fallback indexing.

    Implementations may talk to an LSP server, compiler index, or another semantic
    engine. Returning ``None`` means the provider cannot answer that request and the
    repository service/tool layer should fall back to its bounded local index.

    Provider-produced symbols/references must declare their own precision (normally
    ``SymbolPrecision.LSP``); Harness X never silently upgrades heuristic evidence.
    """

    @property
    def name(self) -> str:
        ...

    def symbol_search(
        self, query: str, *, limit: int
    ) -> SymbolSearchResult | None:
        ...

    def file_outline(self, path: str) -> tuple[SymbolRecord, ...] | None:
        ...

    def symbol_references(
        self, name: str, *, limit: int
    ) -> tuple[SymbolReference, ...] | None:
        ...
