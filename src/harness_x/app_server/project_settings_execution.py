"""M73 project settings compiler and immutable per-execution settings snapshot."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness_x.product import (
    ProjectAutonomyProfile,
    ProjectSettingsRecord,
    ProjectSettingsStore,
    ProjectVerificationStrategy,
)
from harness_x.reasoning import builtin_model_profiles

from .approval_contextual_conversation_execution import (
    ApprovalContextualConversationExecutionCoordinator,
)
from .conversation_execution import (
    ConversationExecutionPlan,
    ConversationExecutionProjection,
    ConversationExecutionSubmitRequest,
)
from .protocol import CodingSessionRequest

_M73_OUTPUT_PREFIX = "conversation_settings_"
_M72_OUTPUT_PREFIX = "conversation_approval_"
_M71_OUTPUT_PREFIX = "conversation_context_"


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


class ProjectSettingsExecutionSnapshot(BaseModel):
    """Immutable product-policy inputs captured before an M73 execution plan is accepted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["project-settings-execution-snapshot-v1"] = (
        "project-settings-execution-snapshot-v1"
    )
    execution_id: str = Field(pattern=r"^exec_[0-9a-f]{32}$")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    settings_revision: int = Field(ge=1)
    settings_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_profile: str = Field(min_length=1, max_length=64)
    verification_strategy: ProjectVerificationStrategy
    verification_commands: tuple[str, ...] = Field(min_length=1, max_length=4)
    project_instructions: str = Field(default="", max_length=6000)
    autonomy_profile: ProjectAutonomyProfile
    max_reasoning_steps: int = Field(ge=1, le=512)
    max_tool_actions: int = Field(ge=1, le=2048)
    preserves_sensitive_action_approval: Literal[True] = True
    created_at: datetime
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ProjectSettingsExecutionSnapshot":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", hashlib.sha256(_canonical(material)).hexdigest())
        return self


