"""Cheap structured Git state reads for repository-aware coding."""

from __future__ import annotations

import subprocess
from pathlib import Path

from harness_x.repository import RepositoryIdentity

from .base import SideEffectLevel, ToolDefinition, ToolSpec
from .repository import GitStatusEntry, GitStatusInput, GitStatusOutput


def _run_git(root: Path, *args: str) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10.0,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _git_identity_and_status(root: Path) -> tuple[RepositoryIdentity, str]:
    inside_rc, inside, _ = _run_git(root, "rev-parse", "--is-inside-work-tree")
    if inside_rc != 0 or inside.strip().casefold() != "true":
        return RepositoryIdentity(root=str(root), is_git_repository=False), ""

    _, head, _ = _run_git(root, "rev-parse", "HEAD")
    branch_rc, branch, _ = _run_git(root, "branch", "--show-current")
    status_rc, status, stderr = _run_git(root, "status", "--porcelain=v1")
    if status_rc != 0:
        raise RuntimeError(stderr.strip() or "git status failed")
    branch_name = branch.strip() if branch_rc == 0 and branch.strip() else None
    identity = RepositoryIdentity(
        root=str(root),
        is_git_repository=True,
        head_sha=head.strip() or None,
        branch=branch_name,
        dirty=bool(status),
    )
    return identity, status


def git_status_v2_definition(root: str | Path) -> ToolDefinition:
    """Structured Git status without rebuilding the repository symbol/inventory index."""

    workspace_root = Path(root).resolve()

    def handler(request: GitStatusInput) -> GitStatusOutput:
        # The request retains the M23 v1 schema for compatibility. Identity/status are
        # intentionally read directly from Git every call; no repository rescan occurs.
        _ = request.refresh_repository_identity
        identity, status = _git_identity_and_status(workspace_root)
        if not identity.is_git_repository:
            return GitStatusOutput(identity=identity)

        entries: list[GitStatusEntry] = []
        truncated = False
        for line in status.splitlines():
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
            version="git-status-v2",
            input_schema=GitStatusInput.model_json_schema(),
            output_schema=GitStatusOutput.model_json_schema(),
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=12.0,
            idempotent=True,
        ),
        input_model=GitStatusInput,
        output_model=GitStatusOutput,
        handler=handler,
    )
