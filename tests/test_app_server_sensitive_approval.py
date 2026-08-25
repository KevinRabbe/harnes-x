from __future__ import annotations

import http.client
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel

from harness_x.app_server import cli as app_server_cli
from harness_x.app_server.sensitive_approval import (
    ApprovalAwareToolExecutor,
    ApprovalDecision,
    ApprovalStatus,
    ConversationSensitiveActionGate,
    SensitiveActionApprovalBroker,
    activate_sensitive_action_gate,
)
from harness_x.app_server.sensitive_approval_operator_http_server import LocalOperatorHTTPServer
from harness_x.app_server.service import AppServerService
from harness_x.app_server.ui_assets import load_ui_asset
from harness_x.core import FixedClock, SystemVersion, TaskId, TraceId
from harness_x.core.contracts import ActionProposal
from harness_x.core.ids import CandidateId
from harness_x.core.provenance import Provenance, SourceKind, VerificationState
from harness_x.orchestrator import TaskOrchestrator
from harness_x.telemetry import TraceRecorder, TraceStore
from harness_x.tools.base import ToolStatus
from harness_x.tools.coding import build_coding_registry


_EXECUTION_ID = "exec_" + "1" * 32
_PROJECT_ID = "project_" + "2" * 32
_CHAT_ID = "chat_" + "3" * 32
_SESSION_ID = "app_" + "4" * 32


def _recorder(tmp_path: Path, *, suffix: str = "approval") -> TraceRecorder:
    return TraceRecorder(
        TraceStore(tmp_path / f"{suffix}.jsonl"),
        TraceId(value="trace_" + "5" * 32),
        TaskId(value="task_" + "6" * 32),
        SystemVersion(value="test-m72"),
        FixedClock(datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)),
    )


def _proposal(
    recorder: TraceRecorder,
    *,
    tool_name: str,
    arguments: dict,
    source_kind: SourceKind = SourceKind.MODEL,
) -> ActionProposal:
    return ActionProposal(
        candidate_id=CandidateId(value="candidate_" + "7" * 32),
        task_id=recorder.task_id,
        tool_name=tool_name,
        arguments=arguments,
        provenance=Provenance(
            source_kind=source_kind,
            source_ref="test:m72-proposal",
            created_at=recorder.clock.now(),
            system_version=recorder.system_version,
            trace_id=recorder.trace_id,
            verification=VerificationState.UNVERIFIED,
        ),
    )


