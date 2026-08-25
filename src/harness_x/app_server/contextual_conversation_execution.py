"""M71 conversation-execution coordinator with frozen durable chat context."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from .conversation_context import (
    ConversationContextPackage,
    ConversationContextStore,
    build_conversation_context,
    render_conversation_context,
)
from .conversation_execution import (
    ConversationExecutionBindingKind,
    ConversationExecutionCoordinator,
    ConversationExecutionPlan,
    ConversationExecutionProjection,
    ConversationExecutionSubmitRequest,
)
from .protocol import AppSessionSnapshot, CodingSessionRequest

_M71_OUTPUT_PREFIX = "conversation_context_"
_M69_DEFAULT_MODEL_PROFILE = "main"
_M69_VERIFICATION_COMMAND = "git diff --check"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ContextualConversationExecutionCoordinator(ConversationExecutionCoordinator):
    """Freeze deterministic product-chat context before crossing into App Session state."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.context_store = ConversationContextStore(self.store.root)
        plan_ids = {item.execution_id for item in self.store.plans}
        unknown = tuple(
            item.execution_id
            for item in self.context_store.contexts
            if item.execution_id not in plan_ids
        )
        if unknown:
            raise ValueError("conversation context references unknown execution plan")

        # Existing M69/M70 plans are historical accepted work. Persist a legacy passthrough
        # package for them rather than retroactively changing the task that their App Session saw.
        # M71 plans carry a distinct durable output-root marker, so a crash after plan append but
        # before context append remains reconstructable as M71 rather than being misclassified.
        for plan in self.store.plans:
            if self.context_store.context(plan.execution_id) is None:
                self._ensure_context_locked(plan)

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
                output_root=str(
                    (
                        self.service.run_root
                        / f"{_M71_OUTPUT_PREFIX}{execution_id}"
                    ).resolve()
                ),
                created_at=_now(),
            )
            plan = self.store.append_plan(plan)
            # Persist context immediately after the immutable plan and before user-message/session
            # writes. The output-root marker makes the one remaining append-before-context crash
            # window deterministically recoverable on restart.
            self._ensure_context_locked(plan)
            return self._reconcile_locked(plan)

    def context(self, execution_id: str) -> ConversationContextPackage:
        with self.product_lock:
            plan = self.store.plan(execution_id)
            self._require_plan_identity(plan)
            context = self.context_store.context(execution_id)
            if context is None:  # pragma: no cover - guarded by _require_plan_identity
                raise RuntimeError("conversation execution context package is missing")
            return context

    def _require_plan_identity(self, plan: ConversationExecutionPlan) -> None:
        super()._require_plan_identity(plan)
        self._ensure_context_locked(plan)

    def _ensure_context_locked(
        self,
        plan: ConversationExecutionPlan,
    ) -> ConversationContextPackage:
        messages = self.product_store.messages(plan.chat_id)
        prior_count = plan.reserved_user_sequence - 1
        if len(messages) < prior_count:
            raise RuntimeError("conversation context source prefix is shorter than execution plan")
        prior_messages = messages[:prior_count]
        legacy_passthrough = not self._is_m71_plan(plan)
        expected = build_conversation_context(
            execution_id=plan.execution_id,
            submission_id=plan.submission_id,
            project_id=plan.project_id,
            chat_id=plan.chat_id,
            reserved_user_sequence=plan.reserved_user_sequence,
            task=plan.task,
            prior_messages=prior_messages,
            legacy_passthrough=legacy_passthrough,
        )
        existing = self.context_store.context(plan.execution_id)
        if existing is None:
            return self.context_store.put(expected)
        if existing.selection_policy != expected.selection_policy:
            raise RuntimeError("conversation execution context selection policy mismatch")
        if existing.fingerprint != expected.fingerprint:
            raise RuntimeError("conversation execution durable context no longer matches chat prefix")
        return existing

    @staticmethod
    def _is_m71_plan(plan: ConversationExecutionPlan) -> bool:
        return Path(plan.output_root).name == f"{_M71_OUTPUT_PREFIX}{plan.execution_id}"

    def _effective_request(
        self,
        plan: ConversationExecutionPlan,
    ) -> CodingSessionRequest:
        context = self._ensure_context_locked(plan)
        rendered_task = render_conversation_context(context)
        if rendered_task == plan.request.task:
            return plan.request
        payload = plan.request.model_dump(mode="python")
        payload["task"] = rendered_task
        return CodingSessionRequest.model_validate(payload)

    def _ensure_session_locked(
        self,
        plan: ConversationExecutionPlan,
    ) -> AppSessionSnapshot:
        expected_request = self._effective_request(plan)
        binding = self.store.binding(
            plan.execution_id, ConversationExecutionBindingKind.SESSION
        )
        if binding is not None:
            snapshot = self.service.session(binding.record_id)
            self._verify_contextual_session(plan, snapshot, expected_request)
            return self.service.enqueue_created_session(snapshot.session_id)

        matches = tuple(
            item
            for item in self.service.sessions()
            if str(Path(item.output_root).resolve())
            == str(Path(plan.output_root).resolve())
        )
        if len(matches) > 1:
            raise RuntimeError(
                "multiple App Sessions use one conversation execution output root"
            )
        if matches:
            snapshot = matches[0]
            self._verify_contextual_session(plan, snapshot, expected_request)
            snapshot = self.service.enqueue_created_session(snapshot.session_id)
        else:
            snapshot = self.service.create_session_at_output_root(
                expected_request,
                output_root=plan.output_root,
            )
            self._verify_contextual_session(plan, snapshot, expected_request)
        self.store.bind(
            plan.execution_id,
            ConversationExecutionBindingKind.SESSION,
            snapshot.session_id,
        )
        return snapshot

    @staticmethod
    def _verify_contextual_session(
        plan: ConversationExecutionPlan,
        snapshot: AppSessionSnapshot,
        expected_request: CodingSessionRequest,
    ) -> None:
        if snapshot.request != expected_request:
            raise RuntimeError("conversation execution App Session contextual request mismatch")
        if str(Path(snapshot.output_root).resolve()) != str(Path(plan.output_root).resolve()):
            raise RuntimeError("conversation execution App Session output-root mismatch")
