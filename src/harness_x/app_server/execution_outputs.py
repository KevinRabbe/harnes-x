"""Bounded M74 projections for execution workspace changes and registered outputs."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from harness_x.tools.git_v2 import git_status_v2_definition
from harness_x.tools.repository import (
    GitDiffInput,
    GitDiffOutput,
    GitStatusInput,
    GitStatusOutput,
    git_diff_definition,
)

from .protocol import AppEvent, AppEventKind, AppSessionSnapshot

_STRICT = ConfigDict(frozen=True, extra="forbid")
_MAX_DIFF_FILES = 50
_MAX_PATCH_CHARS_PER_FILE = 6000
_MAX_PATCH_BYTES_PER_FILE = 24_000
_MAX_PATCH_LINES_PER_FILE = 120
_MAX_ARTIFACT_BYTES = 4 * 1024 * 1024


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _safe_relative_path(value: str) -> str:
    if not value or len(value) > 1024 or "\\" in value or "\x00" in value:
        raise ValueError("execution diff path must be a bounded relative POSIX path")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("execution diff path contains control characters")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or not candidate.parts:
        raise ValueError("execution diff path must be relative")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("execution diff path contains traversal components")
    normalized = candidate.as_posix()
    if normalized == ".":
        raise ValueError("execution diff path must identify a file")
    return normalized


def _canonical_workspace(value: str | Path) -> Path:
    root = Path(value)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("project workspace is unavailable or no longer canonical")
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"cannot resolve project workspace: {exc}") from exc
    if os.path.normcase(os.path.normpath(str(resolved))) != os.path.normcase(
        os.path.normpath(str(root))
    ):
        raise ValueError("project workspace canonical identity changed")
    return resolved


def _status_output(root: Path) -> GitStatusOutput:
    definition = git_status_v2_definition(root)
    result = definition.handler(GitStatusInput(refresh_repository_identity=True))
    return result if isinstance(result, GitStatusOutput) else GitStatusOutput.model_validate(result)


def _diff_output(root: Path, path: str, *, staged: bool) -> GitDiffOutput:
    definition = git_diff_definition(root)
    result = definition.handler(
        GitDiffInput(
            path=path,
            staged=staged,
            context_lines=3,
            max_chars=2800,
        )
    )
    return result if isinstance(result, GitDiffOutput) else GitDiffOutput.model_validate(result)


class ExecutionDiffFileProjection(BaseModel):
    model_config = _STRICT

    path: str = Field(min_length=1, max_length=1024)
    status: Literal["added", "modified", "deleted"]
    index_status: str = Field(min_length=1, max_length=1)
    worktree_status: str = Field(min_length=1, max_length=1)
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    patch: str | None = Field(default=None, max_length=_MAX_PATCH_CHARS_PER_FILE)
    patch_truncated: bool = False

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _safe_relative_path(value)


class ExecutionDiffProjection(BaseModel):
    """Bounded current-workspace Git projection; visibility is not execution authorship proof."""

    model_config = _STRICT

    schema_version: Literal["conversation-execution-diff-projection-v1"] = (
        "conversation-execution-diff-projection-v1"
    )
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    execution_id: str = Field(pattern=r"^exec_[0-9a-f]{32}$")
    session_id: str = Field(pattern=r"^app_[0-9a-f]{32}$")
    source: Literal["canonical-workspace-git-read-v1"] = "canonical-workspace-git-read-v1"
    available: bool
    unavailable_reason: str | None = Field(default=None, max_length=160)
    files: tuple[ExecutionDiffFileProjection, ...] = Field(default=(), max_length=_MAX_DIFF_FILES)
    detected_files: int = Field(default=0, ge=0)
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    truncated: bool = False
    unsafe_entries_skipped: int = Field(default=0, ge=0)
    read_only: Literal[True] = True
    execution_authorship_proven: Literal[False] = False
    verification_authority: Literal[False] = False
    evidence_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_availability(self) -> "ExecutionDiffProjection":
        if self.available and self.unavailable_reason is not None:
            raise ValueError("available execution diff cannot carry an unavailable reason")
        if not self.available and self.unavailable_reason is None:
            raise ValueError("unavailable execution diff requires a reason")
        return self


def _status_kind(index: str, worktree: str) -> Literal["added", "modified", "deleted"]:
    markers = {index, worktree}
    if "?" in markers or "A" in markers:
        return "added"
    if "D" in markers:
        return "deleted"
    return "modified"


def _truncate_patch(text: str) -> tuple[str, bool]:
    lines: list[str] = []
    chars = 0
    byte_count = 0
    truncated = False
    for line in text.splitlines(keepends=True):
        encoded = line.encode("utf-8")
        if (
            len(lines) >= _MAX_PATCH_LINES_PER_FILE
            or chars + len(line) > _MAX_PATCH_CHARS_PER_FILE
            or byte_count + len(encoded) > _MAX_PATCH_BYTES_PER_FILE
        ):
            truncated = True
            break
        lines.append(line)
        chars += len(line)
        byte_count += len(encoded)
    return "".join(lines), truncated


def _patch_stats(patch: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def _patch_for_status(root: Path, path: str, index: str, worktree: str) -> tuple[str | None, bool]:
    fragments: list[str] = []
    source_truncated = False
    if index not in {" ", "?"}:
        staged = _diff_output(root, path, staged=True)
        if staged.diff:
            fragments.append("STAGED CHANGES\n" + staged.diff)
        source_truncated = source_truncated or staged.truncated
    if worktree not in {" ", "?"}:
        working = _diff_output(root, path, staged=False)
        if working.diff:
            fragments.append("WORKTREE CHANGES\n" + working.diff)
        source_truncated = source_truncated or working.truncated
    if not fragments:
        return None, source_truncated
    rendered, projection_truncated = _truncate_patch("\n".join(fragments))
    return rendered or None, source_truncated or projection_truncated


def build_execution_diff_projection(
    *,
    project_id: str,
    chat_id: str,
    execution_id: str,
    snapshot: AppSessionSnapshot,
    workspace_root: str | Path,
) -> ExecutionDiffProjection:
    """Read the existing side-effect-free Git tools and expose a tighter product projection."""

    root = _canonical_workspace(workspace_root)
    if Path(snapshot.request.workspace_root).resolve() != root:
        raise ValueError("conversation execution workspace no longer matches project identity")
    try:
        status = _status_output(root)
    except (OSError, RuntimeError, ValueError):
        return ExecutionDiffProjection(
            project_id=project_id,
            chat_id=chat_id,
            execution_id=execution_id,
            session_id=snapshot.session_id,
            available=False,
            unavailable_reason="repository_status_unavailable",
        )
    if not status.identity.is_git_repository:
        return ExecutionDiffProjection(
            project_id=project_id,
            chat_id=chat_id,
            execution_id=execution_id,
            session_id=snapshot.session_id,
            available=False,
            unavailable_reason="workspace_not_git_repository",
        )

    safe_entries: list[tuple[str, str, str]] = []
    unsafe = 0
    for entry in status.entries:
        try:
            path = _safe_relative_path(entry.path)
        except ValueError:
            unsafe += 1
            continue
        safe_entries.append((path, entry.index_status, entry.worktree_status))
    detected = len(safe_entries)
    selected = safe_entries[:_MAX_DIFF_FILES]
    truncated = status.truncated or detected > len(selected)
    files: list[ExecutionDiffFileProjection] = []
    for path, index, worktree in selected:
        try:
            patch, patch_truncated = _patch_for_status(root, path, index, worktree)
        except (OSError, RuntimeError, ValueError):
            patch, patch_truncated = None, True
        additions, deletions = _patch_stats(patch or "")
        truncated = truncated or patch_truncated
        files.append(
            ExecutionDiffFileProjection(
                path=path,
                status=_status_kind(index, worktree),
                index_status=index,
                worktree_status=worktree,
                additions=additions,
                deletions=deletions,
                patch=patch,
                patch_truncated=patch_truncated,
            )
        )
    return ExecutionDiffProjection(
        project_id=project_id,
        chat_id=chat_id,
        execution_id=execution_id,
        session_id=snapshot.session_id,
        available=True,
        files=tuple(files),
        detected_files=detected,
        additions=sum(item.additions for item in files),
        deletions=sum(item.deletions for item in files),
        truncated=truncated,
        unsafe_entries_skipped=unsafe,
    )


class ExecutionArtifactRecord(BaseModel):
    """Product registration for one software-announced output under the session run root."""

    model_config = _STRICT

    schema_version: Literal["conversation-execution-artifact-v1"] = (
        "conversation-execution-artifact-v1"
    )
    artifact_id: str = Field(pattern=r"^artifact_[0-9a-f]{32}$")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    execution_id: str = Field(pattern=r"^exec_[0-9a-f]{32}$")
    session_id: str = Field(pattern=r"^app_[0-9a-f]{32}$")
    logical_name: str = Field(min_length=1, max_length=255)
    storage_name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=0, le=_MAX_ARTIFACT_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_sequence: int = Field(ge=1)
    source_event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    verification_authority: Literal[False] = False
    evidence_authority: Literal[False] = False
    fingerprint: str = ""

    @field_validator("storage_name")
    @classmethod
    def validate_storage_name(cls, value: str) -> str:
        if (
            value in {".", ".."}
            or "/" in value
            or "\\" in value
            or '"' in value
            or ";" in value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("artifact storage name is unsafe")
        return value

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ExecutionArtifactRecord":
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("artifact timestamp must be timezone-aware")
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", hashlib.sha256(_canonical(material)).hexdigest())
        return self


class ExecutionArtifactRegistry:
    """Append-only registrations derived only from existing App Server artifact events."""

    def __init__(self, root: str | Path) -> None:
        self.path = Path(root).resolve() / "conversation-execution-artifacts.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, ExecutionArtifactRecord] = {}
        if self.path.exists():
            self._load()

    def for_execution(self, execution_id: str) -> tuple[ExecutionArtifactRecord, ...]:
        return tuple(
            sorted(
                (item for item in self._items.values() if item.execution_id == execution_id),
                key=lambda item: (item.created_at, item.artifact_id),
            )
        )

    def artifact(self, artifact_id: str) -> ExecutionArtifactRecord:
        if not isinstance(artifact_id, str) or not artifact_id.startswith("artifact_"):
            raise ValueError("invalid execution artifact ID")
        try:
            return self._items[artifact_id]
        except KeyError as exc:
            raise KeyError(f"unknown execution artifact {artifact_id}") from exc

    def sync_known_artifacts(
        self,
        *,
        project_id: str,
        chat_id: str,
        execution_id: str,
        snapshot: AppSessionSnapshot,
        events: tuple[AppEvent, ...],
        run_root: str | Path,
    ) -> tuple[ExecutionArtifactRecord, ...]:
        if not snapshot.status.terminal:
            return self.for_execution(execution_id)
        supported = tuple(
            event
            for event in events
            if event.kind == AppEventKind.ARTIFACT_AVAILABLE
            and event.payload.get("artifact_kind") == "coding_task_report"
        )
        if len(supported) > 1:
            raise RuntimeError("execution has duplicate coding report artifact events")
        for event in supported:
            if event.session_id != snapshot.session_id:
                raise RuntimeError("execution artifact event belongs to another session")
            data, storage_name = self._validated_event_bytes(
                snapshot=snapshot,
                event=event,
                run_root=run_root,
            )
            digest = hashlib.sha256(data).hexdigest()
            raw_size = event.payload.get("source_bytes")
            raw_digest = event.payload.get("source_sha256")
            if raw_size is not None and raw_size != len(data):
                raise RuntimeError("execution artifact byte count disagrees with source event")
            if raw_digest is not None and raw_digest != digest:
                raise RuntimeError("execution artifact digest disagrees with source event")
            identity = hashlib.sha256(
                _canonical(
                    {
                        "execution_id": execution_id,
                        "event_hash": event.event_hash,
                        "storage_name": storage_name,
                    }
                )
            ).hexdigest()[:32]
            self._put(
                ExecutionArtifactRecord(
                    artifact_id=f"artifact_{identity}",
                    project_id=project_id,
                    chat_id=chat_id,
                    execution_id=execution_id,
                    session_id=snapshot.session_id,
                    logical_name="Coding task report",
                    storage_name=storage_name,
                    media_type="application/json",
                    size_bytes=len(data),
                    sha256=digest,
                    source_event_sequence=event.sequence,
                    source_event_hash=event.event_hash,
                    created_at=event.created_at,
                )
            )
        return self.for_execution(execution_id)

    def bytes_for(
        self,
        record: ExecutionArtifactRecord,
        *,
        snapshot: AppSessionSnapshot,
        run_root: str | Path,
    ) -> bytes:
        if record.session_id != snapshot.session_id:
            raise ValueError("execution artifact belongs to another session")
        output_root = self._validated_output_root(snapshot, run_root)
        data = self._read_regular(output_root / record.storage_name)
        if len(data) != record.size_bytes or hashlib.sha256(data).hexdigest() != record.sha256:
            raise RuntimeError("execution artifact current bytes do not match registration")
        return data

    def _put(self, record: ExecutionArtifactRecord) -> ExecutionArtifactRecord:
        existing = self._items.get(record.artifact_id)
        if existing is not None:
            if existing.fingerprint != record.fingerprint:
                raise RuntimeError("execution artifact registration identity conflict")
            return existing
        payload = _canonical(record.model_dump(mode="json")) + b"\n"
        with self.path.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self._items[record.artifact_id] = record
        return record

    def _load(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"cannot load execution artifact registry: {exc}") from exc
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                raise ValueError(f"blank execution artifact row {number}")
            try:
                raw = json.loads(line)
                stored_fingerprint = str(raw.get("fingerprint", ""))
                record = ExecutionArtifactRecord.model_validate(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"invalid execution artifact row {number}: {exc}") from exc
            if stored_fingerprint != record.fingerprint:
                raise ValueError("execution artifact registration fingerprint mismatch")
            if record.artifact_id in self._items:
                raise ValueError("duplicate execution artifact ID")
            self._items[record.artifact_id] = record

    @classmethod
    def _validated_event_bytes(
        cls,
        *,
        snapshot: AppSessionSnapshot,
        event: AppEvent,
        run_root: str | Path,
    ) -> tuple[bytes, str]:
        raw_path = event.payload.get("path")
        if not isinstance(raw_path, str):
            raise RuntimeError("execution artifact event path is missing")
        output_root = cls._validated_output_root(snapshot, run_root)
        expected = output_root / "coding-task-report.json"
        source = Path(raw_path)
        if not source.is_absolute() or source != expected or source.is_symlink():
            raise RuntimeError("execution artifact event path is not the canonical session output")
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError(f"execution artifact source is unavailable: {exc}") from exc
        if resolved != expected or resolved.parent != output_root:
            raise RuntimeError("execution artifact source escapes the session output root")
        return cls._read_regular(resolved), expected.name

    @staticmethod
    def _validated_output_root(snapshot: AppSessionSnapshot, run_root: str | Path) -> Path:
        root = Path(run_root).resolve()
        output = Path(snapshot.output_root)
        if not output.is_absolute() or output.is_symlink():
            raise RuntimeError("execution artifact output root is not canonical")
        try:
            resolved = output.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError(f"execution artifact output root is unavailable: {exc}") from exc
        if resolved.parent != root:
            raise RuntimeError("execution artifact output root escapes service run root")
        return resolved

    @staticmethod
    def _read_regular(path: Path) -> bytes:
        try:
            info = path.lstat()
        except OSError as exc:
            raise RuntimeError(f"cannot inspect execution artifact: {exc}") from exc
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise RuntimeError("execution artifact must be a regular file")
        if info.st_size > _MAX_ARTIFACT_BYTES:
            raise RuntimeError("execution artifact exceeds the M74 byte limit")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"cannot read execution artifact: {exc}") from exc
        if len(data) != info.st_size:
            raise RuntimeError("execution artifact changed while being read")
        return data


__all__ = [
    "ExecutionArtifactRecord",
    "ExecutionArtifactRegistry",
    "ExecutionDiffFileProjection",
    "ExecutionDiffProjection",
    "build_execution_diff_projection",
]
