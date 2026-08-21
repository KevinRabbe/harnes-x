"""Composite coding registry with M23 repository-intelligence tools."""

from __future__ import annotations

from pathlib import Path

from harness_x.repository import RepositoryIntelligenceService, RepositorySemanticProvider

from .base import ToolRegistry
from .coding import build_coding_registry
from .repository import repository_tool_definitions


def build_repository_coding_registry(
    root: str | Path,
    *,
    allowed_executables: frozenset[str] | None = None,
    repository_service: RepositoryIntelligenceService | None = None,
    semantic_provider: RepositorySemanticProvider | None = None,
) -> ToolRegistry:
    """Return the M21 coding registry extended with M23 structured repository tools."""

    workspace_root = Path(root).resolve()
    if allowed_executables is None:
        registry = build_coding_registry(workspace_root)
    else:
        registry = build_coding_registry(
            workspace_root,
            allowed_executables=allowed_executables,
        )
    service = repository_service or RepositoryIntelligenceService(workspace_root)
    for definition in repository_tool_definitions(
        workspace_root,
        service=service,
        semantic_provider=semantic_provider,
    ):
        registry.register(definition)
    return registry