def _tool_domain(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    recorder = _recorder(tmp_path)
    orchestrator = TaskOrchestrator.create(recorder)
    orchestrator.start("m72-test")
    registry = build_coding_registry(workspace)
    executor = ApprovalAwareToolExecutor(registry, recorder, orchestrator)
    broker = SensitiveActionApprovalBroker(tmp_path / "approvals")
    context = broker.register_execution(
        execution_id=_EXECUTION_ID,
        project_id=_PROJECT_ID,
        chat_id=_CHAT_ID,
        output_root=tmp_path / "run",
        created_at=recorder.clock.now(),
    )
    gate = ConversationSensitiveActionGate(broker, context, _SESSION_ID)
    return workspace, recorder, orchestrator, registry, executor, broker, gate


def _wait_approval(broker: SensitiveActionApprovalBroker, *, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        approvals = broker.projections_for_execution(_EXECUTION_ID)
        if approvals:
            return approvals[0]
        time.sleep(0.01)
    raise AssertionError("sensitive approval did not become durable")


def test_model_overwrite_waits_before_budget_or_execution_then_exact_approve_releases_once(
    tmp_path: Path,
) -> None:
    workspace, recorder, orchestrator, _, executor, broker, gate = _tool_domain(tmp_path)
    target = workspace / "owned.txt"
    target.write_text("old", encoding="utf-8")
    proposal = _proposal(
        recorder,
        tool_name="workspace_write",
        arguments={"path": "owned.txt", "content": "new", "overwrite": True},
    )
    result_box: list = []

    def execute() -> None:
        with activate_sensitive_action_gate(gate):
            result_box.append(
                executor.execute(
                    proposal,
                    routine_allowed_tools=("workspace_write",),
                    granted_permissions=frozenset({"workspace.write"}),
                )
            )

    thread = threading.Thread(target=execute, daemon=True)
    thread.start()
    pending = _wait_approval(broker)
    assert pending.status == ApprovalStatus.PENDING
    assert pending.tool_name == "workspace_write"
    assert pending.details == {
        "path": "owned.txt",
        "overwrite": True,
        "content_characters": 3,
    }
    assert target.read_text(encoding="utf-8") == "old"
    assert orchestrator.session.usage.tool_actions == 0

    first_decision = broker.decide(pending.approval_id, ApprovalDecision.APPROVE)
    second_decision = broker.decide(pending.approval_id, ApprovalDecision.APPROVE)
    assert first_decision.decision == second_decision.decision == ApprovalDecision.APPROVE
    with pytest.raises(ValueError, match="conflicting decision"):
        broker.decide(pending.approval_id, ApprovalDecision.REJECT)

    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert len(result_box) == 1 and result_box[0].status == ToolStatus.SUCCEEDED
    assert target.read_text(encoding="utf-8") == "new"
    assert orchestrator.session.usage.tool_actions == 1
    assert broker.projection(pending.approval_id).status == ApprovalStatus.RELEASED

    # The exact same software proposal cannot consume the same approval release twice.
    with activate_sensitive_action_gate(gate):
        duplicate = executor.execute(
            proposal,
            routine_allowed_tools=("workspace_write",),
            granted_permissions=frozenset({"workspace.write"}),
        )
    assert duplicate.status == ToolStatus.DENIED
    assert "already" in (duplicate.error or "") or "conflict" in (duplicate.error or "")
    assert orchestrator.session.usage.tool_actions == 1


def test_reject_and_interrupted_pending_never_execute_and_pending_survives_reload(
    tmp_path: Path,
) -> None:
    workspace, recorder, orchestrator, _, executor, broker, gate = _tool_domain(tmp_path)
    target = workspace / "owned.txt"
    target.write_text("old", encoding="utf-8")
    proposal = _proposal(
        recorder,
        tool_name="workspace_write",
        arguments={"path": "owned.txt", "content": "rejected", "overwrite": True},
    )
    result_box: list = []

    def execute() -> None:
        with activate_sensitive_action_gate(gate):
            result_box.append(
                executor.execute(
                    proposal,
                    routine_allowed_tools=("workspace_write",),
                    granted_permissions=frozenset({"workspace.write"}),
                )
            )

    thread = threading.Thread(target=execute, daemon=True)
    thread.start()
    pending = _wait_approval(broker)
    broker.decide(pending.approval_id, ApprovalDecision.REJECT)
    thread.join(timeout=5.0)
    assert result_box[0].status == ToolStatus.DENIED
    assert "rejected" in (result_box[0].error or "")
    assert target.read_text(encoding="utf-8") == "old"
    assert orchestrator.session.usage.tool_actions == 0

    # A separate unresolved request remains pending after an interrupted waiter and reload.
    second_root = tmp_path / "second"
    second_root.mkdir()
    second_broker = SensitiveActionApprovalBroker(second_root / "approvals")
    context = second_broker.register_execution(
        execution_id=_EXECUTION_ID,
        project_id=_PROJECT_ID,
        chat_id=_CHAT_ID,
        output_root=second_root / "run",
        created_at=recorder.clock.now(),
    )
    second_gate = ConversationSensitiveActionGate(second_broker, context, _SESSION_ID)
    second_executor = ApprovalAwareToolExecutor(build_coding_registry(workspace), recorder, orchestrator)
    interrupted_box: list = []

    def execute_interrupted() -> None:
        with activate_sensitive_action_gate(second_gate):
            interrupted_box.append(
                second_executor.execute(
                    _proposal(
                        recorder,
                        tool_name="workspace_write",
                        arguments={"path": "owned.txt", "content": "never", "overwrite": True},
                    ),
                    routine_allowed_tools=("workspace_write",),
                    granted_permissions=frozenset({"workspace.write"}),
                )
            )

    interrupted = threading.Thread(target=execute_interrupted, daemon=True)
    interrupted.start()
    unresolved = _wait_approval(second_broker)
    second_broker.interrupt_waiters()
    interrupted.join(timeout=5.0)
    assert interrupted_box[0].status == ToolStatus.DENIED
    reloaded = SensitiveActionApprovalBroker(second_root / "approvals")
    persisted = reloaded.projection(unresolved.approval_id)
    assert persisted.status == ApprovalStatus.PENDING
    assert persisted.decision is None and persisted.released_at is None
    assert target.read_text(encoding="utf-8") == "old"


def test_schema_invalid_and_software_owned_actions_do_not_open_operator_approval(tmp_path: Path) -> None:
    workspace, recorder, orchestrator, registry, executor, broker, gate = _tool_domain(tmp_path)
    invalid = _proposal(
        recorder,
        tool_name="workspace_write",
        arguments={"content": "missing path", "overwrite": True},
    )
    with activate_sensitive_action_gate(gate):
        invalid_result = executor.execute(
            invalid,
            routine_allowed_tools=("workspace_write",),
            granted_permissions=frozenset({"workspace.write"}),
        )
    assert invalid_result.status == ToolStatus.INVALID_INPUT
    assert broker.projections_for_execution(_EXECUTION_ID) == ()
    assert orchestrator.session.usage.tool_actions == 0

    process_definition = registry.require("process_run")
    parsed = process_definition.input_model.model_validate(
        {"argv": ["python", "-c", "print('verification')"], "cwd": "."}
    )
    software_proposal = _proposal(
        recorder,
        tool_name="process_run",
        arguments=parsed.model_dump(mode="python"),
        source_kind=SourceKind.SYSTEM,
    )
    assert broker.authorize(
        context=broker.store.context(_EXECUTION_ID),
        session_id=_SESSION_ID,
        proposal=software_proposal,
        definition=process_definition,
        parsed_input=parsed,
    ) is None
    assert broker.projections_for_execution(_EXECUTION_ID) == ()
    assert not (workspace / "unexpected").exists()


class _Report(BaseModel):
    succeeded: bool
    failure_reason: str | None = None


class _HTTPApprovalRunner:
    def __init__(self, broker: SensitiveActionApprovalBroker) -> None:
        self.approval_broker = broker

    def __call__(self, snapshot) -> _Report:
        workspace = Path(snapshot.request.workspace_root)
        target = workspace / "approval-http.txt"
        recorder = TraceRecorder(
            TraceStore(Path(snapshot.output_root) / ("trace_" + "8" * 32 + ".jsonl")),
            TraceId(value="trace_" + "8" * 32),
            TaskId(value="task_" + "9" * 32),
            SystemVersion(value="test-m72-http"),
            FixedClock(datetime(2026, 8, 25, 12, 5, tzinfo=timezone.utc)),
        )
        orchestrator = TaskOrchestrator.create(recorder)
        orchestrator.start("m72-http")
        executor = ApprovalAwareToolExecutor(build_coding_registry(workspace), recorder, orchestrator)
        proposal = _proposal(
            recorder,
            tool_name="workspace_write",
            arguments={"path": "approval-http.txt", "content": "approved", "overwrite": True},
        )
        context = self.approval_broker.context_for_output_root(snapshot.output_root)
        assert context is not None
        gate = ConversationSensitiveActionGate(self.approval_broker, context, snapshot.session_id)
        with activate_sensitive_action_gate(gate):
            result = executor.execute(
                proposal,
                routine_allowed_tools=("workspace_write",),
                granted_permissions=frozenset({"workspace.write"}),
            )
        report = _Report(succeeded=result.succeeded, failure_reason=result.error)
        Path(snapshot.output_root).mkdir(parents=True, exist_ok=True)
        (Path(snapshot.output_root) / "coding-task-report.json").write_text(
            report.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        assert target.exists()
        return report


class _ServerHarness:
    def __init__(self, root: Path) -> None:
        self.broker = SensitiveActionApprovalBroker(root / "data" / "sensitive-approvals")
        self.runner = _HTTPApprovalRunner(self.broker)
        self.service = AppServerService(root / "data", runner=self.runner)
        self.server = LocalOperatorHTTPServer(self.service, root / "transport", port=0)
        self.server.start_in_thread()

    def close(self) -> None:
        self.server.close()
        self.service.close()


def _http(
    harness: _ServerHarness,
    method: str,
    path: str,
    *,
    body: object | None = None,
    token: str | None = "server",
) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", harness.server.port, timeout=10)
    headers: dict[str, str] = {}
    if token == "server":
        headers["Authorization"] = f"Bearer {harness.server.token}"
    elif token is not None:
        headers["Authorization"] = f"Bearer {token}"
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw.decode("utf-8")) if raw else {}


def _create_project_chat(harness: _ServerHarness, workspace: Path) -> tuple[dict, dict]:
    status, project = _http(
        harness,
        "POST",
        "/v1/projects",
        body={
            "schema_version": "create-project-request-v1",
            "name": "Approval project",
            "workspace_root": str(workspace),
            "default_model_profile": None,
        },
    )
    assert status == 201, project
    status, chat = _http(
        harness,
        "POST",
        f"/v1/projects/{project['project_id']}/chats",
        body={"schema_version": "create-chat-request-v1", "title": "Approval chat"},
    )
    assert status == 201, chat
    return project, chat


def test_http_approval_is_authenticated_owned_exact_and_terminal_safe(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "approval-http.txt"
    target.write_text("old", encoding="utf-8")
    harness = _ServerHarness(tmp_path / "server")
    try:
        project, chat = _create_project_chat(harness, workspace)
        _, other_chat = _create_project_chat(harness, tmp_path / "other-workspace") if False else (None, None)
        execution_path = f"/v1/projects/{project['project_id']}/chats/{chat['chat_id']}/executions"
        status, execution = _http(
            harness,
            "POST",
            execution_path,
            body={
                "schema_version": "conversation-execution-submit-v1",
                "submission_id": "submission_" + "a" * 32,
                "role": "user",
                "content": {"type": "text", "text": "Perform approval-qualified work"},
            },
        )
        assert status == 202, execution
        approvals_path = f"{execution_path}/{execution['execution_id']}/approvals"

        deadline = time.monotonic() + 8.0
        approval_page = None
        while time.monotonic() < deadline:
            status, candidate = _http(harness, "GET", approvals_path)
            assert status == 200, candidate
            if candidate["approvals"]:
                approval_page = candidate
                break
            time.sleep(0.02)
        assert approval_page is not None
        approval = approval_page["approvals"][0]
        assert approval["status"] == "pending"
        assert target.read_text(encoding="utf-8") == "old"

        status, unauthorized = _http(harness, "GET", approvals_path, token=None)
        assert status == 401 and unauthorized["error"] == "unauthorized"

        decision_path = f"{approvals_path}/{approval['approval_id']}"
        status, malformed = _http(
            harness,
            "POST",
            decision_path,
            body={
                "schema_version": "sensitive-action-approval-decision-request-v1",
                "decision": "approve",
                "tool_name": "workspace_write",
            },
        )
        assert status == 400 and malformed["error"] == "invalid_sensitive_approval_request"

        status, decided = _http(
            harness,
            "POST",
            decision_path,
            body={
                "schema_version": "sensitive-action-approval-decision-request-v1",
                "decision": "approve",
            },
        )
        assert status == 200 and decided["decision"] == "approve"

        deadline = time.monotonic() + 8.0
        terminal = None
        while time.monotonic() < deadline:
            status, candidate = _http(
                harness,
                "GET",
                f"{execution_path}/{execution['execution_id']}",
            )
            assert status == 200, candidate
            if candidate["terminal"]:
                terminal = candidate
                break
            time.sleep(0.02)
        assert terminal is not None and terminal["status"] == "succeeded"
        assert target.read_text(encoding="utf-8") == "approved"

        status, terminal_decision = _http(
            harness,
            "POST",
            decision_path,
            body={
                "schema_version": "sensitive-action-approval-decision-request-v1",
                "decision": "approve",
            },
        )
        assert status == 409
        assert "terminal" in (terminal_decision.get("detail") or "")
    finally:
        harness.close()


def test_approval_ui_is_allowlisted_safe_and_sends_decision_only() -> None:
    asset = load_ui_asset("/ui/approval_bridge.js")
    assert asset is not None
    javascript = asset[1].decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    bootstrap_asset = load_ui_asset("/ui/bootstrap.js")
    assert bootstrap_asset is not None
    bootstrap = bootstrap_asset[1].decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")

    for fragment in (
        "/approvals",
        "sensitive-action-approval-decision-request-v1",
        'decide("approve")',
        'decide("reject")',
        "textContent",
        "conversationExecutionPath",
    ):
        assert fragment in javascript
    for forbidden in (
        "Authorization",
        "Bearer ",
        "innerHTML",
        "insertAdjacentHTML",
        "localStorage",
        "sessionStorage",
        "document.cookie",
    ):
        assert forbidden not in javascript

    body_start = javascript.index("body: JSON.stringify({")
    body_end = javascript.index("}),", body_start)
    decision_body = javascript[body_start:body_end]
    assert "schema_version" in decision_body and "decision" in decision_body
    for forbidden in ("tool_name", "arguments", "command", "path", "details", "candidate_id"):
        assert forbidden not in decision_body

    assert "/ui/execution_bridge.js" in bootstrap
    assert "/ui/approval_bridge.js" in bootstrap
    assert bootstrap.index("await loadConversationExecutionBridge") < bootstrap.index(
        "await loadSensitiveApprovalBridge"
    )
    assert bootstrap.index("history.replaceState") < bootstrap.index(
        "await loadConversationExecutionBridge"
    )
    assert app_server_cli.LocalOperatorHTTPServer.__module__.endswith(
        "conversation_operator_http_server"
    )