class ProjectSettingsExecutionStore:
    """Append-only snapshots; orphan snapshots are inert, missing snapshots for M73 plans fail closed."""

    def __init__(self, root: str | Path) -> None:
        self.path = Path(root).resolve() / "project-settings-execution-snapshots.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, ProjectSettingsExecutionSnapshot] = {}
        if self.path.exists():
            self._load()

    def snapshot(self, execution_id: str) -> ProjectSettingsExecutionSnapshot | None:
        return self._items.get(execution_id)

    def put(self, item: ProjectSettingsExecutionSnapshot) -> ProjectSettingsExecutionSnapshot:
        existing = self._items.get(item.execution_id)
        if existing is not None:
            if existing.fingerprint != item.fingerprint:
                raise RuntimeError("conversation execution settings snapshot identity conflict")
            return existing
        payload = _canonical(item.model_dump(mode="json")) + b"\n"
        with self.path.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self._items[item.execution_id] = item
        return item

    def _load(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"cannot load project settings execution snapshots: {exc}") from exc
        for number, line in enumerate(lines, start=1):
            try:
                raw = json.loads(line)
                stored_fingerprint = str(raw.get("fingerprint", ""))
                item = ProjectSettingsExecutionSnapshot.model_validate(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(
                    f"invalid project settings execution snapshot line {number}: {exc}"
                ) from exc
            if stored_fingerprint != item.fingerprint:
                raise ValueError("project settings execution snapshot fingerprint mismatch")
            if item.execution_id in self._items:
                raise ValueError("duplicate project settings execution snapshot ID")
            self._items[item.execution_id] = item


def compile_project_settings(
    execution_id: str,
    settings: ProjectSettingsRecord,
) -> ProjectSettingsExecutionSnapshot:
    """Compile product defaults only into existing bounded request inputs."""

    try:
        builtin_model_profiles().get(settings.model_profile)
    except KeyError as exc:
        raise ValueError(str(exc)) from exc

    verification = {
        ProjectVerificationStrategy.DIFF_CHECK: ("git diff --check",),
        ProjectVerificationStrategy.PYTEST: ("python -m pytest",),
        ProjectVerificationStrategy.PYTEST_AND_DIFF_CHECK: (
            "python -m pytest",
            "git diff --check",
        ),
    }[settings.verification_strategy]
    reasoning_steps, tool_actions = {
        ProjectAutonomyProfile.STANDARD: (32, 48),
        ProjectAutonomyProfile.CAUTIOUS: (16, 24),
    }[settings.autonomy_profile]
    return ProjectSettingsExecutionSnapshot(
        execution_id=execution_id,
        project_id=settings.project_id,
        settings_revision=settings.revision,
        settings_fingerprint=settings.fingerprint,
        model_profile=settings.model_profile,
        verification_strategy=settings.verification_strategy,
        verification_commands=verification,
        project_instructions=settings.project_instructions,
        autonomy_profile=settings.autonomy_profile,
        max_reasoning_steps=reasoning_steps,
        max_tool_actions=tool_actions,
        created_at=_now(),
    )


def render_project_instructions(snapshot: ProjectSettingsExecutionSnapshot) -> str:
    if not snapshot.project_instructions:
        return ""
    return (
        "\n\nHARNESS X PROJECT INSTRUCTIONS\n"
        "source: project-settings-v1\n"
        f"project_id: {snapshot.project_id}\n"
        f"settings_revision: {snapshot.settings_revision}\n"
        f"settings_fingerprint: {snapshot.settings_fingerprint}\n"
        "instructions:\n"
        f"{snapshot.project_instructions}\n"
        "END HARNESS X PROJECT INSTRUCTIONS"
    )


class ProjectSettingsConversationExecutionCoordinator(
    ApprovalContextualConversationExecutionCoordinator
):
    """M72 approval-aware coordinator with M73 settings frozen before plan acceptance."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.settings_store = ProjectSettingsStore(self.product_store)
        self.settings_execution_store = ProjectSettingsExecutionStore(self.store.root)
        for plan in self.store.plans:
            if self._is_m73_plan(plan) and self.settings_execution_store.snapshot(plan.execution_id) is None:
                raise RuntimeError("M73 conversation execution is missing its settings snapshot")

    def submit(
        self,
        *,
        project_id: str,
        chat_id: str,
        request: ConversationExecutionSubmitRequest,
    ) -> ConversationExecutionProjection:
        if self.approval_broker is None:
            return super().submit(project_id=project_id, chat_id=chat_id, request=request)

        with self.product_lock:
            existing = self.store.plan_for_submission(request.submission_id)
            if existing is not None:
                if (
                    existing.project_id != project_id
                    or existing.chat_id != chat_id
                    or existing.task != request.text
                ):
                    raise ValueError("submission ID is already bound to different conversation work")
                return self._reconcile_locked(existing)

            project = self.product_store.project(project_id)
            chat = self.product_store.chat(chat_id)
            if chat.project_id != project_id:
                raise ValueError("chat belongs to another project")
            if project.archived or chat.archived:
                raise ValueError("cannot execute from an archived project/chat")
            workspace = Path(project.workspace_root)
            if not workspace.is_dir():
                raise ValueError("project workspace is not currently available")

            execution_id = f"exec_{uuid.uuid4().hex}"
            settings = self.settings_store.settings(project_id)
            snapshot = self.settings_execution_store.put(
                compile_project_settings(execution_id, settings)
            )
            coding_request = CodingSessionRequest(
                workspace_root=workspace,
                task=request.text,
                model_profile=snapshot.model_profile,
                verification_commands=snapshot.verification_commands,
                max_reasoning_steps=snapshot.max_reasoning_steps,
                max_tool_actions=snapshot.max_tool_actions,
            )
            plan = ConversationExecutionPlan(
                execution_id=execution_id,
                submission_id=request.submission_id,
                project_id=project_id,
                chat_id=chat_id,
                task=request.text,
                reserved_user_sequence=chat.message_count + 1,
                request=coding_request,
                output_root=str(
                    (self.service.run_root / f"{_M73_OUTPUT_PREFIX}{execution_id}").resolve()
                ),
                created_at=snapshot.created_at,
            )
            plan = self.store.append_plan(plan)
            self._ensure_approval_context(plan)
            self._ensure_context_locked(plan)
            return self._reconcile_locked(plan)

    def _require_plan_identity(self, plan: ConversationExecutionPlan) -> None:
        super()._require_plan_identity(plan)
        if not self._is_m73_plan(plan):
            return
        snapshot = self.settings_execution_store.snapshot(plan.execution_id)
        if snapshot is None:
            raise RuntimeError("M73 conversation execution settings snapshot is missing")
        if snapshot.project_id != plan.project_id:
            raise RuntimeError("M73 conversation execution settings project identity mismatch")
        if (
            snapshot.model_profile != plan.request.model_profile
            or snapshot.verification_commands != plan.request.verification_commands
            or snapshot.max_reasoning_steps != plan.request.max_reasoning_steps
            or snapshot.max_tool_actions != plan.request.max_tool_actions
        ):
            raise RuntimeError("M73 conversation execution request no longer matches settings snapshot")

    def _effective_request(self, plan: ConversationExecutionPlan) -> CodingSessionRequest:
        request = super()._effective_request(plan)
        if not self._is_m73_plan(plan):
            return request
        snapshot = self.settings_execution_store.snapshot(plan.execution_id)
        if snapshot is None:
            raise RuntimeError("M73 conversation execution settings snapshot is missing")
        rendered = render_project_instructions(snapshot)
        if not rendered:
            return request
        payload = request.model_dump(mode="python")
        payload["task"] = request.task + rendered
        return CodingSessionRequest.model_validate(payload)

    @staticmethod
    def _is_m73_plan(plan: ConversationExecutionPlan) -> bool:
        return Path(plan.output_root).name == f"{_M73_OUTPUT_PREFIX}{plan.execution_id}"

    @staticmethod
    def _is_m72_plan(plan: ConversationExecutionPlan) -> bool:
        name = Path(plan.output_root).name
        return name in {
            f"{_M72_OUTPUT_PREFIX}{plan.execution_id}",
            f"{_M73_OUTPUT_PREFIX}{plan.execution_id}",
        }

    @staticmethod
    def _is_m71_plan(plan: ConversationExecutionPlan) -> bool:
        name = Path(plan.output_root).name
        return name in {
            f"{_M71_OUTPUT_PREFIX}{plan.execution_id}",
            f"{_M72_OUTPUT_PREFIX}{plan.execution_id}",
            f"{_M73_OUTPUT_PREFIX}{plan.execution_id}",
        }


__all__ = [
    "ProjectSettingsConversationExecutionCoordinator",
    "ProjectSettingsExecutionSnapshot",
    "ProjectSettingsExecutionStore",
    "compile_project_settings",
    "render_project_instructions",
]
