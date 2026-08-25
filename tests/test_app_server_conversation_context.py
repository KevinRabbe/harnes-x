from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from harness_x.app_server import cli as app_server_cli
from harness_x.app_server.contextual_conversation_execution import (
    ContextualConversationExecutionCoordinator,
)
from harness_x.app_server.conversation_context import build_conversation_context
from harness_x.app_server.conversation_execution import (
    ConversationExecutionCoordinator,
    ConversationExecutionSubmitRequest,
)
from harness_x.app_server.service import AppServerService
from harness_x.product import ChatMessageRole, ProjectChatStore


class _Report(BaseModel):
    succeeded: bool = True


class _CaptureRunner:
    def __init__(self) -> None:
        self.tasks: list[str] = []

    def __call__(self, snapshot) -> _Report:
        self.tasks.append(snapshot.request.task)
        output = Path(snapshot.output_root)
        output.mkdir(parents=True, exist_ok=True)
        report = _Report()
        (output / "coding-task-report.json").write_text(
            report.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        return report


def _request(submission_id: str, text: str) -> ConversationExecutionSubmitRequest:
    return ConversationExecutionSubmitRequest.model_validate(
        {
            "schema_version": "conversation-execution-submit-v1",
            "submission_id": submission_id,
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


def _domain(tmp_path: Path, *, runner=None):
    service = AppServerService(tmp_path / "data", runner=runner or _CaptureRunner())
    product = ProjectChatStore(service.root / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(name="Project", workspace_root=workspace)
    chat = product.create_chat(project.project_id, title="Chat")
    return service, product, project, chat


def test_context_builder_is_deterministic_excludes_system_and_keeps_current_exactly_once(
    tmp_path: Path,
) -> None:
    product = ProjectChatStore(tmp_path / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(name="Project", workspace_root=workspace)
    chat = product.create_chat(project.project_id, title="Chat")
    first = product.append_text_message(chat.chat_id, role=ChatMessageRole.USER, text="Inspect alpha")
    second = product.append_text_message(
        chat.chat_id,
        role=ChatMessageRole.ASSISTANT,
        text="Alpha inspection completed",
    )
    product.append_system_notice(
        chat.chat_id,
        code="internal.notice",
        text="must-not-enter-runtime-context",
    )

    kwargs = dict(
        execution_id="exec_" + "1" * 32,
        submission_id="submission_" + "2" * 32,
        project_id=project.project_id,
        chat_id=chat.chat_id,
        reserved_user_sequence=4,
        task="Implement beta",
        prior_messages=product.messages(chat.chat_id),
    )
    first_context = build_conversation_context(**kwargs)
    second_context = build_conversation_context(**kwargs)

    assert first_context.fingerprint == second_context.fingerprint
    assert first_context.prior_source_count == 3
    assert first_context.eligible_prior_count == 2
    assert first_context.excluded_prior_count == 1
    assert first_context.selected_prior_count == 2
    assert [item.message_id for item in first_context.items[:-1]] == [
        first.message_id,
        second.message_id,
    ]
    assert first_context.items[-1].source_kind == "submission"
    assert first_context.items[-1].submission_id == kwargs["submission_id"]
    assert first_context.items[-1].text == "Implement beta"
    assert "must-not-enter-runtime-context" not in first_context.model_dump_json()


def test_context_builder_uses_recent_contiguous_suffix_under_item_and_size_bounds(
    tmp_path: Path,
) -> None:
    product = ProjectChatStore(tmp_path / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(name="Project", workspace_root=workspace)
    chat = product.create_chat(project.project_id, title="Chat")
    for index in range(30):
        product.append_text_message(
            chat.chat_id,
            role=ChatMessageRole.USER,
            text=f"turn-{index:02d}-" + ("x" * 900),
        )

    context = build_conversation_context(
        execution_id="exec_" + "3" * 32,
        submission_id="submission_" + "4" * 32,
        project_id=project.project_id,
        chat_id=chat.chat_id,
        reserved_user_sequence=31,
        task="final bounded request",
        prior_messages=product.messages(chat.chat_id),
    )
    selected = context.items[:-1]
    assert 0 < context.selected_prior_count <= 24
    assert context.omitted_prior_count > 0
    assert context.rendered_chars <= context.max_rendered_chars == 20_000
    assert context.rendered_bytes <= context.max_rendered_bytes == 80_000
    assert selected[-1].sequence == 30
    assert [item.sequence for item in selected] == list(
        range(31 - len(selected), 31)
    )


def test_contextual_coordinator_freezes_prior_turns_into_second_app_session_and_restart(
    tmp_path: Path,
) -> None:
    runner = _CaptureRunner()
    service, product, project, chat = _domain(tmp_path, runner=runner)
    lock = threading.RLock()
    root = service.root / "conversation-executions"
    coordinator = ContextualConversationExecutionCoordinator(service, product, lock, root)
    try:
        first = coordinator.submit(
            project_id=project.project_id,
            chat_id=chat.chat_id,
            request=_request("submission_" + "5" * 32, "First work turn"),
        )
        _wait_terminal(coordinator, first.execution_id)
        first_session = service.session(first.session_id)
        assert first_session.request.task == "First work turn"

        second = coordinator.submit(
            project_id=project.project_id,
            chat_id=chat.chat_id,
            request=_request("submission_" + "6" * 32, "Second work turn"),
        )
        context = coordinator.context(second.execution_id)
        assert context.selection_policy == "m71-recent-durable-text-v1"
        assert context.selected_prior_count == 2
        assert [item.role for item in context.items] == ["user", "assistant", "user"]
        assert context.items[-1].text == "Second work turn"

        second_terminal = _wait_terminal(coordinator, second.execution_id)
        second_session = service.session(second_terminal.session_id)
        assert second_session.request.task != "Second work turn"
        assert "First work turn" in second_session.request.task
        assert "Harness X completed this work successfully." in second_session.request.task
        assert second_session.request.task.endswith("Second work turn")
        assert runner.tasks[-1] == second_session.request.task

        restarted = ContextualConversationExecutionCoordinator(service, product, lock, root)
        restarted_context = restarted.context(second.execution_id)
        restarted_projection = restarted.projection(second.execution_id)
        assert restarted_context.fingerprint == context.fingerprint
        assert restarted_projection.session_id == second.session_id
        assert service.session(second.session_id).request == second_session.request
    finally:
        service.close()


def test_pre_m71_plans_are_frozen_as_legacy_passthrough_instead_of_reinterpreted(
    tmp_path: Path,
) -> None:
    runner = _CaptureRunner()
    service, product, project, chat = _domain(tmp_path, runner=runner)
    lock = threading.RLock()
    root = service.root / "conversation-executions"
    legacy = ConversationExecutionCoordinator(service, product, lock, root)
    try:
        first = legacy.submit(
            project_id=project.project_id,
            chat_id=chat.chat_id,
            request=_request("submission_" + "7" * 32, "Legacy first"),
        )
        _wait_terminal(legacy, first.execution_id)
        second = legacy.submit(
            project_id=project.project_id,
            chat_id=chat.chat_id,
            request=_request("submission_" + "8" * 32, "Legacy second"),
        )
        _wait_terminal(legacy, second.execution_id)
        assert service.session(second.session_id).request.task == "Legacy second"

        upgraded = ContextualConversationExecutionCoordinator(service, product, lock, root)
        upgraded.reconcile_all()
        context = upgraded.context(second.execution_id)
        assert context.selection_policy == "m71-legacy-passthrough-v1"
        assert context.selected_prior_count == 0
        assert context.omitted_prior_count == 2
        assert service.session(second.session_id).request.task == "Legacy second"
    finally:
        service.close()


def test_persisted_context_fails_closed_when_its_durable_chat_prefix_changes(
    tmp_path: Path,
) -> None:
    runner = _CaptureRunner()
    service, product, project, chat = _domain(tmp_path, runner=runner)
    lock = threading.RLock()
    root = service.root / "conversation-executions"
    coordinator = ContextualConversationExecutionCoordinator(service, product, lock, root)
    try:
        first = coordinator.submit(
            project_id=project.project_id,
            chat_id=chat.chat_id,
            request=_request("submission_" + "9" * 32, "Original durable turn"),
        )
        _wait_terminal(coordinator, first.execution_id)
        second = coordinator.submit(
            project_id=project.project_id,
            chat_id=chat.chat_id,
            request=_request("submission_" + "a" * 32, "Context-bound turn"),
        )
        _wait_terminal(coordinator, second.execution_id)
        coordinator.context(second.execution_id)

        ledgers = tuple(product.root.rglob("messages.jsonl"))
        assert len(ledgers) == 1
        ledger = ledgers[0]
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        rows[0]["content"]["text"] = "tampered durable turn"
        ledger.write_text(
            "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows)
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="durable context no longer matches chat prefix"):
            coordinator.context(second.execution_id)
    finally:
        service.close()


def test_production_cli_keeps_m70_http_identity_but_installs_m71_context_coordinator(
    tmp_path: Path,
) -> None:
    assert app_server_cli.LocalOperatorHTTPServer.__module__.endswith(
        "conversation_operator_http_server"
    )
    service = AppServerService(tmp_path / "data", runner=_CaptureRunner())
    server = app_server_cli.LocalOperatorHTTPServer(service, tmp_path / "transport", port=0)
    server.start_in_thread()
    try:
        assert isinstance(server.conversation, ContextualConversationExecutionCoordinator)
    finally:
        server.close()
        service.close()
