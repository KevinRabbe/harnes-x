"""Public resource guard for the M76 Improvement Observatory.

The detailed projection reader already bounds record depth/count/bytes. This guard additionally
bounds *all* directory entries considered before the authenticated HTTP route enters that reader,
so a project-local tree full of unrelated files cannot turn one observatory GET into an unbounded
filesystem walk.
"""

from __future__ import annotations

import os
from pathlib import Path

from harness_x import __version__

from .improvement_observatory import (
    ImprovementObservatoryProjection,
    ObservatorySource,
    ObservatorySourceStatus,
    build_improvement_observatory,
)

_MAX_PUBLIC_TREE_DEPTH = 6
_MAX_PUBLIC_TREE_ENTRIES = 512


def _public_tree_within_budget(workspace_root: str | Path) -> tuple[bool, str | None]:
    workspace = Path(workspace_root).resolve()
    root = workspace / ".harness-x"
    if not root.is_dir() or root.is_symlink():
        return True, None

    pending: list[tuple[Path, int]] = [(root, 0)]
    entries = 0
    try:
        while pending:
            directory, depth = pending.pop()
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    entries += 1
                    if entries > _MAX_PUBLIC_TREE_ENTRIES:
                        return False, "tree-entry limit reached"
                    if depth >= _MAX_PUBLIC_TREE_DEPTH or entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append((Path(entry.path), depth + 1))
    except OSError:
        return False, "tree enumeration failed within observatory read boundary"
    return True, None


def build_public_improvement_observatory(
    *,
    project_id: str,
    workspace_root: str | Path,
) -> ImprovementObservatoryProjection:
    """Build the public projection only after a bounded non-following tree preflight."""

    within_budget, detail = _public_tree_within_budget(workspace_root)
    if within_budget:
        return build_improvement_observatory(
            project_id=project_id,
            workspace_root=workspace_root,
        )

    workspace = Path(workspace_root).resolve()
    root = workspace / ".harness-x"
    return ImprovementObservatoryProjection(
        project_id=project_id,
        software_version=__version__,
        observatory_root_present=root.is_dir() and not root.is_symlink(),
        scan_truncated=True,
        sources=(
            ObservatorySource(
                relative_path=".harness-x",
                record_kind="observatory_scan",
                status=ObservatorySourceStatus.SCAN_TRUNCATED,
                detail=detail or "public observatory traversal budget exceeded",
            ),
        ),
    )


__all__ = ["build_public_improvement_observatory"]
