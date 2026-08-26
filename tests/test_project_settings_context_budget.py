from __future__ import annotations

import threading
import time
from pathlib import Path

from pydantic import BaseModel

from harness_x.app_server.conversation_execution import ConversationExecutionSubmitRequest
from harness_x.app_server.project_settings_execution import (
    ProjectSettingsConversationExecutionCoordinator,
    render_project_instructions,
)
from harness_x.app_server.sensitive_approval import SensitiveActionApprovalBroker
from harness_x.app_server.service import AppServerService
from harness_x.product import (
    ChatMessageRole,
    ProjectAutonomyProfile,
    ProjectChatStore,
    ProjectSettingsStore,
    ProjectVerificationStrategy,
)


class _Report(BaseModel):
    succeeded: bool = True


class _CaptureRunner:
    def __init__(self, broker: SensitiveActionApprovalBroker) -> None:
        self.approval_broker = broker
        self.requests = []

    def __call__(self, snapshot) -> _Report:
        self.requests.append(snapshot.request)
        output = Path(snapshot.output_root)
        output.mkdir(parents=True, exist_ok=True)
        report = _Report()
        (output / "coding-task-report.json").write_text(
            report.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        return report


def _request(text: str) -> ConversationExecutionSubmitRequest:
    return ConversationExecutionSubmitRequest.model_validate(
        {
            "schema_version": "conversation-execution-submit-v1",
            "submission_id": "submission_" + "b" * 32,
            "role": "user",
            "content": {"type": "text", "text": text},
        }
    )


def _wait_terminal(coordinator, execution_id: str, *, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        projection = coordinator.projection(execution_id)
        if projection.terminal:
            return projection
        time.sleep(0.02)
    raise AssertionError(f"conversation execution did not finish: {execution_id}")


def test_project_instructions_reserve_context_budget_before_session_creation_and_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "server"
    broker = SensitiveActionApprovalBroker(root / "data" / "sensitive-approvals")
    runner = _CaptureRunner(broker)
    service = AppServerService(root / "data", runner=runner)
    product = ProjectChatStore(service.root / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(name="Budget project", workspace_root=workspace)
    chat = product.create_chat(project.project_id, title="Long history")

    for index in range(30):
        product.append_text_message(
            chat.chat_id,
            role=ChatMessageRole.USER if index % 2 == 0 else ChatMessageRole.ASSISTANT,
            text=f"history-{index:02d}-" + ("x" * 900),
        )

    ProjectSettingsStore(product).replace(
        project.project_id,
        model_profile="main",
        verification_strategy=ProjectVerificationStrategy.DIFF_CHECK,
        project_instructions="I" * 6000,
        autonomy_profile=ProjectAutonomyProfile.STANDARD,
    )
    coordinator = ProjectSettingsConversationExecutionCoordinator(
        service,
        product,
        threading.RLock(),
        service.root / "conversation-executions",
    )
    try:
        projection = coordinator.submit(
            project_id=project.project_id,
            chat_id=chat.chat_id,
            request=_request("Apply the bounded project guidance."),
        )
        plan = coordinator.store.plan(projection.execution_id)
        snapshot = coordinator.settings_execution_store.snapshot(projection.execution_id)
        assert snapshot is not None
        rendered_instructions = render_project_instructions(snapshot)

        context = coordinator.context(projection.execution_id)
        assert context.max_rendered_chars == 20_000 - len(rendered_instructions)
        assert context.max_rendered_bytes == 80_000 - len(rendered_instructions.encode("utf-8"))
        assert context.rendered_chars <= context.max_rendered_chars
        assert context.rendered_bytes <= context.max_rendered_bytes
        assert 0 < context.selected_prior_count < 24
        assert context.omitted_prior_count > 0

        effective = coordinator._effective_request(plan)
        assert effective.task.endswith(rendered_instructions)
        assert len(effective.task) <= 20_000
        assert len(effective.task.encode("utf-8")) <= 80_000

        terminal = _wait_terminal(coordinator, projection.execution_id)
        session = service.session(terminal.session_id)
        assert session.request == effective
        assert runner.requests and runner.requests[-1] == effective

        restarted = ProjectSettingsConversationExecutionCoordinator(
            service,
            product,
            threading.RLock(),
            service.root / "conversation-executions",
        )
        restarted_context = restarted.context(projection.execution_id)
        assert restarted_context.fingerprint == context.fingerprint
        assert restarted_context.max_rendered_chars == context.max_rendered_chars
        assert restarted._effective_request(plan) == effective
    finally:
        service.close()
