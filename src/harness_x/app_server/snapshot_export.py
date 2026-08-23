"""Deterministic terminal App Server session snapshot export for portable verification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .protocol import AppSessionSnapshot

MAX_SESSION_SNAPSHOT_EXPORT_BYTES = 2 * 1024 * 1024


class SnapshotExportNotTerminalError(RuntimeError):
    """Portable snapshots are intentionally unavailable for mutable sessions."""


class SnapshotExportCorruptionError(RuntimeError):
    """The durable App Server snapshot does not satisfy its stored fingerprint contract."""


class SnapshotExportTooLargeError(RuntimeError):
    """The deterministic snapshot export exceeds the bounded response size."""


def canonical_snapshot_material(value: object) -> bytes:
    """Return the exact canonical JSON encoding used by AppSessionSnapshot fingerprints."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


class PortableCodingSessionRequest(BaseModel):
    """Structural portable request schema that never resolves downloaded filesystem paths."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-coding-session-request-v1"] = (
        "app-coding-session-request-v1"
    )
    workspace_root: str
    task: str = Field(min_length=1, max_length=20000)
    model_profile: str = Field(min_length=1, max_length=80)
    verification_commands: tuple[str, ...] = Field(default=(), max_length=32)
    verification_plan_path: str | None = None
    project_memory_root: str | None = None
    project_memory_key: str | None = Field(default=None, max_length=1000)
    max_reasoning_steps: int = Field(default=32, ge=1, le=512)
    max_tool_actions: int = Field(default=48, ge=1, le=2048)
    max_output_tokens: int = Field(default=65536, ge=1024, le=1048576)
    baseline_verification: bool = True
    browser_application_spec_path: str | None = None
    browser_verification_plan_path: str | None = None
    browser_headed: bool = False

    @model_validator(mode="after")
    def _validate_verification_shape(self) -> "PortableCodingSessionRequest":
        if not self.task.strip() or not self.model_profile.strip():
            raise ValueError("task and model_profile cannot be blank")
        if any(not item.strip() for item in self.verification_commands):
            raise ValueError("verification commands cannot be blank")
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
        if self.project_memory_key is not None and not self.project_memory_key.strip():
            raise ValueError("project memory key cannot be blank")
        return self


class PortableSessionSnapshot(BaseModel):
    """Portable structural mirror of app-session-snapshot-v1 with inert path strings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-session-snapshot-v1"] = "app-session-snapshot-v1"
    session_id: str = Field(pattern=r"^app_[0-9a-f]{32}$")
    status: Literal[
        "created",
        "running",
        "succeeded",
        "failed",
        "cancel_requested",
        "cancelled",
    ]
    request: PortableCodingSessionRequest
    output_root: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    event_count: int = Field(default=0, ge=0)
    latest_event_hash: str | None = Field(default=None, min_length=64, max_length=64)
    coding_report_path: str | None = None
    trace_id: str | None = Field(default=None, pattern=r"^trace_[0-9a-f]{32}$")
    trace_path: str | None = None
    failure_reason: str | None = Field(default=None, max_length=4000)
    cancel_requested: bool = False
    revision: int = Field(default=1, ge=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_snapshot_shape(self) -> "PortableSessionSnapshot":
        if self.status in {"succeeded", "failed", "cancelled"} and self.completed_at is None:
            raise ValueError("terminal app session requires completed_at")
        if (self.trace_id is None) != (self.trace_path is None):
            raise ValueError("trace_id and trace_path must be present together")
        return self


@dataclass(frozen=True, slots=True)
class RenderedSessionSnapshot:
    """Exact generated snapshot response bytes plus their validated identity."""

    payload: bytes
    source_bytes: int
    source_sha256: str
    fingerprint: str
    revision: int


def validate_terminal_session_snapshot(
    *,
    snapshot: AppSessionSnapshot,
    expected_session_id: str | None = None,
) -> AppSessionSnapshot:
    """Revalidate a terminal durable snapshot and require its stored fingerprint to agree."""

    if not snapshot.status.terminal:
        raise SnapshotExportNotTerminalError(
            "session snapshot export is available only after the App Server session is terminal"
        )
    if snapshot.completed_at is None:
        raise SnapshotExportCorruptionError("terminal session snapshot is missing completed_at")
    if expected_session_id is not None and snapshot.session_id != expected_session_id:
        raise SnapshotExportCorruptionError(
            "session snapshot identity does not match the requested session"
        )

    supplied_fingerprint = snapshot.fingerprint
    raw = snapshot.model_dump(mode="json")
    try:
        revalidated = AppSessionSnapshot.model_validate(raw)
    except Exception as exc:
        raise SnapshotExportCorruptionError(
            f"session snapshot cannot be revalidated: {exc}"
        ) from exc
    if supplied_fingerprint != revalidated.fingerprint:
        raise SnapshotExportCorruptionError(
            "session snapshot fingerprint does not match snapshot contents"
        )
    if revalidated.session_id != snapshot.session_id:
        raise SnapshotExportCorruptionError("session snapshot identity changed during revalidation")
    return snapshot


def render_terminal_session_snapshot(
    *,
    snapshot: AppSessionSnapshot,
    expected_session_id: str | None = None,
    maximum_bytes: int = MAX_SESSION_SNAPSHOT_EXPORT_BYTES,
) -> RenderedSessionSnapshot:
    """Validate, serialize once, and retain one deterministic terminal snapshot payload."""

    if maximum_bytes < 1 or maximum_bytes > MAX_SESSION_SNAPSHOT_EXPORT_BYTES:
        raise ValueError(
            f"maximum_bytes must be between 1 and {MAX_SESSION_SNAPSHOT_EXPORT_BYTES}"
        )
    validated = validate_terminal_session_snapshot(
        snapshot=snapshot,
        expected_session_id=expected_session_id,
    )
    payload = validated.model_dump_json().encode("utf-8") + b"\n"
    if len(payload) > maximum_bytes:
        raise SnapshotExportTooLargeError(
            f"session snapshot export exceeds {maximum_bytes} byte limit"
        )
    return RenderedSessionSnapshot(
        payload=payload,
        source_bytes=len(payload),
        source_sha256=hashlib.sha256(payload).hexdigest(),
        fingerprint=validated.fingerprint,
        revision=validated.revision,
    )
