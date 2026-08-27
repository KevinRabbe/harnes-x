"""Crash-safe completion of an M75 retry intent prepared before its plan append."""

from __future__ import annotations

from pathlib import Path

from .conversation_execution import ConversationExecutionPlan, ConversationExecutionProjection
from .reliability_execution import (
    _M75_OUTPUT_PREFIX,
    ConversationExecutionRetryRecord,
    ConversationExecutionRetryRequest,
    ReliableProjectResourceConversationExecutionCoordinator,
)


class RecoverableReliableProjectResourceConversationExecutionCoordinator(
    ReliableProjectResourceConversationExecutionCoordinator
):
    """Resume the one durable retry-record-before-plan crash window idempotently.

    M75 deliberately writes cloned immutable settings/resources and retry provenance before the
    new execution plan. If the process dies after retry provenance is durable but before the plan
    append, the same retry submission must reconstruct that exact prepared execution identity
    rather than allocate a second execution or conflict with its own durable intent.
    """

    def retry_execution(
        self,
        project_id: str,
        chat_id: str,
        source_execution_id: str,
        request: ConversationExecutionRetryRequest,
    ) -> ConversationExecutionProjection:
        with self.product_lock:
            prepared = self.retry_store.record_for_submission(request.submission_id)
            existing = self.store.plan_for_submission(request.submission_id)
            if prepared is None or existing is not None:
                return super().retry_execution(
                    project_id,
                    chat_id,
                    source_execution_id,
                    request,
                )
            return self._resume_prepared_retry_locked(
                project_id,
                chat_id,
                source_execution_id,
                prepared,
            )

    def _resume_prepared_retry_locked(
        self,
        project_id: str,
        chat_id: str,
        source_execution_id: str,
        prepared: ConversationExecutionRetryRecord,
    ) -> ConversationExecutionProjection:
        source = self.store.plan(source_execution_id)
        self._require_route_identity(source, project_id, chat_id)
        source_projection = self._reconcile_locked(source)
        if not source_projection.terminal or source_projection.status not in {"failed", "cancelled"}:
            raise ValueError("only failed or cancelled terminal executions can be retried")
        if not self._is_m74_plan(source):
            raise ValueError("retry requires an M74-or-later execution with frozen inputs")
        if (
            prepared.source_execution_id != source_execution_id
            or prepared.project_id != project_id
            or prepared.chat_id != chat_id
        ):
            raise ValueError("retry submission ID is already bound to different work")

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

        settings_snapshot = self.settings_execution_store.snapshot(prepared.execution_id)
        expected_settings = self._clone_settings_snapshot(
            source_settings,
            prepared.execution_id,
            prepared.created_at,
        )
        if settings_snapshot is None or settings_snapshot.fingerprint != expected_settings.fingerprint:
            raise RuntimeError("prepared retry settings snapshot is missing or inconsistent")

        resource_snapshot = self.resource_execution_store.snapshot(prepared.execution_id)
        expected_resources = self._clone_resource_snapshot(
            source_resources,
            prepared.execution_id,
            prepared.submission_id,
            prepared.created_at,
        )
        if resource_snapshot is None or resource_snapshot.fingerprint != expected_resources.fingerprint:
            raise RuntimeError("prepared retry resource snapshot is missing or inconsistent")

        plan = ConversationExecutionPlan(
            execution_id=prepared.execution_id,
            submission_id=prepared.submission_id,
            project_id=project_id,
            chat_id=chat_id,
            task=source.task,
            reserved_user_sequence=chat.message_count + 1,
            request=source.request,
            output_root=str(
                (self.service.run_root / f"{_M75_OUTPUT_PREFIX}{prepared.execution_id}").resolve()
            ),
            created_at=prepared.created_at,
        )
        plan = self.store.append_plan(plan)
        self._require_retry_record_identity(plan, prepared)
        self._require_plan_identity(plan)
        self._ensure_approval_context(plan)
        self._ensure_context_locked(plan)
        return self._reconcile_locked(plan)


__all__ = ["RecoverableReliableProjectResourceConversationExecutionCoordinator"]
