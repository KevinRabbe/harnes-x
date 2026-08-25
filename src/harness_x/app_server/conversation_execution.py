"""Durable M69 bridge from one Project/Chat turn to one existing App Server session.

The bridge does not replace either durable store. It commits an immutable plan before crossing
into chat/session state, then records explicit bindings after each already-durable operation.
Recovery uses deterministic anchors (reserved chat sequence, unique output root, deterministic
terminal result text) to close append-before-bind crash windows without duplicate work.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from harness_x.product import ChatMessageRole, ProjectChatStore

from .protocol import AppSessionSnapshot, AppSessionStatus, CodingSessionRequest

if TYPE_CHECKING:
    from .service import AppServerService

_M69_VERIFICATION_POLICY = "m69-git-diff-check-v1"
_M69_VERIFICATION_COMMAND = "git diff --check"
_M69_DEFAULT_MODEL_PROFILE = "main"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConversationExecutionBindingKind(StrEnum):
    USER_MESSAGE = "user_message"
    SESSION = "session"
    RESULT_MESSAGE = "result_message"


class ConversationExecutionPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["conversation-execution-plan-v1"] = "conversation-execution-plan-v1"
    execution_id: str = Field(pattern=r"^exec_[0-9a-f]{32}$")
    submission_id: str = Field(pattern=r"^submission_[0-9a-f]{32}$")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    task: str = Field(min_length=1, max_length=120_000)
    reserved_user_sequence: int = Field(ge=1)
    request: CodingSessionRequest
    verification_policy_id: Literal["m69-git-diff-check-v1"] = _M69_VERIFICATION_POLICY
    output_root: str = Field(min_length=1, max_length=4000)
    created_at: datetime
    fingerprint: str = ""

    @field_validator("task")
    @classmethod
    def require_nonblank_task(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("conversation execution task cannot be blank")
        return value

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ConversationExecutionPlan":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", _sha256(_canonical(material)))
        return self


class ConversationExecutionBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["conversation-execution-binding-v1"] = (
        "conversation-execution-binding-v1"
    )
    execution_id: str = Field(pattern=r"^exec_[0-9a-f]{32}$")
    kind: ConversationExecutionBindingKind
    record_id: str = Field(min_length=1, max_length=120)
    created_at: datetime


class ConversationExecutionProjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["conversation-execution-projection-v1"] = (
        "conversation-execution-projection-v1"
    )
    execution_id: str
    submission_id: str
    project_id: str
    chat_id: str
    user_message_id: str | None = None
    session_id: str | None = None
    result_message_id: str | None = None
    status: str
    terminal: bool
    model_profile: str
    verification_policy_id: str
    created_at: datetime


class ConversationExecutionSubmitRequest(BaseModel):
    """Authenticated UI request. The browser can author only the operator's text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["conversation-execution-submit-v1"] = (
        "conversation-execution-submit-v1"
    )
    submission_id: str = Field(pattern=r"^submission_[0-9a-f]{32}$")
    role: Literal["user"] = "user"
    content: dict[str, str]

    @model_validator(mode="after")
    def validate_content(self) -> "ConversationExecutionSubmitRequest":
        if set(self.content) != {"type", "text"}:
            raise ValueError("conversation execution content must contain type and text")
        if self.content["type"] != "text":
            raise ValueError("conversation execution content type must be text")
        text = self.content["text"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("conversation execution text cannot be blank")
        if len(text) > 120_000:
            raise ValueError("conversation execution text exceeds 120000 characters")
        return self

    @property
    def text(self) -> str:
        return self.content["text"]


class ConversationExecutionStore:
    """Append-only immutable plans plus append-only record bindings."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.plans_path = self.root / "conversation-execution-plans.jsonl"
        self.bindings_path = self.root / "conversation-execution-bindings.jsonl"
        self._plans: dict[str, ConversationExecutionPlan] = {}
        self._submission_ids: dict[str, str] = {}
        self._bindings: dict[tuple[str, ConversationExecutionBindingKind], ConversationExecutionBinding] = {}
        self._load()

    @property
    def plans(self) -> tuple[ConversationExecutionPlan, ...]:
        return tuple(sorted(self._plans.values(), key=lambda item: (item.created_at, item.execution_id)))

    def plan(self, execution_id: str) -> ConversationExecutionPlan:
        try:
            return self._plans[execution_id]
        except KeyError as exc:
            raise KeyError(f"unknown conversation execution {execution_id}") from exc

    def plan_for_submission(self, submission_id: str) -> ConversationExecutionPlan | None:
        execution_id = self._submission_ids.get(submission_id)
        return None if execution_id is None else self.plan(execution_id)

    def plans_for_chat(self, project_id: str, chat_id: str) -> tuple[ConversationExecutionPlan, ...]:
        return tuple(
            item
            for item in self.plans
            if item.project_id == project_id and item.chat_id == chat_id
        )

    def append_plan(self, plan: ConversationExecutionPlan) -> ConversationExecutionPlan:
        existing = self.plan_for_submission(plan.submission_id)
        if existing is not None:
            if (
                existing.project_id != plan.project_id
                or existing.chat_id != plan.chat_id
                or existing.task != plan.task
            ):
                raise ValueError("submission ID is already bound to different conversation work")
            return existing
        if plan.execution_id in self._plans:
            raise ValueError("conversation execution ID is already registered")
        self._append(self.plans_path, _canonical(plan.model_dump(mode="json")))
        self._plans[plan.execution_id] = plan
        self._submission_ids[plan.submission_id] = plan.execution_id
        return plan

    def binding(
        self,
        execution_id: str,
        kind: ConversationExecutionBindingKind,
    ) -> ConversationExecutionBinding | None:
        self.plan(execution_id)
        return self._bindings.get((execution_id, kind))

    def bind(
        self,
        execution_id: str,
        kind: ConversationExecutionBindingKind,
        record_id: str,
    ) -> ConversationExecutionBinding:
        self.plan(execution_id)
        key = (execution_id, kind)
        existing = self._bindings.get(key)
        if existing is not None:
            if existing.record_id != record_id:
                raise RuntimeError(
                    f"conversation execution {execution_id} has conflicting {kind.value} binding"
                )
            return existing
        binding = ConversationExecutionBinding(
            execution_id=execution_id,
            kind=kind,
            record_id=record_id,
            created_at=_now(),
        )
        self._append(self.bindings_path, _canonical(binding.model_dump(mode="json")))
        self._bindings[key] = binding
        return binding

    def _load(self) -> None:
        if self.plans_path.exists():
            for line_number, raw_line in enumerate(
                self.plans_path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not raw_line.strip():
                    raise ValueError(f"blank conversation execution plan row {line_number}")
                raw = json.loads(raw_line)
                stored_fingerprint = str(raw.get("fingerprint", ""))
                plan = ConversationExecutionPlan.model_validate(raw)
                if stored_fingerprint != plan.fingerprint:
                    raise ValueError(
                        f"conversation execution plan fingerprint mismatch: {plan.execution_id}"
                    )
                if plan.execution_id in self._plans:
                    raise ValueError("duplicate conversation execution ID")
                if plan.submission_id in self._submission_ids:
                    raise ValueError("duplicate conversation execution submission ID")
                self._plans[plan.execution_id] = plan
                self._submission_ids[plan.submission_id] = plan.execution_id
        if self.bindings_path.exists():
            for line_number, raw_line in enumerate(
                self.bindings_path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not raw_line.strip():
                    raise ValueError(f"blank conversation execution binding row {line_number}")
                binding = ConversationExecutionBinding.model_validate_json(raw_line)
                if binding.execution_id not in self._plans:
                    raise ValueError("conversation execution binding references unknown plan")
                key = (binding.execution_id, binding.kind)
                existing = self._bindings.get(key)
                if existing is not None:
                    if existing.record_id != binding.record_id:
                        raise ValueError("conflicting conversation execution binding rows")
                    raise ValueError("duplicate conversation execution binding row")
                self._bindings[key] = binding

    @staticmethod
    def _append(path: Path, payload: bytes) -> None:
        with path.open("ab") as handle:
            handle.write(payload + b"\n")
            handle.flush()
            os.fsync(handle.fileno())


class ConversationExecutionCoordinator:
    """Reconcile M69 plans against the existing ProjectChatStore and AppSessionStore."""

    def __init__(
        self,
        service: AppServerService,
        product_store: ProjectChatStore,
        product_lock: threading.RLock,
        root: str | Path,
    ) -> None:
        self.service = service
        self.product_store = product_store
        self.product_lock = product_lock
        self.store = ConversationExecutionStore(root)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> threading.Thread:
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._thread = threading.Thread(
            target=self._loop,
            name="harness-x-conversation-execution-reconciler",
            daemon=True,
        )
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def submit(
        self,
        *,
        project_id: str,
        chat_id: str,
        request: ConversationExecutionSubmitRequest,
    ) -> ConversationExecutionProjection:
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

            model_profile = project.default_model_profile or _M69_DEFAULT_MODEL_PROFILE
            coding_request = CodingSessionRequest(
                workspace_root=workspace,
                task=request.text,
                model_profile=model_profile,
                verification_commands=(_M69_VERIFICATION_COMMAND,),
            )
            execution_id = f"exec_{uuid.uuid4().hex}"
            plan = ConversationExecutionPlan(
                execution_id=execution_id,
                submission_id=request.submission_id,
                project_id=project_id,
                chat_id=chat_id,
                task=request.text,
                reserved_user_sequence=chat.message_count + 1,
                request=coding_request,
                output_root=str((self.service.run_root / f"conversation_{execution_id}").resolve()),
                created_at=_now(),
            )
            plan = self.store.append_plan(plan)
            return self._reconcile_locked(plan)

    def projection(self, execution_id: str) -> ConversationExecutionProjection:
        with self.product_lock:
            return self._reconcile_locked(self.store.plan(execution_id))

    def projections_for_chat(
        self,
        project_id: str,
        chat_id: str,
    ) -> tuple[ConversationExecutionProjection, ...]:
        with self.product_lock:
            self._require_owned_chat(project_id, chat_id)
            return tuple(
                self._reconcile_locked(plan)
                for plan in self.store.plans_for_chat(project_id, chat_id)
            )

    def reconcile_all(self) -> None:
        with self.product_lock:
            for plan in self.store.plans:
                self._reconcile_locked(plan)

    def assert_project_archivable(self, project_id: str) -> None:
        with self.product_lock:
            self.product_store.project(project_id)
            for plan in self.store.plans:
                if plan.project_id == project_id and self._is_active_locked(plan):
                    raise ValueError("cannot archive a project with active conversation execution")

    def assert_chat_archivable(self, project_id: str, chat_id: str) -> None:
        with self.product_lock:
            self._require_owned_chat(project_id, chat_id)
            for plan in self.store.plans_for_chat(project_id, chat_id):
                if self._is_active_locked(plan):
                    raise ValueError("cannot archive a chat with active conversation execution")

    def _is_active_locked(self, plan: ConversationExecutionPlan) -> bool:
        session_binding = self.store.binding(plan.execution_id, ConversationExecutionBindingKind.SESSION)
        if session_binding is None:
            return True
        try:
            return not self.service.store.session(session_binding.record_id).status.terminal
        except KeyError:
            raise RuntimeError("conversation execution session binding points to missing session")

    def _reconcile_locked(self, plan: ConversationExecutionPlan) -> ConversationExecutionProjection:
        self._require_plan_identity(plan)
        user_message_id = self._ensure_user_message_locked(plan)
        snapshot = self._ensure_session_locked(plan)
        result_message_id = self._ensure_terminal_result_locked(plan, snapshot)
        return self._projection(plan, user_message_id, snapshot, result_message_id)

    def _require_plan_identity(self, plan: ConversationExecutionPlan) -> None:
        project = self.product_store.project(plan.project_id)
        chat = self.product_store.chat(plan.chat_id)
        if chat.project_id != project.project_id:
            raise RuntimeError("conversation execution plan project/chat identity mismatch")
        if Path(plan.request.workspace_root).resolve() != Path(project.workspace_root).resolve():
            raise RuntimeError("conversation execution plan workspace no longer matches project identity")
        if plan.request.task != plan.task:
            raise RuntimeError("conversation execution plan task/request mismatch")

    def _require_owned_chat(self, project_id: str, chat_id: str) -> None:
        self.product_store.project(project_id)
        chat = self.product_store.chat(chat_id)
        if chat.project_id != project_id:
            raise ValueError("chat belongs to another project")

    def _ensure_user_message_locked(self, plan: ConversationExecutionPlan) -> str:
        binding = self.store.binding(plan.execution_id, ConversationExecutionBindingKind.USER_MESSAGE)
        messages = self.product_store.messages(plan.chat_id)
        if binding is not None:
            message = next((item for item in messages if item.message_id == binding.record_id), None)
            if message is None:
                raise RuntimeError("conversation user-message binding points to missing message")
            self._verify_user_message(plan, message)
            return message.message_id

        sequence = plan.reserved_user_sequence
        if len(messages) >= sequence:
            message = messages[sequence - 1]
            self._verify_user_message(plan, message)
        elif len(messages) == sequence - 1:
            message = self.product_store.append_text_message(
                plan.chat_id,
                role=ChatMessageRole.USER,
                text=plan.task,
            )
            self._verify_user_message(plan, message)
        else:
            raise RuntimeError("conversation execution reserved user sequence is ahead of chat ledger")
        self.store.bind(
            plan.execution_id,
            ConversationExecutionBindingKind.USER_MESSAGE,
            message.message_id,
        )
        return message.message_id

    @staticmethod
    def _verify_user_message(plan: ConversationExecutionPlan, message) -> None:
        if (
            message.project_id != plan.project_id
            or message.chat_id != plan.chat_id
            or message.sequence != plan.reserved_user_sequence
            or message.role != ChatMessageRole.USER
            or getattr(message.content, "type", None) != "text"
            or getattr(message.content, "text", None) != plan.task
        ):
            raise RuntimeError("conversation execution reserved user message does not match plan")

    def _ensure_session_locked(self, plan: ConversationExecutionPlan) -> AppSessionSnapshot:
        binding = self.store.binding(plan.execution_id, ConversationExecutionBindingKind.SESSION)
        if binding is not None:
            snapshot = self.service.store.session(binding.record_id)
            self._verify_session(plan, snapshot)
            self._ensure_created_session_queued(snapshot)
            return snapshot

        matches = tuple(
            item
            for item in self.service.store.sessions
            if str(Path(item.output_root).resolve()) == str(Path(plan.output_root).resolve())
        )
        if len(matches) > 1:
            raise RuntimeError("multiple App Sessions use one conversation execution output root")
        if matches:
            snapshot = matches[0]
            self._verify_session(plan, snapshot)
        else:
            snapshot = self.service.store.create_session(
                plan.request,
                output_root=plan.output_root,
            )
            self._verify_session(plan, snapshot)
        self.store.bind(
            plan.execution_id,
            ConversationExecutionBindingKind.SESSION,
            snapshot.session_id,
        )
        self._ensure_created_session_queued(snapshot)
        return snapshot

    @staticmethod
    def _verify_session(plan: ConversationExecutionPlan, snapshot: AppSessionSnapshot) -> None:
        if snapshot.request != plan.request:
            raise RuntimeError("conversation execution App Session request mismatch")
        if str(Path(snapshot.output_root).resolve()) != str(Path(plan.output_root).resolve()):
            raise RuntimeError("conversation execution App Session output-root mismatch")

    def _ensure_created_session_queued(self, snapshot: AppSessionSnapshot) -> None:
        if snapshot.status != AppSessionStatus.CREATED:
            return
        with self.service._condition:
            if self.service._stopping:
                return
            if (
                snapshot.session_id != self.service._active_session_id
                and snapshot.session_id not in self.service._queue
            ):
                self.service._queue.append(snapshot.session_id)
                self.service._condition.notify_all()

    def _ensure_terminal_result_locked(
        self,
        plan: ConversationExecutionPlan,
        snapshot: AppSessionSnapshot,
    ) -> str | None:
        if not snapshot.status.terminal:
            return None
        text = self._terminal_text(plan, snapshot)
        binding = self.store.binding(plan.execution_id, ConversationExecutionBindingKind.RESULT_MESSAGE)
        messages = self.product_store.messages(plan.chat_id)
        if binding is not None:
            message = next((item for item in messages if item.message_id == binding.record_id), None)
            if message is None:
                raise RuntimeError("conversation result-message binding points to missing message")
            self._verify_result_message(plan, message, text)
            return message.message_id

        matches = tuple(
            item
            for item in messages
            if item.role == ChatMessageRole.ASSISTANT
            and getattr(item.content, "type", None) == "text"
            and getattr(item.content, "text", None) == text
        )
        if len(matches) > 1:
            raise RuntimeError("conversation execution has duplicate deterministic result messages")
        if matches:
            message = matches[0]
        else:
            message = self.product_store.append_text_message(
                plan.chat_id,
                role=ChatMessageRole.ASSISTANT,
                text=text,
            )
        self._verify_result_message(plan, message, text)
        self.store.bind(
            plan.execution_id,
            ConversationExecutionBindingKind.RESULT_MESSAGE,
            message.message_id,
        )
        return message.message_id

    @staticmethod
    def _verify_result_message(plan: ConversationExecutionPlan, message, text: str) -> None:
        if (
            message.project_id != plan.project_id
            or message.chat_id != plan.chat_id
            or message.role != ChatMessageRole.ASSISTANT
            or getattr(message.content, "type", None) != "text"
            or getattr(message.content, "text", None) != text
        ):
            raise RuntimeError("conversation execution result message does not match terminal projection")

    @staticmethod
    def _terminal_text(plan: ConversationExecutionPlan, snapshot: AppSessionSnapshot) -> str:
        header = {
            AppSessionStatus.SUCCEEDED: "Harness X completed this work successfully.",
            AppSessionStatus.FAILED: "Harness X could not complete this work.",
            AppSessionStatus.CANCELLED: "Harness X execution was cancelled.",
        }[snapshot.status]
        lines = [
            header,
            "",
            f"Execution link: {plan.execution_id}",
            f"Execution session: {snapshot.session_id}",
            f"Status: {snapshot.status.value}",
        ]
        if snapshot.status == AppSessionStatus.FAILED and snapshot.failure_reason:
            lines.append(f"Reason: {snapshot.failure_reason}")
        lines.extend(
            [
                "",
                "Open Advanced to inspect the durable coding report, causal trace, lifecycle ledger, and evidence exports.",
            ]
        )
        return "\n".join(lines)

    def _projection(
        self,
        plan: ConversationExecutionPlan,
        user_message_id: str | None,
        snapshot: AppSessionSnapshot | None,
        result_message_id: str | None,
    ) -> ConversationExecutionProjection:
        return ConversationExecutionProjection(
            execution_id=plan.execution_id,
            submission_id=plan.submission_id,
            project_id=plan.project_id,
            chat_id=plan.chat_id,
            user_message_id=user_message_id,
            session_id=None if snapshot is None else snapshot.session_id,
            result_message_id=result_message_id,
            status="planned" if snapshot is None else snapshot.status.value,
            terminal=False if snapshot is None else snapshot.status.terminal,
            model_profile=plan.request.model_profile,
            verification_policy_id=plan.verification_policy_id,
            created_at=plan.created_at,
        )

    def _loop(self) -> None:
        while not self._stop.wait(0.35):
            if self.service._stopping:
                return
            try:
                self.reconcile_all()
            except (OSError, RuntimeError, ValueError, KeyError):
                # Exact diagnostics remain available through subsequent projection calls/tests.
                # A product reconciliation issue must never rewrite the underlying App Session.
                time.sleep(0.35)
