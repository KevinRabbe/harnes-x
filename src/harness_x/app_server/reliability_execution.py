"""M75 everyday execution interruption, stop, and exact-input retry contracts."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .conversation_execution import (
    ConversationExecutionBindingKind,
    ConversationExecutionPlan,
    ConversationExecutionProjection,
)
from .project_settings_execution import ProjectSettingsExecutionSnapshot
from .resource_execution import (
    ConversationExecutionResourceSnapshot,
    ProjectResourceConversationExecutionCoordinator,
    _render_resource_context,
)

_STRICT = ConfigDict(frozen=True, extra="forbid")
_M75_OUTPUT_PREFIX = "conversation_reliability_"
_RESTART_INTERRUPTION = "app_server_restart_interrupted_running_session"


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


class ConversationExecutionRetryRequest(BaseModel):
    """One explicit retry identity; prior work inputs are resolved server-side."""

    model_config = _STRICT

    schema_version: Literal["conversation-execution-retry-v1"] = (
        "conversation-execution-retry-v1"
    )
    submission_id: str = Field(pattern=r"^submission_[0-9a-f]{32}$")


class ConversationExecutionStopRequest(BaseModel):
    """Exact empty stop request so no browser-supplied session identity becomes authority."""

    model_config = _STRICT

    schema_version: Literal["conversation-execution-stop-v1"] = (
        "conversation-execution-stop-v1"
    )


class ConversationExecutionRetryRecord(BaseModel):
    """Append-only provenance linking a new explicit retry to its immutable source execution."""

    model_config = _STRICT

    schema_version: Literal["conversation-execution-retry-record-v1"] = (
        "conversation-execution-retry-record-v1"
    )
    execution_id: str = Field(pattern=r"^exec_[0-9a-f]{32}$")
    source_execution_id: str = Field(pattern=r"^exec_[0-9a-f]{32}$")
    submission_id: str = Field(pattern=r"^submission_[0-9a-f]{32}$")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    created_at: datetime
    fingerprint: str = ""

    @model_validator(mode="after")
    def validate_and_fingerprint(self) -> "ConversationExecutionRetryRecord":
        if self.execution_id == self.source_execution_id:
            raise ValueError("retry execution must differ from its source execution")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("retry timestamp must be timezone-aware")
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", hashlib.sha256(_canonical(material)).hexdigest())
        return self


class ConversationExecutionRetryStore:
    """Append-only M75 retry lineage; orphan rows are inert until a plan exists."""

    def __init__(self, root: str | Path) -> None:
        self.path = Path(root).resolve() / "conversation-execution-retries.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._by_execution: dict[str, ConversationExecutionRetryRecord] = {}
        self._by_submission: dict[str, ConversationExecutionRetryRecord] = {}
        if self.path.exists():
            self._load()

    def record(self, execution_id: str) -> ConversationExecutionRetryRecord | None:
        return self._by_execution.get(execution_id)

    def record_for_submission(
        self, submission_id: str
    ) -> ConversationExecutionRetryRecord | None:
        return self._by_submission.get(submission_id)

    def put(self, item: ConversationExecutionRetryRecord) -> ConversationExecutionRetryRecord:
        existing = self._by_execution.get(item.execution_id)
        if existing is not None:
            if existing.fingerprint != item.fingerprint:
                raise RuntimeError("conversation retry execution identity conflict")
            return existing
        submission = self._by_submission.get(item.submission_id)
        if submission is not None:
            if submission.fingerprint != item.fingerprint:
                raise RuntimeError("conversation retry submission identity conflict")
            return submission
        payload = _canonical(item.model_dump(mode="json")) + b"\n"
        with self.path.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self._by_execution[item.execution_id] = item
        self._by_submission[item.submission_id] = item
        return item

    def _load(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"cannot load conversation retry records: {exc}") from exc
        for number, line in enumerate(lines, start=1):
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise ValueError("retry row must be a JSON object")
                stored_fingerprint = str(raw.get("fingerprint", ""))
                item = ConversationExecutionRetryRecord.model_validate(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"invalid conversation retry line {number}: {exc}") from exc
            if stored_fingerprint != item.fingerprint:
                raise ValueError("conversation retry fingerprint mismatch")
            if item.execution_id in self._by_execution:
                raise ValueError("duplicate conversation retry execution ID")
            if item.submission_id in self._by_submission:
                raise ValueError("duplicate conversation retry submission ID")
            self._by_execution[item.execution_id] = item
            self._by_submission[item.submission_id] = item


class ConversationExecutionReliabilityProjection(BaseModel):
    """Read-only everyday recovery controls derived from durable execution/session state."""

    model_config = _STRICT

    schema_version: Literal["conversation-execution-reliability-v1"] = (
        "conversation-execution-reliability-v1"
    )
    execution_id: str = Field(pattern=r"^exec_[0-9a-f]{32}$")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    status: str = Field(min_length=1, max_length=64)
    terminal: bool
    interrupted_by_restart: bool = False
    can_stop: bool
    can_retry: bool
    can_continue: bool
    retry_source_execution_id: str | None = Field(
        default=None,
        pattern=r"^exec_[0-9a-f]{32}$",
    )


class ReliableProjectResourceConversationExecutionCoordinator(
    ProjectResourceConversationExecutionCoordinator
):
    """M74 coordinator plus explicit M75 recovery actions over durable state."""

    def __init__(self, service, product_store, product_lock, root) -> None:
        self.retry_store = ConversationExecutionRetryStore(root)
        super().__init__(service, product_store, product_lock, root)
        for plan in self.store.plans:
            if not self._is_m75_plan(plan):
                continue
            record = self.retry_store.record(plan.execution_id)
            if record is None:
                raise RuntimeError("M75 retry execution is missing retry provenance")
            self._require_retry_record_identity(plan, record)

    def reliability_projection(
        self,
        project_id: str,
        chat_id: str,
        execution_id: str,
    ) -> ConversationExecutionReliabilityProjection:
        with self.product_lock:
            plan = self.store.plan(execution_id)
            self._require_route_identity(plan, project_id, chat_id)
            projection = self._reconcile_locked(plan)
            return self._reliability_projection_locked(plan, projection)

    def stop_execution(
        self,
        project_id: str,
        chat_id: str,
        execution_id: str,
    ) -> ConversationExecutionReliabilityProjection:
        with self.product_lock:
            plan = self.store.plan(execution_id)
            self._require_route_identity(plan, project_id, chat_id)
            projection = self._reconcile_locked(plan)
            if projection.terminal:
                return self._reliability_projection_locked(plan, projection)
            if projection.session_id is None:
                raise RuntimeError("active conversation execution does not have a session binding")
            self.service.cancel(projection.session_id)
            projection = self._reconcile_locked(plan)
            return self._reliability_projection_locked(plan, projection)

    def retry_execution(
        self,
        project_id: str,
        chat_id: str,
        source_execution_id: str,
        request: ConversationExecutionRetryRequest,
    ) -> ConversationExecutionProjection:
        with self.product_lock:
            source = self.store.plan(source_execution_id)
            self._require_route_identity(source, project_id, chat_id)
            source_projection = self._reconcile_locked(source)
            if not source_projection.terminal or source_projection.status not in {"failed", "cancelled"}:
                raise ValueError("only failed or cancelled terminal executions can be retried")
            if not self._is_m74_plan(source):
                raise ValueError("retry requires an M74-or-later execution with frozen inputs")

            existing = self.store.plan_for_submission(request.submission_id)
            if existing is not None:
                record = self.retry_store.record(existing.execution_id)
                if (
                    record is None
                    or record.source_execution_id != source_execution_id
                    or existing.project_id != project_id
                    or existing.chat_id != chat_id
                    or existing.task != source.task
                ):
                    raise ValueError("retry submission ID is already bound to different work")
                self._require_retry_record_identity(existing, record)
                return self._reconcile_locked(existing)

            active = tuple(
                item
                for item in self.projections_for_chat(project_id, chat_id)
                if not item.terminal
            )
            if active:
                raise ValueError("chat already has an active conversation execution")

            project = self.product_store.project(project_id)
            chat = self.product_store.chat(chat_id)
            if project.archived or chat.archived:
                raise ValueError("cannot retry from an archived project/chat")
            workspace = Path(project.workspace_root)
            if not workspace.is_dir():
                raise ValueError("project workspace is not currently available")
            if Path(source.request.workspace_root).resolve() != workspace.resolve():
                raise RuntimeError("retry source workspace no longer matches project identity")

            source_settings = self.settings_execution_store.snapshot(source.execution_id)
            if source_settings is None:
                raise RuntimeError("retry source settings snapshot is missing")
            source_resources = self.resource_execution_store.snapshot(source.execution_id)
            if source_resources is None:
                raise RuntimeError("retry source resource snapshot is missing")

            execution_id = f"exec_{uuid.uuid4().hex}"
            created_at = _now()
            settings_snapshot = self.settings_execution_store.put(
                self._clone_settings_snapshot(source_settings, execution_id, created_at)
            )
            resource_snapshot = self.resource_execution_store.put(
                self._clone_resource_snapshot(
                    source_resources,
                    execution_id,
                    request.submission_id,
                    created_at,
                )
            )
            retry_record = self.retry_store.put(
                ConversationExecutionRetryRecord(
                    execution_id=execution_id,
                    source_execution_id=source_execution_id,
                    submission_id=request.submission_id,
                    project_id=project_id,
                    chat_id=chat_id,
                    created_at=created_at,
                )
            )
            plan = ConversationExecutionPlan(
                execution_id=execution_id,
                submission_id=request.submission_id,
                project_id=project_id,
                chat_id=chat_id,
                task=source.task,
                reserved_user_sequence=chat.message_count + 1,
                request=source.request,
                output_root=str(
                    (self.service.run_root / f"{_M75_OUTPUT_PREFIX}{execution_id}").resolve()
                ),
                created_at=created_at,
            )
            plan = self.store.append_plan(plan)
            self._require_retry_record_identity(plan, retry_record)
            self._require_plan_identity(plan)
            if settings_snapshot.execution_id != plan.execution_id:
                raise RuntimeError("retry settings identity changed before plan acceptance")
            if resource_snapshot.execution_id != plan.execution_id:
                raise RuntimeError("retry resource identity changed before plan acceptance")
            self._ensure_approval_context(plan)
            self._ensure_context_locked(plan)
            return self._reconcile_locked(plan)

    def _reliability_projection_locked(
        self,
        plan: ConversationExecutionPlan,
        projection: ConversationExecutionProjection,
    ) -> ConversationExecutionReliabilityProjection:
        interrupted = False
        if projection.session_id is not None:
            snapshot = self.service.session(projection.session_id)
            interrupted = (
                projection.status == "failed"
                and snapshot.failure_reason == _RESTART_INTERRUPTION
            )
        retry_record = self.retry_store.record(plan.execution_id)
        retryable = (
            projection.terminal
            and projection.status in {"failed", "cancelled"}
            and self._is_m74_plan(plan)
        )
        return ConversationExecutionReliabilityProjection(
            execution_id=projection.execution_id,
            project_id=projection.project_id,
            chat_id=projection.chat_id,
            status=projection.status,
            terminal=projection.terminal,
            interrupted_by_restart=interrupted,
            can_stop=not projection.terminal and projection.session_id is not None,
            can_retry=retryable,
            can_continue=projection.terminal,
            retry_source_execution_id=(
                None if retry_record is None else retry_record.source_execution_id
            ),
        )

    @staticmethod
    def _clone_settings_snapshot(
        source: ProjectSettingsExecutionSnapshot,
        execution_id: str,
        created_at: datetime,
    ) -> ProjectSettingsExecutionSnapshot:
        payload = source.model_dump(mode="python", exclude={"fingerprint"})
        payload["execution_id"] = execution_id
        payload["created_at"] = created_at
        return ProjectSettingsExecutionSnapshot.model_validate(payload)

    @staticmethod
    def _clone_resource_snapshot(
        source: ConversationExecutionResourceSnapshot,
        execution_id: str,
        submission_id: str,
        created_at: datetime,
    ) -> ConversationExecutionResourceSnapshot:
        rendered = _render_resource_context(
            execution_id=execution_id,
            project_id=source.project_id,
            items=source.items,
        )
        return ConversationExecutionResourceSnapshot(
            execution_id=execution_id,
            submission_id=submission_id,
            project_id=source.project_id,
            chat_id=source.chat_id,
            items=source.items,
            rendered_context=rendered,
            created_at=created_at,
        )

    def _require_retry_record_identity(
        self,
        plan: ConversationExecutionPlan,
        record: ConversationExecutionRetryRecord,
    ) -> None:
        if (
            record.execution_id != plan.execution_id
            or record.submission_id != plan.submission_id
            or record.project_id != plan.project_id
            or record.chat_id != plan.chat_id
        ):
            raise RuntimeError("M75 retry record does not match execution plan identity")
        source = self.store.plan(record.source_execution_id)
        if source.project_id != plan.project_id or source.chat_id != plan.chat_id:
            raise RuntimeError("M75 retry source belongs to another project/chat")
        if source.task != plan.task:
            raise RuntimeError("M75 retry changed the accepted source task")

    @staticmethod
    def _require_route_identity(
        plan: ConversationExecutionPlan,
        project_id: str,
        chat_id: str,
    ) -> None:
        if plan.project_id != project_id or plan.chat_id != chat_id:
            raise ValueError("conversation execution belongs to another project/chat")

    @staticmethod
    def _is_m75_plan(plan: ConversationExecutionPlan) -> bool:
        return Path(plan.output_root).name == f"{_M75_OUTPUT_PREFIX}{plan.execution_id}"

    @staticmethod
    def _is_m74_plan(plan: ConversationExecutionPlan) -> bool:
        return (
            ProjectResourceConversationExecutionCoordinator._is_m74_plan(plan)
            or ReliableProjectResourceConversationExecutionCoordinator._is_m75_plan(plan)
        )

    @staticmethod
    def _is_m73_plan(plan: ConversationExecutionPlan) -> bool:
        return (
            ProjectResourceConversationExecutionCoordinator._is_m73_plan(plan)
            or ReliableProjectResourceConversationExecutionCoordinator._is_m75_plan(plan)
        )

    @staticmethod
    def _is_m72_plan(plan: ConversationExecutionPlan) -> bool:
        return (
            ProjectResourceConversationExecutionCoordinator._is_m72_plan(plan)
            or ReliableProjectResourceConversationExecutionCoordinator._is_m75_plan(plan)
        )

    @staticmethod
    def _is_m71_plan(plan: ConversationExecutionPlan) -> bool:
        return (
            ProjectResourceConversationExecutionCoordinator._is_m71_plan(plan)
            or ReliableProjectResourceConversationExecutionCoordinator._is_m75_plan(plan)
        )


__all__ = [
    "ConversationExecutionReliabilityProjection",
    "ConversationExecutionRetryRecord",
    "ConversationExecutionRetryRequest",
    "ConversationExecutionRetryStore",
    "ConversationExecutionStopRequest",
    "ReliableProjectResourceConversationExecutionCoordinator",
]
