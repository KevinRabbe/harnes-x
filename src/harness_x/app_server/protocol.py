"""Typed protocol contracts for the local Harness X App Server.

M34 exposes existing Harness X runtime state to a future GUI without moving authority into
HTTP/UI code. The server is single-user and loopback-only; these models define the stable
wire contract and durable session projection.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


class AppSessionStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


class AppEventKind(StrEnum):
    SESSION_CREATED = "session_created"
    SESSION_STARTED = "session_started"
    SESSION_STATUS = "session_status"
    SESSION_CANCEL_REQUESTED = "session_cancel_requested"
    SESSION_COMPLETED = "session_completed"
    SESSION_FAILED = "session_failed"
    SESSION_CANCELLED = "session_cancelled"
    ARTIFACT_AVAILABLE = "artifact_available"


class CodingSessionRequest(BaseModel):
    """One explicit coding-session request accepted by the local app server."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-coding-session-request-v1"] = (
        "app-coding-session-request-v1"
    )
    workspace_root: Path
    task: str = Field(min_length=1, max_length=20000)
    model_profile: str = Field(min_length=1, max_length=80)
    verification_commands: tuple[str, ...] = Field(default=(), max_length=32)
    verification_plan_path: Path | None = None
    project_memory_root: Path | None = None
    project_memory_key: str | None = Field(default=None, max_length=1000)
    max_reasoning_steps: int = Field(default=32, ge=1, le=512)
    max_tool_actions: int = Field(default=48, ge=1, le=2048)
    max_output_tokens: int = Field(default=65536, ge=1024, le=1048576)
    baseline_verification: bool = True
    browser_application_spec_path: Path | None = None
    browser_verification_plan_path: Path | None = None
    browser_headed: bool = False

    @field_validator("task", "model_profile")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value cannot be blank")
        return stripped

    @field_validator("project_memory_key")
    @classmethod
    def _strip_optional_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("project memory key cannot be blank")
        return stripped

    @field_validator("workspace_root")
    @classmethod
    def _resolve_workspace(cls, value: Path) -> Path:
        resolved = value.expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"workspace_root must be an existing directory: {resolved}")
        return resolved

    @field_validator("project_memory_root")
    @classmethod
    def _resolve_memory_root(cls, value: Path | None) -> Path | None:
        return value.expanduser().resolve() if value is not None else None

    @field_validator(
        "verification_plan_path",
        "browser_application_spec_path",
        "browser_verification_plan_path",
    )
    @classmethod
    def _resolve_required_files(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        resolved = value.expanduser().resolve()
        if not resolved.is_file():
            raise ValueError(f"configured app-server file does not exist: {resolved}")
        return resolved

    @field_validator("verification_commands")
    @classmethod
    def _validate_commands(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("verification commands cannot be blank")
        return normalized

    @model_validator(mode="after")
    def _validate_verification(self) -> "CodingSessionRequest":
        if not self.verification_commands and self.verification_plan_path is None:
            raise ValueError(
                "coding session requires verification_commands or verification_plan_path"
            )
        browser_paths = (
            self.browser_application_spec_path,
            self.browser_verification_plan_path,
        )
        if any(item is not None for item in browser_paths) and not all(
            item is not None for item in browser_paths
        ):
            raise ValueError(
                "browser mode requires both application spec and browser verification plan"
            )
        return self


class AppSessionSnapshot(BaseModel):
    """Durable software-owned projection for one app-server session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-session-snapshot-v1"] = "app-session-snapshot-v1"
    session_id: str = Field(pattern=r"^app_[0-9a-f]{32}$")
    status: AppSessionStatus
    request: CodingSessionRequest
    output_root: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    event_count: int = Field(default=0, ge=0)
    latest_event_hash: str | None = Field(default=None, min_length=64, max_length=64)
    coding_report_path: str | None = None
    failure_reason: str | None = Field(default=None, max_length=4000)
    cancel_requested: bool = False
    revision: int = Field(default=1, ge=1)
    fingerprint: str = ""

    @model_validator(mode="after")
    def _derive_fingerprint(self) -> "AppSessionSnapshot":
        if self.status.terminal and self.completed_at is None:
            raise ValueError("terminal app session requires completed_at")
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", hashlib.sha256(_canonical(material)).hexdigest())
        return self


class AppEvent(BaseModel):
    """Hash-chained append-only event emitted by the app server."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-event-v1"] = "app-event-v1"
    session_id: str = Field(pattern=r"^app_[0-9a-f]{32}$")
    sequence: int = Field(ge=1)
    kind: AppEventKind
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    previous_hash: str | None = Field(default=None, min_length=64, max_length=64)
    event_hash: str = Field(min_length=64, max_length=64)

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        sequence: int,
        kind: AppEventKind,
        payload: dict[str, Any] | None = None,
        previous_hash: str | None = None,
        created_at: datetime | None = None,
    ) -> "AppEvent":
        timestamp = created_at or datetime.now(timezone.utc)
        material = {
            "schema_version": "app-event-v1",
            "session_id": session_id,
            "sequence": sequence,
            "kind": kind.value,
            "created_at": timestamp.isoformat(),
            "payload": payload or {},
            "previous_hash": previous_hash,
        }
        digest = hashlib.sha256(_canonical(material)).hexdigest()
        return cls(
            session_id=session_id,
            sequence=sequence,
            kind=kind,
            created_at=timestamp,
            payload=payload or {},
            previous_hash=previous_hash,
            event_hash=digest,
        )

    def verify_hash(self) -> bool:
        material = {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "kind": self.kind.value,
            "created_at": self.created_at.isoformat(),
            "payload": self.payload,
            "previous_hash": self.previous_hash,
        }
        return self.event_hash == hashlib.sha256(_canonical(material)).hexdigest()


class AppServerHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-server-health-v1"] = "app-server-health-v1"
    ok: Literal[True] = True
    server_version: str
    active_sessions: int = Field(ge=0)
    total_sessions: int = Field(ge=0)


class AppServerError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-server-error-v1"] = "app-server-error-v1"
    error: str
    detail: str | None = None
