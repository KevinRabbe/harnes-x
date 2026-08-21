"""M26 repository-aware coding registry with bounded browser tools."""

from __future__ import annotations

from pathlib import Path

from harness_x.browser import BrowserProvider
from harness_x.repository import RepositoryIntelligenceService, RepositorySemanticProvider

from .base import ToolRegistry
from .browser import browser_tool_definitions
from .coding_repository import build_repository_coding_registry


def build_browser_coding_registry(
    root: str | Path,
    provider: BrowserProvider,
    *,
    allowed_executables: frozenset[str] | None = None,
    repository_service: RepositoryIntelligenceService | None = None,
    semantic_provider: RepositorySemanticProvider | None = None,
) -> ToolRegistry:
    workspace_root = Path(root).resolve()
    base = build_repository_coding_registry(
        workspace_root,
        allowed_executables=allowed_executables,
        repository_service=repository_service,
        semantic_provider=semantic_provider,
    )
    registry = ToolRegistry()
    for spec in base.specs():
        registry.register(base.require(spec.name))
    for definition in browser_tool_definitions(provider):
        registry.register(definition)
    return registry
