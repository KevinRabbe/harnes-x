"""Immutable project-scoped file resources for M74."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .store import ProjectChatStore

_STRICT = ConfigDict(frozen=True, extra="forbid")
_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
_MAX_WORKSPACE_SNAPSHOT_BYTES = 1024 * 1024
_MAX_SOURCE_PATH_CHARS = 1024
_ATTACHMENT_ID = re.compile(r"^attachment_[0-9a-f]{32}$")
_SNAPSHOT_ID = re.compile(r"^file_snapshot_[0-9a-f]{32}$")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("project resource timestamp must be timezone-aware")
    return value


def _normalize_display_filename(value: str) -> str:
    value = value.strip()
    if not value or value in {".", ".."}:
        raise ValueError("attachment filename must be a nonblank display name")
    if len(value) > 255:
        raise ValueError("attachment filename is too long")
    if "/" in value or "\\" in value:
        raise ValueError("attachment filename must not contain path separators")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("attachment filename contains control characters")
    return value


def _normalize_media_type(value: str) -> str:
    value = value.strip().casefold()
    if not value or len(value) > 128:
        raise ValueError("attachment media type is invalid")
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise ValueError("attachment media type must contain visible ASCII only")
    return value


def _normalize_workspace_relative_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("workspace file path must be text")
    value = value.strip()
    if not value or len(value) > _MAX_SOURCE_PATH_CHARS:
        raise ValueError("workspace file path is empty or too long")
    if "\\" in value or "\x00" in value:
        raise ValueError("workspace file path must use bounded relative POSIX syntax")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("workspace file path contains control characters")
    candidate = PurePosixPath(value)
    if candidate.is_absolute():
        raise ValueError("workspace file path must be relative")
    parts = candidate.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("workspace file path contains traversal or empty components")
    for part in parts:
        if ":" in part or part.endswith((" ", ".")):
            raise ValueError("workspace file path contains an unsafe component")
        stem = part.split(".", 1)[0].casefold()
        if stem in _WINDOWS_RESERVED:
            raise ValueError("workspace file path contains a reserved component")
    normalized = candidate.as_posix()
    if normalized == ".":
        raise ValueError("workspace file path must identify a file")
    return normalized


def _is_textual_utf8(data: bytes) -> bool:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    return all(character in "\t\r\n" or ord(character) >= 32 for character in text)


class ProjectAttachmentRecord(BaseModel):
    """Immutable metadata for one project-owned user attachment."""

    model_config = _STRICT

    schema_version: Literal["project-attachment-v1"] = "project-attachment-v1"
    attachment_id: str = Field(pattern=r"^attachment_[0-9a-f]{32}$")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default="application/octet-stream", min_length=1, max_length=128)
    size_bytes: int = Field(ge=0, le=_MAX_ATTACHMENT_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_encoding: Literal["utf-8"] | None = None
    created_at: datetime
    fingerprint: str = ""

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        return _normalize_display_filename(value)

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        return _normalize_media_type(value)

    @field_validator("created_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_timestamp(value)

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ProjectAttachmentRecord":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", hashlib.sha256(_canonical(material)).hexdigest())
        return self


class ProjectWorkspaceFileSnapshotRecord(BaseModel):
    """Immutable metadata for bytes snapshotted from the canonical project workspace."""

    model_config = _STRICT

    schema_version: Literal["project-file-snapshot-v1"] = "project-file-snapshot-v1"
    snapshot_id: str = Field(pattern=r"^file_snapshot_[0-9a-f]{32}$")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    source_path: str = Field(min_length=1, max_length=_MAX_SOURCE_PATH_CHARS)
    size_bytes: int = Field(ge=0, le=_MAX_WORKSPACE_SNAPSHOT_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_encoding: Literal["utf-8"] | None = None
    created_at: datetime
    fingerprint: str = ""

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        return _normalize_workspace_relative_path(value)

    @field_validator("created_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_timestamp(value)

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ProjectWorkspaceFileSnapshotRecord":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", hashlib.sha256(_canonical(material)).hexdigest())
        return self


class ProjectResourceStore:
    """Immutable project attachment and workspace-file snapshots kept outside M66 state."""

    def __init__(self, product_store: ProjectChatStore) -> None:
        self.product_store = product_store

    def create_attachment(
        self,
        project_id: str,
        *,
        filename: str,
        data: bytes,
        media_type: str = "application/octet-stream",
    ) -> ProjectAttachmentRecord:
        project = self.product_store.project(project_id)
        if project.archived:
            raise ValueError("cannot add an attachment to an archived project")
        if not isinstance(data, bytes):
            raise TypeError("attachment data must be bytes")
        if len(data) > _MAX_ATTACHMENT_BYTES:
            raise ValueError("attachment exceeds the M74 byte limit")
        attachment_id = self._new_attachment_id(project_id)
        record = ProjectAttachmentRecord(
            attachment_id=attachment_id,
            project_id=project_id,
            filename=filename,
            media_type=media_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            text_encoding="utf-8" if _is_textual_utf8(data) else None,
            created_at=_now(),
        )
        self._persist(
            self._attachment_blob_path(project_id, attachment_id),
            self._attachment_metadata_path(project_id, attachment_id),
            record.model_dump_json(indent=2) + "\n",
            data,
        )
        return self.attachment(project_id, attachment_id)

    def attachment(self, project_id: str, attachment_id: str) -> ProjectAttachmentRecord:
        self.product_store.project(project_id)
        self._validate_attachment_id(attachment_id)
        path = self._attachment_metadata_path(project_id, attachment_id)
        if not path.is_file() or path.is_symlink():
            raise KeyError(f"unknown project attachment {attachment_id}")
        raw = self._load_metadata(path, "attachment")
        stored_fingerprint = str(raw.get("fingerprint", ""))
        try:
            record = ProjectAttachmentRecord.model_validate(raw)
        except ValueError as exc:
            raise ValueError(f"cannot validate project attachment metadata: {exc}") from exc
        if record.project_id != project_id or record.attachment_id != attachment_id:
            raise ValueError("project attachment owner identity mismatch")
        if stored_fingerprint != record.fingerprint:
            raise ValueError("project attachment metadata fingerprint mismatch")
        self._verified_blob(
            self._attachment_blob_path(project_id, attachment_id),
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            limit=_MAX_ATTACHMENT_BYTES,
            kind="project attachment",
        )
        return record

    def attachment_bytes(self, project_id: str, attachment_id: str) -> bytes:
        record = self.attachment(project_id, attachment_id)
        return self._verified_blob(
            self._attachment_blob_path(project_id, attachment_id),
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            limit=_MAX_ATTACHMENT_BYTES,
            kind="project attachment",
        )

    def snapshot_workspace_file(
        self,
        project_id: str,
        *,
        source_path: str,
    ) -> ProjectWorkspaceFileSnapshotRecord:
        project = self.product_store.project(project_id)
        if project.archived:
            raise ValueError("cannot snapshot a workspace file for an archived project")
        normalized = _normalize_workspace_relative_path(source_path)
        source = self._workspace_file(project.workspace_root, normalized)
        data = self._read_regular_file(
            source,
            limit=_MAX_WORKSPACE_SNAPSHOT_BYTES,
            kind="workspace file",
        )
        snapshot_id = self._new_snapshot_id(project_id)
        record = ProjectWorkspaceFileSnapshotRecord(
            snapshot_id=snapshot_id,
            project_id=project_id,
            source_path=normalized,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            text_encoding="utf-8" if _is_textual_utf8(data) else None,
            created_at=_now(),
        )
        self._persist(
            self._snapshot_blob_path(project_id, snapshot_id),
            self._snapshot_metadata_path(project_id, snapshot_id),
            record.model_dump_json(indent=2) + "\n",
            data,
        )
        return self.workspace_file_snapshot(project_id, snapshot_id)

    def workspace_file_snapshot(
        self,
        project_id: str,
        snapshot_id: str,
    ) -> ProjectWorkspaceFileSnapshotRecord:
        self.product_store.project(project_id)
        self._validate_snapshot_id(snapshot_id)
        path = self._snapshot_metadata_path(project_id, snapshot_id)
        if not path.is_file() or path.is_symlink():
            raise KeyError(f"unknown workspace file snapshot {snapshot_id}")
        raw = self._load_metadata(path, "workspace file snapshot")
        stored_fingerprint = str(raw.get("fingerprint", ""))
        try:
            record = ProjectWorkspaceFileSnapshotRecord.model_validate(raw)
        except ValueError as exc:
            raise ValueError(f"cannot validate workspace file snapshot metadata: {exc}") from exc
        if record.project_id != project_id or record.snapshot_id != snapshot_id:
            raise ValueError("workspace file snapshot owner identity mismatch")
        if stored_fingerprint != record.fingerprint:
            raise ValueError("workspace file snapshot metadata fingerprint mismatch")
        self._verified_blob(
            self._snapshot_blob_path(project_id, snapshot_id),
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            limit=_MAX_WORKSPACE_SNAPSHOT_BYTES,
            kind="workspace file snapshot",
        )
        return record

    def workspace_file_bytes(self, project_id: str, snapshot_id: str) -> bytes:
        record = self.workspace_file_snapshot(project_id, snapshot_id)
        return self._verified_blob(
            self._snapshot_blob_path(project_id, snapshot_id),
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            limit=_MAX_WORKSPACE_SNAPSHOT_BYTES,
            kind="workspace file snapshot",
        )

    def _workspace_file(self, workspace_root: str, relative_path: str) -> Path:
        root = Path(workspace_root)
        if not root.exists() or not root.is_dir() or root.is_symlink():
            raise ValueError("project workspace is unavailable or no longer canonical")
        try:
            resolved_root = root.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"cannot resolve project workspace: {exc}") from exc
        if os.path.normcase(os.path.normpath(str(resolved_root))) != os.path.normcase(
            os.path.normpath(workspace_root)
        ):
            raise ValueError("project workspace canonical identity changed")
        candidate = root
        for part in PurePosixPath(relative_path).parts:
            candidate = candidate / part
            if candidate.is_symlink():
                raise ValueError("workspace file path must not traverse symlinks")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except (OSError, ValueError) as exc:
            raise ValueError("workspace file path escapes or does not exist in the project workspace") from exc
        if resolved != candidate:
            raise ValueError("workspace file path changed during canonical resolution")
        return candidate

    @staticmethod
    def _read_regular_file(path: Path, *, limit: int, kind: str) -> bytes:
        try:
            info = path.lstat()
        except OSError as exc:
            raise ValueError(f"cannot inspect {kind}: {exc}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{kind} must be a regular file")
        if info.st_size > limit:
            raise ValueError(f"{kind} exceeds the M74 byte limit")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read {kind}: {exc}") from exc
        if len(data) != info.st_size:
            raise ValueError(f"{kind} changed while being read")
        return data

    @staticmethod
    def _persist(blob_path: Path, metadata_path: Path, metadata: str, data: bytes) -> None:
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        if blob_path.exists() or metadata_path.exists():
            raise RuntimeError("project resource identity collision")
        blob_temp = blob_path.with_name(f".{blob_path.name}.{uuid.uuid4().hex}.tmp")
        metadata_temp = metadata_path.with_name(f".{metadata_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with blob_temp.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(blob_temp, blob_path)
            with metadata_temp.open("xb") as handle:
                handle.write(metadata.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(metadata_temp, metadata_path)
        finally:
            for temporary in (blob_temp, metadata_temp):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _load_metadata(path: Path, kind: str) -> dict[str, object]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load {kind} metadata: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"cannot validate {kind} metadata: expected a JSON object")
        return raw

    @staticmethod
    def _verified_blob(
        path: Path,
        *,
        size_bytes: int,
        sha256: str,
        limit: int,
        kind: str,
    ) -> bytes:
        try:
            info = path.lstat()
        except OSError as exc:
            raise ValueError(f"cannot load {kind} bytes: {exc}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{kind} blob must be a regular file")
        if info.st_size > limit or info.st_size != size_bytes:
            raise ValueError(f"{kind} blob size mismatch")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot load {kind} bytes: {exc}") from exc
        if len(data) != size_bytes or hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError(f"{kind} blob digest mismatch")
        return data

    def _new_attachment_id(self, project_id: str) -> str:
        for _ in range(8):
            identifier = f"attachment_{uuid.uuid4().hex}"
            if not self._attachment_blob_path(project_id, identifier).exists() and not self._attachment_metadata_path(
                project_id, identifier
            ).exists():
                return identifier
        raise RuntimeError("could not allocate project attachment identity")

    def _new_snapshot_id(self, project_id: str) -> str:
        for _ in range(8):
            identifier = f"file_snapshot_{uuid.uuid4().hex}"
            if not self._snapshot_blob_path(project_id, identifier).exists() and not self._snapshot_metadata_path(
                project_id, identifier
            ).exists():
                return identifier
        raise RuntimeError("could not allocate workspace snapshot identity")

    @staticmethod
    def _validate_attachment_id(attachment_id: str) -> None:
        if not _ATTACHMENT_ID.fullmatch(attachment_id):
            raise ValueError("invalid project attachment identity")

    @staticmethod
    def _validate_snapshot_id(snapshot_id: str) -> None:
        if not _SNAPSHOT_ID.fullmatch(snapshot_id):
            raise ValueError("invalid workspace file snapshot identity")

    def _resource_root(self, project_id: str) -> Path:
        return self.product_store.projects_root / project_id / "resources"

    def _attachment_blob_path(self, project_id: str, attachment_id: str) -> Path:
        return self._resource_root(project_id) / "attachments" / "blobs" / f"{attachment_id}.blob"

    def _attachment_metadata_path(self, project_id: str, attachment_id: str) -> Path:
        return self._resource_root(project_id) / "attachments" / "metadata" / f"{attachment_id}.json"

    def _snapshot_blob_path(self, project_id: str, snapshot_id: str) -> Path:
        return self._resource_root(project_id) / "workspace-snapshots" / "blobs" / f"{snapshot_id}.blob"

    def _snapshot_metadata_path(self, project_id: str, snapshot_id: str) -> Path:
        return self._resource_root(project_id) / "workspace-snapshots" / "metadata" / f"{snapshot_id}.json"
