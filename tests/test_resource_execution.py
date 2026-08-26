from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from harness_x.app_server.resource_execution import (
    ConversationExecutionResourceStore,
    ProjectResourceConversationExecutionCoordinator,
    ResourceConversationExecutionSubmitRequest,
    compile_execution_resources,
)
from harness_x.app_server.sensitive_approval import SensitiveActionApprovalBroker
from harness_x.app_server.service import AppServerService
from harness_x.product import (
    ProjectAutonomyProfile,
    ProjectChatStore,
    ProjectResourceStore,
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


def _wait_terminal(coordinator, execution_id: str, *, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        projection = coordinator.projection(execution_id)
        if projection.terminal:
            return projection
        time.sleep(0.02)
    raise AssertionError(f"conversation execution did not finish: {execution_id}")


def _request(
    submission_id: str,
    text: str,
    *,
    resources: list[dict] | None = None,
    schema_version: str = "conversation-execution-submit-v2",
) -> ResourceConversationExecutionSubmitRequest:
    return ResourceConversationExecutionSubmitRequest.model_validate(
        {
            "schema_version": schema_version,
            "submission_id": submission_id,
            "role": "user",
            "content": {"type": "text", "text": text},
            **({"resources": resources} if resources is not None else {}),
        }
    )


def _runtime(tmp_path: Path):
    root = tmp_path / "server"
    broker = SensitiveActionApprovalBroker(root / "data" / "sensitive-approvals")
    runner = _CaptureApprovalRunner(broker)
    service = AppServerService(root / "data", runner=runner)
    product = ProjectChatStore(service.root / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(name="Project", workspace_root=workspace)
    chat = product.create_chat(project.project_id, title="Chat")
    return service, product, workspace, project, chat, broker, runner


def test_resource_submit_schema_is_v1_compatible_bounded_and_duplicate_safe() -> None:
    legacy = _request(
        "submission_" + "1" * 32,
        "legacy",
        schema_version="conversation-execution-submit-v1",
    )
    assert legacy.resources == ()

    with pytest.raises(ValidationError, match="v1 conversation execution submissions"):
        _request(
            "submission_" + "2" * 32,
            "invalid legacy resources",
            schema_version="conversation-execution-submit-v1",
            resources=[{"kind": "workspace_file", "source_path": "src/a.py"}],
        )

    with pytest.raises(ValidationError, match="cannot contain duplicates"):
        _request(
            "submission_" + "3" * 32,
            "duplicates",
            resources=[
                {"kind": "workspace_file", "source_path": "src/a.py"},
                {"kind": "workspace_file", "source_path": "src/a.py"},
            ],
        )

    with pytest.raises(ValidationError):
        _request(
            "submission_" + "4" * 32,
            "too many",
            resources=[
                {"kind": "workspace_file", "source_path": f"{index}.txt"}
                for index in range(5)
            ],
        )

    with pytest.raises(ValidationError):
        _request(
            "submission_" + "5" * 32,
            "browser cannot author an absolute host path field",
            resources=[
                {
                    "kind": "attachment",
                    "attachment_id": "attachment_" + "a" * 32,
                    "absolute_path": "C:/secret.txt",
                }
            ],
        )


def test_execution_resource_store_is_append_only_restart_safe_and_tamper_evident(
    tmp_path: Path,
) -> None:
    product = ProjectChatStore(tmp_path / "product")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = product.create_project(name="Project", workspace_root=workspace)
    chat = product.create_chat(project.project_id, title="Chat")
    resources = ProjectResourceStore(product)
    attachment = resources.create_attachment(
        project.project_id,
        filename="note.txt",
        data=b"frozen text\n",
        media_type="text/plain",
    )
    request = _request(
        "submission_" + "6" * 32,
        "use note",
        resources=[{"kind": "attachment", "attachment_id": attachment.attachment_id}],
    )
    snapshot = compile_execution_resources(
        execution_id="exec_" + "6" * 32,
        submission_id=request.submission_id,
        project_id=project.project_id,
        chat_id=chat.chat_id,
        references=request.resources,
        resource_store=resources,
    )

    root = tmp_path / "execution"
    store = ConversationExecutionResourceStore(root)
    assert store.put(snapshot) == snapshot
    assert store.put(snapshot) == snapshot
    assert ConversationExecutionResourceStore(root).snapshot(snapshot.execution_id) == snapshot

    lines = store.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    raw = json.loads(lines[0])
    raw["rendered_context"] = raw["rendered_context"].replace("frozen text", "tampered text")
    store.path.write_text(json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ConversationExecutionResourceStore(root)


def test_m74_execution_freezes_resource_context_and_preserves_m72_m73_identity(
    tmp_path: Path,
) -> None:
    service, product, workspace, project, chat, broker, runner = _runtime(tmp_path)
    resource_store = ProjectResourceStore(product)
    attachment = resource_store.create_attachment(
        project.project_id,
        filename="notes.txt",
        data=b"attachment before\n",
        media_type="text/plain",
    )
    source = workspace / "src" / "input.py"
    source.parent.mkdir()
    source.write_bytes(b"print('workspace before')\n")
    settings = ProjectSettingsStore(product).replace(
        project.project_id,
        model_profile="qwen3-8b",
        verification_strategy=ProjectVerificationStrategy.PYTEST_AND_DIFF_CHECK,
        project_instructions="Preserve resource provenance.",
        autonomy_profile=ProjectAutonomyProfile.CAUTIOUS,
    )
    coordinator = ProjectResourceConversationExecutionCoordinator(
        service,
        product,
        threading.RLock(),
        service.root / "conversation-executions",
    )
    try:
        request = _request(
            "submission_" + "7" * 32,
            "Use the referenced inputs",
            resources=[
                {"kind": "attachment", "attachment_id": attachment.attachment_id},
                {"kind": "workspace_file", "source_path": "src/input.py"},
            ],
        )
        projection = coordinator.submit(
            project_id=project.project_id,
            chat_id=chat.chat_id,
            request=request,
        )
        plan = coordinator.store.plan(projection.execution_id)
        assert Path(plan.output_root).name == f"conversation_resources_{projection.execution_id}"
        frozen = coordinator.resource_execution_store.snapshot(projection.execution_id)
        assert frozen is not None
        assert frozen.request_keys == request.resource_keys
        assert frozen.preserves_sensitive_action_approval is True
        assert len(frozen.items) == 2
        assert "attachment before" in frozen.rendered_context
        assert "workspace before" in frozen.rendered_context
        assert "authority: untrusted-context-only" in frozen.rendered_context
        assert broker.context_for_output_root(plan.output_root) is not None

        settings_snapshot = coordinator.settings_execution_store.snapshot(projection.execution_id)
        assert settings_snapshot is not None
        assert settings_snapshot.settings_fingerprint == settings.fingerprint
        assert settings_snapshot.model_profile == "qwen3-8b"
        assert settings_snapshot.verification_commands == ("python -m pytest", "git diff --check")

        effective = coordinator._effective_request(plan)
        assert "Preserve resource provenance." in effective.task
        assert "attachment before" in effective.task
        assert "workspace before" in effective.task

        source.write_bytes(b"print('workspace after')\n")
        attachment_blob = (
            product.projects_root
            / project.project_id
            / "resources"
            / "attachments"
            / "blobs"
            / f"{attachment.attachment_id}.blob"
        )
        attachment_blob.write_bytes(b"attachment after\n")
        assert coordinator._effective_request(plan) == effective

        retried = coordinator.submit(
            project_id=project.project_id,
            chat_id=chat.chat_id,
            request=request,
        )
        assert retried.execution_id == projection.execution_id
        assert coordinator.resource_execution_store.snapshot(projection.execution_id) == frozen

        different = _request(
            request.submission_id,
            request.text,
            resources=[{"kind": "workspace_file", "source_path": "src/other.py"}],
        )
        with pytest.raises(ValueError, match="different conversation resources"):
            coordinator.submit(
                project_id=project.project_id,
                chat_id=chat.chat_id,
                request=different,
            )

        terminal = _wait_terminal(coordinator, projection.execution_id)
        session = service.session(terminal.session_id)
        assert session.request == effective
        assert runner.requests and runner.requests[-1] == effective

        restarted = ProjectResourceConversationExecutionCoordinator(
            service,
            product,
            threading.RLock(),
            service.root / "conversation-executions",
        )
        assert restarted.resource_execution_store.snapshot(projection.execution_id) == frozen
        assert restarted._effective_request(restarted.store.plan(projection.execution_id)) == effective
    finally:
        service.close()


def test_binary_resource_is_metadata_only_and_text_projection_is_explicitly_truncated(
    tmp_path: Path,
) -> None:
    service, product, workspace, project, chat, _, _ = _runtime(tmp_path)
    resources = ProjectResourceStore(product)
    binary = resources.create_attachment(
        project.project_id,
        filename="opaque.bin",
        data=b"\xff\x00\x01",
    )
    long_text = workspace / "long.txt"
    long_text.write_bytes(("x" * 4000).encode("utf-8"))
    coordinator = ProjectResourceConversationExecutionCoordinator(
        service,
        product,
        threading.RLock(),
        service.root / "conversation-executions",
    )
    try:
        projection = coordinator.submit(
            project_id=project.project_id,
            chat_id=chat.chat_id,
            request=_request(
                "submission_" + "8" * 32,
                "Inspect bounded resources",
                resources=[
                    {"kind": "attachment", "attachment_id": binary.attachment_id},
                    {"kind": "workspace_file", "source_path": "long.txt"},
                ],
            ),
        )
        frozen = coordinator.resource_execution_store.snapshot(projection.execution_id)
        assert frozen is not None
        opaque, textual = frozen.items
        assert opaque.text_encoding is None
        assert opaque.context_text is None
        assert opaque.context_truncated is False
        assert "[opaque metadata only]" in frozen.rendered_context
        assert "\ufffd" not in frozen.rendered_context

        assert textual.text_encoding == "utf-8"
        assert textual.context_text == "x" * 1200
        assert textual.context_truncated is True
        effective = coordinator._effective_request(coordinator.store.plan(projection.execution_id))
        assert len(effective.task) <= 20_000
        assert len(effective.task.encode("utf-8")) <= 80_000
    finally:
        service.close()


def test_m74_combined_project_resource_and_chat_context_stays_inside_inherited_envelope(
    tmp_path: Path,
) -> None:
    service, product, workspace, project, chat, _, _ = _runtime(tmp_path)
    ProjectSettingsStore(product).replace(
        project.project_id,
        model_profile="main",
        verification_strategy=ProjectVerificationStrategy.DIFF_CHECK,
        project_instructions="I" * 5000,
        autonomy_profile=ProjectAutonomyProfile.STANDARD,
    )
    for index in range(4):
        (workspace / f"input-{index}.txt").write_bytes(("z" * 4000).encode("utf-8"))
    for index in range(12):
        product.append_text_message(
            chat.chat_id,
            role="user" if index % 2 == 0 else "assistant",
            text=(f"history-{index} " + "h" * 1200),
        )
    coordinator = ProjectResourceConversationExecutionCoordinator(
        service,
        product,
        threading.RLock(),
        service.root / "conversation-executions",
    )
    try:
        request = _request(
            "submission_" + "9" * 32,
            "Use bounded combined context",
            resources=[
                {"kind": "workspace_file", "source_path": f"input-{index}.txt"}
                for index in range(4)
            ],
        )
        projection = coordinator.submit(
            project_id=project.project_id,
            chat_id=chat.chat_id,
            request=request,
        )
        frozen = coordinator.resource_execution_store.snapshot(projection.execution_id)
        assert frozen is not None
        assert all(item.context_truncated for item in frozen.items)
        effective = coordinator._effective_request(coordinator.store.plan(projection.execution_id))
        assert len(effective.task) <= 20_000
        assert len(effective.task.encode("utf-8")) <= 80_000
        assert "HARNESS X PROJECT INSTRUCTIONS" in effective.task
        assert "HARNESS X EXECUTION RESOURCES" in effective.task
        assert "Use bounded combined context" in effective.task
    finally:
        service.close()


def test_resource_resolution_rejects_wrong_project_before_plan_acceptance(tmp_path: Path) -> None:
    service, product, workspace, project, chat, _, _ = _runtime(tmp_path)
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    other = product.create_project(name="Other", workspace_root=other_workspace)
    attachment = ProjectResourceStore(product).create_attachment(
        other.project_id,
        filename="other.txt",
        data=b"not owned",
    )
    coordinator = ProjectResourceConversationExecutionCoordinator(
        service,
        product,
        threading.RLock(),
        service.root / "conversation-executions",
    )
    try:
        with pytest.raises(KeyError):
            coordinator.submit(
                project_id=project.project_id,
                chat_id=chat.chat_id,
                request=_request(
                    "submission_" + "a" * 32,
                    "wrong owner",
                    resources=[
                        {"kind": "attachment", "attachment_id": attachment.attachment_id}
                    ],
                ),
            )
        assert coordinator.store.plan_for_submission("submission_" + "a" * 32) is None
    finally:
        service.close()


def test_missing_m74_resource_snapshot_fails_closed_on_restart(tmp_path: Path) -> None:
    service, product, workspace, project, chat, _, _ = _runtime(tmp_path)
    (workspace / "input.txt").write_bytes(b"input\n")
    coordinator = ProjectResourceConversationExecutionCoordinator(
        service,
        product,
        threading.RLock(),
        service.root / "conversation-executions",
    )
    try:
        projection = coordinator.submit(
            project_id=project.project_id,
            chat_id=chat.chat_id,
            request=_request(
                "submission_" + "b" * 32,
                "freeze input",
                resources=[{"kind": "workspace_file", "source_path": "input.txt"}],
            ),
        )
        assert coordinator.resource_execution_store.snapshot(projection.execution_id) is not None
        coordinator.resource_execution_store.path.unlink()
        with pytest.raises(RuntimeError, match="resource snapshot"):
            ProjectResourceConversationExecutionCoordinator(
                service,
                product,
                threading.RLock(),
                service.root / "conversation-executions",
            )
    finally:
        service.close()
