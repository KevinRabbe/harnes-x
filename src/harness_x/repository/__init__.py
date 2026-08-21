"""Bounded repository intelligence for coding tasks."""

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
from .providers import RepositorySemanticProvider
from .service import RepositoryIntelligenceService

__all__ = [
    "DependencyEdge",
    "RepositoryFile",
    "RepositoryIdentity",
    "RepositoryInstruction",
    "RepositoryIntelligenceService",
    "RepositorySemanticProvider",
    "RepositorySnapshot",
    "SymbolPrecision",
    "SymbolRecord",
    "SymbolReference",
    "SymbolSearchResult",
]
