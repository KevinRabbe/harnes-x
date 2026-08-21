"""Task workspace isolation for coding runs.

M24 keeps model-selected edits away from the operator's source checkout. Git sources
are cloned locally at an exact HEAD and dirty tracked/untracked state is overlaid into
the clone; non-Git sources are copied. Task deltas are exported as hash-addressed
artifacts before optional cleanup.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field


class IsolationStrategy(StrEnum):
    GIT_CLONE = "git_clone"
    SNAPSHOT_COPY = "snapshot_copy"


class IsolationRetention(StrEnum):
    ALWAYS = "always"
    ON_FAILURE = "on_failure"
    NEVER = "never"


class SourceWorkspaceIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "source-workspace-identity-v1"
    source_root: str
    is_git_repository: bool
    head_sha: str | None = None
    branch: str | None = None
    dirty: bool = False
    fingerprint: str = Field(min_length=64, max_length=64)
    support_paths: tuple[str, ...] = ()


class WorkspaceFileState(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)


class TaskWorkspaceChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    change_type: str
    baseline_sha256: str | None = None
    final_sha256: str | None = None
    final_size_bytes: int | None = Field(default=None, ge=0)


class IsolationStartManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "task-workspace-start-v1"
    session_id: str
    strategy: IsolationStrategy
    source: SourceWorkspaceIdentity
    workspace_root: str
    retention: IsolationRetention
    baseline_file_count: int = Field(ge=0)


class IsolationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "task-workspace-result-v1"
    session_id: str
    strategy: IsolationStrategy
    source: SourceWorkspaceIdentity
    workspace_root: str
    retained: bool
    succeeded: bool
    changes: tuple[TaskWorkspaceChange, ...] = ()
    change_manifest_path: str
    changed_files_root: str
    initial_git_patch_path: str | None = None
    final_git_patch_path: str | None = None

    @property
    def changed_file_count(self) -> int:
        return len(self.changes)


class PreparedTaskWorkspace(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    session_id: str
    strategy: IsolationStrategy
    source: SourceWorkspaceIdentity
    session_root: Path
    workspace_root: Path
    baseline: dict[str, WorkspaceFileState]
    initial_git_patch: bytes = b""


_CHANGE_IGNORED_NAMES = frozenset(
    {
        ".git",
        ".harness-x",
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
    }
)
_COPY_IGNORED_NAMES = frozenset({".git", ".harness-x"})


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _run_bytes(
    argv: Iterable[str],
    *,
    cwd: Path,
    input_data: bytes | None = None,
    timeout: float = 120.0,
) -> subprocess.CompletedProcess[bytes]:
    kwargs: dict[str, object] = {
        "cwd": cwd,
        "capture_output": True,
        "shell": False,
        "timeout": timeout,
        "check": False,
    }
    if input_data is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = input_data
    try:
        completed = subprocess.run(list(argv), **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"isolation command failed to execute: {exc}") from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"isolation command failed ({completed.returncode}): {' '.join(argv)}: {stderr}"
        )
    return completed


def _git_bytes(root: Path, *args: str) -> bytes:
    return _run_bytes(("git", *args), cwd=root).stdout or b""


def _git_text(root: Path, *args: str) -> str:
    return _git_bytes(root, *args).decode("utf-8", errors="replace").strip()


def _inside(root: Path, relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute():
        raise ValueError("support paths must be relative to the source workspace")
    target = (root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("support path escapes the source workspace") from exc
    return target


def _copy_path(source_root: Path, workspace_root: Path, relative: str) -> None:
    source = _inside(source_root, relative)
    if not source.exists():
        raise FileNotFoundError(f"support path does not exist: {relative}")
    parts = Path(relative).parts
    if any(part in _COPY_IGNORED_NAMES for part in parts):
        raise ValueError("support paths cannot include Harness X or Git metadata")
    destination = workspace_root / Path(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=False)
    else:
        shutil.copy2(source, destination, follow_symlinks=True)


def _snapshot_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in _COPY_IGNORED_NAMES}


def _iter_content_files(root: Path):
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in _CHANGE_IGNORED_NAMES)
        base = Path(current)
        for name in sorted(names):
            path = base / name
            relative = path.relative_to(root)
            if any(part in _CHANGE_IGNORED_NAMES for part in relative.parts):
                continue
            if path.is_symlink():
                payload = os.readlink(path).encode("utf-8", errors="surrogateescape")
                yield relative.as_posix(), WorkspaceFileState(
                    sha256=_sha256_bytes(b"symlink\0" + payload),
                    size_bytes=len(payload),
                )
                continue
            if not path.is_file():
                continue
            try:
                payload = path.read_bytes()
            except OSError:
                continue
            yield relative.as_posix(), WorkspaceFileState(
                sha256=_sha256_bytes(payload),
                size_bytes=len(payload),
            )


def _content_manifest(root: Path) -> dict[str, WorkspaceFileState]:
    return dict(_iter_content_files(root))


def _manifest_fingerprint(manifest: dict[str, WorkspaceFileState]) -> str:
    material = [
        (path, state.sha256, state.size_bytes)
        for path, state in sorted(manifest.items())
    ]
    return _sha256_bytes(_canonical(material))


def _git_untracked_paths(root: Path) -> tuple[str, ...]:
    raw = _git_bytes(root, "ls-files", "--others", "--exclude-standard", "-z")
    paths = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        paths.append(item.decode("utf-8", errors="replace"))
    return tuple(paths)


def _untracked_fingerprint(root: Path, paths: tuple[str, ...]) -> str:
    material: list[tuple[str, str, int]] = []
    for relative in paths:
        path = _inside(root, relative)
        if path.is_symlink():
            payload = os.readlink(path).encode("utf-8", errors="surrogateescape")
        elif path.is_file():
            payload = path.read_bytes()
        else:
            continue
        material.append((relative, _sha256_bytes(payload), len(payload)))
    return _sha256_bytes(_canonical(material))


def _support_fingerprint(root: Path, support_paths: tuple[str, ...]) -> str:
    material: list[tuple[str, str, int]] = []
    for relative in support_paths:
        target = _inside(root, relative)
        if target.is_dir():
            for path, state in _iter_content_files(target):
                material.append((f"{relative.rstrip('/')}/{path}", state.sha256, state.size_bytes))
        elif target.is_symlink():
            payload = os.readlink(target).encode("utf-8", errors="surrogateescape")
            material.append((relative, _sha256_bytes(payload), len(payload)))
        elif target.is_file():
            payload = target.read_bytes()
            material.append((relative, _sha256_bytes(payload), len(payload)))
    return _sha256_bytes(_canonical(sorted(material)))


def _git_source_identity(root: Path, support_paths: tuple[str, ...]) -> tuple[SourceWorkspaceIdentity, bytes, tuple[str, ...]]:
    inside = _git_text(root, "rev-parse", "--is-inside-work-tree")
    if inside.casefold() != "true":
        raise ValueError("source is not a Git worktree")
    head = _git_text(root, "rev-parse", "HEAD")
    branch = _git_text(root, "branch", "--show-current") or None
    status = _git_bytes(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    patch = _git_bytes(root, "diff", "--binary", "HEAD", "--")
    untracked = _git_untracked_paths(root)
    material = {
        "kind": "git-source-v1",
        "head": head,
        "status_sha256": _sha256_bytes(status),
        "tracked_diff_sha256": _sha256_bytes(patch),
        "untracked_sha256": _untracked_fingerprint(root, untracked),
        "support_sha256": _support_fingerprint(root, support_paths),
    }
    identity = SourceWorkspaceIdentity(
        source_root=str(root),
        is_git_repository=True,
        head_sha=head,
        branch=branch,
        dirty=bool(status),
        fingerprint=_sha256_bytes(_canonical(material)),
        support_paths=support_paths,
    )
    return identity, patch, untracked


def _non_git_identity(root: Path) -> SourceWorkspaceIdentity:
    manifest = _content_manifest(root)
    return SourceWorkspaceIdentity(
        source_root=str(root),
        is_git_repository=False,
        fingerprint=_manifest_fingerprint(manifest),
    )


def _is_git_repository(root: Path) -> bool:
    try:
        return _git_text(root, "rev-parse", "--is-inside-work-tree").casefold() == "true"
    except RuntimeError:
        return False


def _changes(
    baseline: dict[str, WorkspaceFileState],
    final: dict[str, WorkspaceFileState],
) -> tuple[TaskWorkspaceChange, ...]:
    result: list[TaskWorkspaceChange] = []
    for path in sorted(set(baseline) | set(final)):
        before = baseline.get(path)
        after = final.get(path)
        if before is not None and after is not None and before.sha256 == after.sha256:
            continue
        if before is None and after is not None:
            kind = "added"
        elif before is not None and after is None:
            kind = "deleted"
        else:
            kind = "modified"
        result.append(
            TaskWorkspaceChange(
                path=path,
                change_type=kind,
                baseline_sha256=(before.sha256 if before else None),
                final_sha256=(after.sha256 if after else None),
                final_size_bytes=(after.size_bytes if after else None),
            )
        )
    return tuple(result)


class TaskWorkspaceIsolationManager:
    """Prepare, record, export, and optionally clean one isolated coding workspace."""

    def __init__(
        self,
        source_root: str | Path,
        artifact_root: str | Path,
        *,
        isolation_root: str | Path | None = None,
        retention: IsolationRetention = IsolationRetention.ALWAYS,
        support_paths: Iterable[str] = (),
    ) -> None:
        self.source_root = Path(source_root).resolve()
        if not self.source_root.is_dir():
            raise ValueError("source workspace must be an existing directory")
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        base = (
            Path(isolation_root).resolve()
            if isolation_root is not None
            else Path(tempfile.gettempdir()).resolve() / "harness-x" / "task-workspaces"
        )
        try:
            base.relative_to(self.source_root)
        except ValueError:
            pass
        else:
            raise ValueError("isolation root must not be inside the source workspace")
        self.isolation_root = base
        self.isolation_root.mkdir(parents=True, exist_ok=True)
        self.retention = IsolationRetention(retention)
        normalized = tuple(
            dict.fromkeys(
                item.strip().replace("\\", "/")
                for item in support_paths
                if item.strip()
            )
        )
        for item in normalized:
            _inside(self.source_root, item)
        self.support_paths = normalized
        self._prepared: PreparedTaskWorkspace | None = None

    def prepare(self) -> PreparedTaskWorkspace:
        if self._prepared is not None:
            raise RuntimeError("task workspace is already prepared")
        session_id = f"taskws_{uuid.uuid4().hex[:20]}"
        session_root = self.isolation_root / session_id
        workspace_root = session_root / "workspace"
        session_root.mkdir(parents=True, exist_ok=False)

        try:
            if _is_git_repository(self.source_root):
                prepared = self._prepare_git(session_id, session_root, workspace_root)
            else:
                prepared = self._prepare_snapshot(session_id, session_root, workspace_root)
        except Exception:
            shutil.rmtree(session_root, ignore_errors=True)
            raise

        start = IsolationStartManifest(
            session_id=prepared.session_id,
            strategy=prepared.strategy,
            source=prepared.source,
            workspace_root=str(prepared.workspace_root),
            retention=self.retention,
            baseline_file_count=len(prepared.baseline),
        )
        self._write_json(
            self.artifact_root / "isolation-start.json",
            start.model_dump(mode="json"),
        )
        self._prepared = prepared
        return prepared

    def _prepare_git(
        self,
        session_id: str,
        session_root: Path,
        workspace_root: Path,
    ) -> PreparedTaskWorkspace:
        before, initial_patch, untracked = _git_source_identity(
            self.source_root, self.support_paths
        )
        assert before.head_sha is not None
        _run_bytes(
            (
                "git",
                "clone",
                "--quiet",
                "--shared",
                "--no-checkout",
                str(self.source_root),
                str(workspace_root),
            ),
            cwd=session_root,
        )
        _run_bytes(
            ("git", "checkout", "--quiet", "--detach", before.head_sha),
            cwd=workspace_root,
        )
        if initial_patch:
            _run_bytes(
                ("git", "apply", "--binary", "--whitespace=nowarn"),
                cwd=workspace_root,
                input_data=initial_patch,
            )
        for relative in untracked:
            source = _inside(self.source_root, relative)
            destination = workspace_root / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(
                    source,
                    destination,
                    dirs_exist_ok=True,
                    symlinks=False,
                )
            else:
                shutil.copy2(source, destination, follow_symlinks=True)
        for relative in self.support_paths:
            _copy_path(self.source_root, workspace_root, relative)

        after, _, _ = _git_source_identity(self.source_root, self.support_paths)
        if after.fingerprint != before.fingerprint:
            raise RuntimeError(
                "source workspace changed while isolation snapshot was being prepared"
            )
        baseline = _content_manifest(workspace_root)
        return PreparedTaskWorkspace(
            session_id=session_id,
            strategy=IsolationStrategy.GIT_CLONE,
            source=before,
            session_root=session_root,
            workspace_root=workspace_root,
            baseline=baseline,
            initial_git_patch=initial_patch,
        )

    def _prepare_snapshot(
        self,
        session_id: str,
        session_root: Path,
        workspace_root: Path,
    ) -> PreparedTaskWorkspace:
        before = _non_git_identity(self.source_root)
        shutil.copytree(
            self.source_root,
            workspace_root,
            symlinks=False,
            ignore=_snapshot_ignore,
        )
        source_after = _non_git_identity(self.source_root)
        if source_after.fingerprint != before.fingerprint:
            raise RuntimeError(
                "source workspace changed while isolation snapshot was being prepared"
            )
        baseline = _content_manifest(workspace_root)
        if _manifest_fingerprint(baseline) != before.fingerprint:
            raise RuntimeError(
                "isolated filesystem snapshot does not match the source fingerprint"
            )
        return PreparedTaskWorkspace(
            session_id=session_id,
            strategy=IsolationStrategy.SNAPSHOT_COPY,
            source=before,
            session_root=session_root,
            workspace_root=workspace_root,
            baseline=baseline,
        )

    def finalize(self, *, succeeded: bool) -> IsolationResult:
        prepared = self._prepared
        if prepared is None:
            raise RuntimeError("task workspace has not been prepared")
        final = _content_manifest(prepared.workspace_root)
        changes = _changes(prepared.baseline, final)

        bundle_root = self.artifact_root / "isolated-changes"
        changed_files_root = bundle_root / "files"
        if bundle_root.exists():
            shutil.rmtree(bundle_root)
        changed_files_root.mkdir(parents=True, exist_ok=True)
        for change in changes:
            if change.change_type == "deleted":
                continue
            source = prepared.workspace_root / Path(change.path)
            destination = changed_files_root / Path(change.path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                resolved = source.resolve()
                if resolved.is_file():
                    shutil.copy2(resolved, destination)
                else:
                    destination.write_text(os.readlink(source), encoding="utf-8")
            elif source.is_file():
                shutil.copy2(source, destination)

        initial_patch_path: Path | None = None
        final_patch_path: Path | None = None
        if prepared.source.is_git_repository:
            initial_patch_path = self.artifact_root / "source-initial.patch"
            initial_patch_path.write_bytes(prepared.initial_git_patch)
            final_patch_path = self.artifact_root / "isolated-final.patch"
            final_patch_path.write_bytes(
                _git_bytes(
                    prepared.workspace_root,
                    "diff",
                    "--binary",
                    "HEAD",
                    "--",
                )
            )

        should_retain = self.retention == IsolationRetention.ALWAYS or (
            self.retention == IsolationRetention.ON_FAILURE and not succeeded
        )
        manifest_path = self.artifact_root / "isolation-result.json"
        result = IsolationResult(
            session_id=prepared.session_id,
            strategy=prepared.strategy,
            source=prepared.source,
            workspace_root=str(prepared.workspace_root),
            retained=should_retain,
            succeeded=succeeded,
            changes=changes,
            change_manifest_path=str(manifest_path),
            changed_files_root=str(changed_files_root),
            initial_git_patch_path=(
                str(initial_patch_path) if initial_patch_path else None
            ),
            final_git_patch_path=(str(final_patch_path) if final_patch_path else None),
        )
        self._write_json(manifest_path, result.model_dump(mode="json"))
        if not should_retain:
            shutil.rmtree(prepared.session_root, ignore_errors=True)
        return result

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)
