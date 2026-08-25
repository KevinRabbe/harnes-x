"""M72 contextual conversation coordinator with pre-session approval identity."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from .contextual_conversation_execution import ContextualConversationExecutionCoordinator
from .conversation_execution import (
    ConversationExecutionPlan,
    ConversationExecutionProjection,
    ConversationExecutionSubmitRequest,
)
from .protocol import CodingSessionRequest
from .sensitive_approval import SensitiveActionApprovalBroker

_M72_OUTPUT_PREFIX = "conversation_approval_"
_M71_OUTPUT_PREFIX = "conversation_context_"
_M69_DEFAULT_MODEL_PROFILE = "main"
_M69_VERIFICATION_COMMAND = "git diff --check"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalContextualConversationExecutionCoordinator(
    ContextualConversationExecutionCoordinator
):
    """Persist execution approval identity before creating an M72 App Session."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        broker = getattr(self.service.runner, "approval_broker", None)
        self.approval_broker = (
            broker if isinstance(broker, SensitiveActionApprovalBroker) else None
        )
        # AppServerService has already recovered durable sessions, but M70 does not call
        # reconcile_all until this constructor returns. Re-establish every M72 output-root
        # binding now so a recovered CREATED session cannot run without its approval policy.
        if self.approval_broker is not None:
            for plan in self.store.plans:
                if self._is_m72_plan(plan):
                    self._ensure_approval_context(plan)

    def submit(
        self,
        *,
        project_id: str,
        chat_id: str,
        request: ConversationExecutionSubmitRequest,
    ) -> ConversationExecutionProjection:
        # Inherited tests/direct test servers may carry a plain runner. Those remain on the
        # frozen M71 contextual behavior; only an explicit approval-aware runner creates M72 plans.
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
                    (self.service.run_root / f"{_M72_OUTPUT_PREFIX}{execution_id}").resolve()
                ),
                created_at=_now(),
            )
            plan = self.store.append_plan(plan)
            # This durable mapping precedes both context/session writes. A crash after this point
            # is recoverable and cannot transform an M72 plan into an ungated run.
            self._ensure_approval_context(plan)
            self._ensure_context_locked(plan)
            return self._reconcile_locked(plan)

    def _require_plan_identity(self, plan: ConversationExecutionPlan) -> None:
        super()._require_plan_identity(plan)
        if self.approval_broker is not None and self._is_m72_plan(plan):
            self._ensure_approval_context(plan)

    def _ensure_approval_context(self, plan: ConversationExecutionPlan):
        if self.approval_broker is None:
            raise RuntimeError("M72 approval context requested without an approval broker")
        return self.approval_broker.register_execution(
            execution_id=plan.execution_id,
            project_id=plan.project_id,
            chat_id=plan.chat_id,
            output_root=plan.output_root,
            created_at=plan.created_at,
        )

    @staticmethod
    def _is_m72_plan(plan: ConversationExecutionPlan) -> bool:
        return Path(plan.output_root).name == f"{_M72_OUTPUT_PREFIX}{plan.execution_id}"

    @staticmethod
    def _is_m71_plan(plan: ConversationExecutionPlan) -> bool:
        name = Path(plan.output_root).name
        return name in {
            f"{_M71_OUTPUT_PREFIX}{plan.execution_id}",
            f"{_M72_OUTPUT_PREFIX}{plan.execution_id}",
        }
