from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from harness_x.app_server.conversation_execution import ConversationExecutionSubmitRequest
from harness_x.app_server.project_settings_execution import (
    ProjectSettingsConversationExecutionCoordinator,
    ProjectSettingsExecutionStore,
    compile_project_settings,
)
from harness_x.app_server.sensitive_approval import SensitiveActionApprovalBroker
from harness_x.app_server.service import AppServerService
from harness_x.product import (
    ProjectAutonomyProfile,
    ProjectChatStore,
    ProjectSettingsStore,
    ProjectVerificationStrategy,
)


class _Report(BaseModel):
    succeeded: bool = True


class _CaptureApprovalRunner:
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


@pytest.mark.parametrize(
    ("verification_strategy", "commands"),
    [
        (ProjectVerificationStrategy.DIFF_CHECK, ("git diff --check",)),
        (ProjectVerificationStrategy.PYTEST, ("python -m pytest",)),
        (
            ProjectVerificationStrategy.PYTEST_AND_DIFF_CHECK,
            ("python -m pytest", "git diff --check"),
        ),
    ],
)
def test_settings_compiler_uses_only_named_verification_policies(
    tmp_path: Path,
    verification_strategy: ProjectVerificationStrategy,
    commands: tuple[str, ...],
) -> None:
    product = ProjectChatStore(tmp_path / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(name="Project", workspace_root=workspace)
    settings = ProjectSettingsStore(product).replace(
        project.project_id,
        model_profile="qwen3-8b",
        verification_strategy=verification_strategy,
        project_instructions="Keep changes narrow.",
        autonomy_profile=ProjectAutonomyProfile.CAUTIOUS,
    )

    snapshot = compile_project_settings("exec_" + "1" * 32, settings)
    assert snapshot.model_profile == "qwen3-8b"
    assert snapshot.verification_commands == commands
    assert snapshot.max_reasoning_steps == 16
    assert snapshot.max_tool_actions == 24
    assert snapshot.preserves_sensitive_action_approval is True
    assert snapshot.settings_fingerprint == settings.fingerprint
    assert snapshot.project_instructions == "Keep changes narrow."


def test_standard_autonomy_retains_existing_bounded_runtime_defaults(tmp_path: Path) -> None:
    product = ProjectChatStore(tmp_path / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(name="Project", workspace_root=workspace)
    settings = ProjectSettingsStore(product).settings(project.project_id)
    snapshot = compile_project_settings("exec_" + "2" * 32, settings)
    assert snapshot.model_profile == "main"
    assert snapshot.max_reasoning_steps == 32
    assert snapshot.max_tool_actions == 48
    assert snapshot.verification_commands == ("git diff --check",)


def test_settings_compiler_rejects_unknown_model_profile_before_execution(tmp_path: Path) -> None:
    product = ProjectChatStore(tmp_path / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(name="Project", workspace_root=workspace)
    settings = ProjectSettingsStore(product).replace(
        project.project_id,
        model_profile="unknown-profile",
        verification_strategy=ProjectVerificationStrategy.DIFF_CHECK,
        project_instructions="",
        autonomy_profile=ProjectAutonomyProfile.STANDARD,
    )
    with pytest.raises(ValueError, match="unknown model profile"):
        compile_project_settings("exec_" + "3" * 32, settings)


def test_execution_snapshot_store_is_append_only_restart_safe_and_tamper_evident(
    tmp_path: Path,
) -> None:
    product = ProjectChatStore(tmp_path / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(name="Project", workspace_root=workspace)
    settings = ProjectSettingsStore(product).settings(project.project_id)
    snapshot = compile_project_settings("exec_" + "4" * 32, settings)

    root = tmp_path / "execution"
    store = ProjectSettingsExecutionStore(root)
    assert store.put(snapshot) == snapshot
    assert store.put(snapshot) == snapshot
    assert ProjectSettingsExecutionStore(root).snapshot(snapshot.execution_id) == snapshot

    lines = store.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    raw = json.loads(lines[0])
    raw["model_profile"] = "coder"
    store.path.write_text(json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        ProjectSettingsExecutionStore(root)


def test_m73_execution_freezes_settings_and_keeps_m72_approval_identity(tmp_path: Path) -> None:
    root = tmp_path / "server"
    broker = SensitiveActionApprovalBroker(root / "data" / "sensitive-approvals")
    runner = _CaptureApprovalRunner(broker)
    service = AppServerService(root / "data", runner=runner)
    product = ProjectChatStore(service.root / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(name="Project", workspace_root=workspace)
    chat = product.create_chat(project.project_id, title="Chat")
    settings_store = ProjectSettingsStore(product)
    initial = settings_store.replace(
        project.project_id,
        model_profile="qwen3-8b",
        verification_strategy=ProjectVerificationStrategy.PYTEST_AND_DIFF_CHECK,
        project_instructions="Preserve invariant alpha.",
        autonomy_profile=ProjectAutonomyProfile.CAUTIOUS,
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
            request=_request("submission_" + "5" * 32, "Implement the requested change"),
        )
        plan = coordinator.store.plan(projection.execution_id)
        frozen = coordinator.settings_execution_store.snapshot(projection.execution_id)
        assert frozen is not None
        assert frozen.settings_revision == initial.revision
        assert frozen.settings_fingerprint == initial.fingerprint
        assert frozen.model_profile == "qwen3-8b"
        assert frozen.verification_commands == ("python -m pytest", "git diff --check")
        assert plan.request.model_profile == "qwen3-8b"
        assert plan.request.verification_commands == frozen.verification_commands
        assert plan.request.max_reasoning_steps == 16
        assert plan.request.max_tool_actions == 24
        assert broker.context_for_output_root(plan.output_root) is not None

        effective = coordinator._effective_request(plan)
        assert "Preserve invariant alpha." in effective.task
        assert f"settings_revision: {initial.revision}" in effective.task
        assert f"settings_fingerprint: {initial.fingerprint}" in effective.task

        later = settings_store.replace(
            project.project_id,
            model_profile="main",
            verification_strategy=ProjectVerificationStrategy.DIFF_CHECK,
            project_instructions="Replacement instruction beta.",
            autonomy_profile=ProjectAutonomyProfile.STANDARD,
        )
        assert later.fingerprint != initial.fingerprint
        assert coordinator.settings_execution_store.snapshot(projection.execution_id) == frozen
        effective_after_edit = coordinator._effective_request(plan)
        assert effective_after_edit == effective
        assert "Replacement instruction beta." not in effective_after_edit.task

        terminal = _wait_terminal(coordinator, projection.execution_id)
        session = service.session(terminal.session_id)
        assert session.request.model_profile == "qwen3-8b"
        assert session.request.verification_commands == frozen.verification_commands
        assert session.request.max_reasoning_steps == 16
        assert session.request.max_tool_actions == 24
        assert "Preserve invariant alpha." in session.request.task
        assert runner.requests and runner.requests[-1] == session.request

        restarted = ProjectSettingsConversationExecutionCoordinator(
            service,
            product,
            threading.RLock(),
            service.root / "conversation-executions",
        )
        restarted_frozen = restarted.settings_execution_store.snapshot(projection.execution_id)
        assert restarted_frozen == frozen
        assert restarted.projection(projection.execution_id).session_id == terminal.session_id
    finally:
        service.close()
