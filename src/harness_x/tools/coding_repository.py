"""Composite coding registry with M23 repository-intelligence tools."""

from __future__ import annotations

from pathlib import Path

from harness_x.repository import RepositoryIntelligenceService, RepositorySemanticProvider

from .base import ToolRegistry
from .coding import build_coding_registry
from .patch_v2 import workspace_patch_v2_definition
from .repository import repository_tool_definitions


def build_repository_coding_registry(
    root: str | Path,
    *,
    allowed_executables: frozenset[str] | None = None,
    repository_service: RepositoryIntelligenceService | None = None,
    semantic_provider: RepositorySemanticProvider | None = None,
) -> ToolRegistry:
    """Return M21 coding tools upgraded with M23 repository intelligence.

    The existing ``workspace_patch`` name is intentionally preserved because M22 owns
    mutation/verification freshness by tool identity. M23 replaces only its definition
    with v2, which supports both exact-text mode and hash-guarded range mode.
    """

    workspace_root = Path(root).resolve()
    if allowed_executables is None:
        base_registry = build_coding_registry(workspace_root)
    else:
        base_registry = build_coding_registry(
            workspace_root,
            allowed_executables=allowed_executables,
        )

    registry = ToolRegistry()
    for spec in base_registry.specs():
        if spec.name == "workspace_patch":
            continue
        registry.register(base_registry.require(spec.name))
    registry.register(workspace_patch_v2_definition(workspace_root))

    service = repository_service or RepositoryIntelligenceService(workspace_root)
    for definition in repository_tool_definitions(
        workspace_root,
        service=service,
        semantic_provider=semantic_provider,
    ):
        # Range mode is exposed through workspace_patch-v2 so M22's mutation authority
        # remains correct without modifying the qualified M22 runtime.
        if definition.spec.name == "workspace_patch_range":
            continue
        registry.register(definition)
    return registry
