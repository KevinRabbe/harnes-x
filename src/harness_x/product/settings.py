"""Versioned project settings kept separate from the frozen M66 project/chat state file."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .store import ProjectChatStore

_STRICT = ConfigDict(frozen=True, extra="forbid")
_PROFILE_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"


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


class ProjectVerificationStrategy(StrEnum):
    DIFF_CHECK = "diff_check"
    PYTEST = "pytest"
    PYTEST_AND_DIFF_CHECK = "pytest_and_diff_check"


class ProjectAutonomyProfile(StrEnum):
    STANDARD = "standard"
    CAUTIOUS = "cautious"


class ProjectSettingsRecord(BaseModel):
    """One complete project-owned settings value used only by future work submissions."""

    model_config = _STRICT

    schema_version: Literal["project-settings-v1"] = "project-settings-v1"
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    model_profile: str = Field(default="main", pattern=_PROFILE_PATTERN)
    verification_strategy: ProjectVerificationStrategy = ProjectVerificationStrategy.DIFF_CHECK
    project_instructions: str = Field(default="", max_length=6000)
    autonomy_profile: ProjectAutonomyProfile = ProjectAutonomyProfile.STANDARD
    revision: int = Field(default=1, ge=1)
    updated_at: datetime
    fingerprint: str = ""

    @field_validator("model_profile")
    @classmethod
    def normalize_model_profile(cls, value: str) -> str:
        return value.strip().casefold()

    @field_validator("project_instructions")
    @classmethod
    def normalize_project_instructions(cls, value: str) -> str:
        return value.strip()

    @field_validator("updated_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("project settings timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ProjectSettingsRecord":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(
            self,
            "fingerprint",
            hashlib.sha256(_canonical(material)).hexdigest(),
        )
        return self


class ProjectSettingsStore:
    """Atomic per-project settings files without changing M66's state fingerprint schema."""

    def __init__(self, product_store: ProjectChatStore) -> None:
        self.product_store = product_store
        self.root = product_store.root

    def settings(self, project_id: str) -> ProjectSettingsRecord:
        project = self.product_store.project(project_id)
        path = self._path(project_id)
        if not path.exists():
            # Legacy M66-M72 projects synthesize a deterministic value without rewriting state.
            return ProjectSettingsRecord(
                project_id=project_id,
                model_profile=project.default_model_profile or "main",
                updated_at=project.created_at,
            )
        return self._load(project_id, path)

    def persisted(self, project_id: str) -> bool:
        self.product_store.project(project_id)
        return self._path(project_id).is_file()

    def replace(
        self,
        project_id: str,
        *,
        model_profile: str,
        verification_strategy: ProjectVerificationStrategy | str,
        project_instructions: str,
        autonomy_profile: ProjectAutonomyProfile | str,
    ) -> ProjectSettingsRecord:
        project = self.product_store.project(project_id)
        if project.archived:
            raise ValueError("cannot update settings for an archived project")
        path = self._path(project_id)
        revision = self._load(project_id, path).revision + 1 if path.exists() else 1
        record = ProjectSettingsRecord(
            project_id=project_id,
            model_profile=model_profile,
            verification_strategy=verification_strategy,
            project_instructions=project_instructions,
            autonomy_profile=autonomy_profile,
            revision=revision,
            updated_at=_now(),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (record.model_dump_json(indent=2) + "\n").encode("utf-8")
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return self._load(project_id, path)

    def _load(self, project_id: str, path: Path) -> ProjectSettingsRecord:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load project settings: {exc}") from exc
        stored_fingerprint = str(raw.get("fingerprint", ""))
        try:
            record = ProjectSettingsRecord.model_validate(raw)
        except ValueError as exc:
            raise ValueError(f"cannot validate project settings: {exc}") from exc
        if record.project_id != project_id:
            raise ValueError("project settings owner identity mismatch")
        if stored_fingerprint != record.fingerprint:
            raise ValueError("project settings fingerprint mismatch")
        return record

    def _path(self, project_id: str) -> Path:
        return self.product_store.projects_root / project_id / "settings.json"
