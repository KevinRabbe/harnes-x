"""Durable M72 approval state and the conversation-scoped tool execution gate.

The operator approves or rejects an exact software-proposed action. The browser never authors
or mutates the proposal. Approval state is deliberately separate from chat, trace/evidence,
memory, and self-improvement authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from harness_x.core.contracts import ActionProposal
from harness_x.core.provenance import SourceKind
from harness_x.orchestrator import BudgetDelta
from harness_x.tools.base import ToolDefinition, ToolExecutor, ToolResult, ToolStatus


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RELEASED = "released"


class ApprovalExecutionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["sensitive-approval-execution-context-v1"] = (
        "sensitive-approval-execution-context-v1"
    )
    execution_id: str = Field(pattern=r"^exec_[0-9a-f]{32}$")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    output_root: str = Field(min_length=1, max_length=4000)
    created_at: datetime
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ApprovalExecutionContext":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", _sha256(material))
        return self


class SensitiveActionApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["sensitive-action-approval-request-v1"] = (
        "sensitive-action-approval-request-v1"
    )
    approval_id: str = Field(pattern=r"^approval_[0-9a-f]{32}$")
    execution_id: str = Field(pattern=r"^exec_[0-9a-f]{32}$")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    session_id: str = Field(pattern=r"^app_[0-9a-f]{32}$")
    candidate_id: str = Field(min_length=1, max_length=160)
    action_fingerprint: str = Field(min_length=64, max_length=64)
    tool_name: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1200)
    details: dict[str, str | int | bool] = Field(default_factory=dict)
    created_at: datetime
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "SensitiveActionApprovalRequest":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", _sha256(material))
        return self


class SensitiveActionApprovalDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["sensitive-action-approval-decision-v1"] = (
        "sensitive-action-approval-decision-v1"
    )
    approval_id: str = Field(pattern=r"^approval_[0-9a-f]{32}$")
    decision: ApprovalDecision
    decided_at: datetime
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "SensitiveActionApprovalDecision":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", _sha256(material))
        return self


class SensitiveActionApprovalRelease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["sensitive-action-approval-release-v1"] = (
        "sensitive-action-approval-release-v1"
    )
    approval_id: str = Field(pattern=r"^approval_[0-9a-f]{32}$")
    released_at: datetime
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "SensitiveActionApprovalRelease":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", _sha256(material))
        return self


class SensitiveActionApprovalProjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["sensitive-action-approval-projection-v1"] = (
        "sensitive-action-approval-projection-v1"
    )
    approval_id: str
    execution_id: str
    project_id: str
    chat_id: str
    session_id: str
    candidate_id: str
    action_fingerprint: str
    tool_name: str
    summary: str
    details: dict[str, str | int | bool]
    status: ApprovalStatus
    decision: ApprovalDecision | None = None
    created_at: datetime
    decided_at: datetime | None = None
    released_at: datetime | None = None


class ApprovalDecisionRequest(BaseModel):
    """The browser can author only one decision for one approval ID in the URL."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["sensitive-action-approval-decision-request-v1"] = (
        "sensitive-action-approval-decision-request-v1"
    )
    decision: ApprovalDecision


class SensitiveActionApprovalStore:
    """Append-only durable approval ledgers with fail-closed fingerprint validation."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.contexts_path = self.root / "execution-contexts.jsonl"
        self.requests_path = self.root / "approval-requests.jsonl"
        self.decisions_path = self.root / "approval-decisions.jsonl"
        self.releases_path = self.root / "approval-releases.jsonl"
        self._contexts: dict[str, ApprovalExecutionContext] = {}
        self._output_roots: dict[str, str] = {}
        self._requests: dict[str, SensitiveActionApprovalRequest] = {}
        self._decisions: dict[str, SensitiveActionApprovalDecision] = {}
        self._releases: dict[str, SensitiveActionApprovalRelease] = {}
        self._load()

    @property
    def contexts(self) -> tuple[ApprovalExecutionContext, ...]:
        return tuple(sorted(self._contexts.values(), key=lambda item: (item.created_at, item.execution_id)))

    def context(self, execution_id: str) -> ApprovalExecutionContext:
        try:
            return self._contexts[execution_id]
        except KeyError as exc:
            raise KeyError(f"unknown approval execution context {execution_id}") from exc

    def context_for_output_root(self, output_root: str | Path) -> ApprovalExecutionContext | None:
        key = str(Path(output_root).resolve())
        execution_id = self._output_roots.get(key)
        return None if execution_id is None else self.context(execution_id)

    def put_context(self, context: ApprovalExecutionContext) -> ApprovalExecutionContext:
        existing = self._contexts.get(context.execution_id)
        if existing is not None:
            if existing.fingerprint != context.fingerprint:
                raise ValueError("approval execution context identity conflict")
            return existing
        output_root = str(Path(context.output_root).resolve())
        owner = self._output_roots.get(output_root)
        if owner is not None and owner != context.execution_id:
            raise ValueError("approval output root is already bound to another execution")
        self._append(self.contexts_path, context)
        self._contexts[context.execution_id] = context
        self._output_roots[output_root] = context.execution_id
        return context

    def request(self, approval_id: str) -> SensitiveActionApprovalRequest:
        try:
            return self._requests[approval_id]
        except KeyError as exc:
            raise KeyError(f"unknown sensitive-action approval {approval_id}") from exc

    def requests_for_execution(self, execution_id: str) -> tuple[SensitiveActionApprovalRequest, ...]:
        return tuple(
            sorted(
                (item for item in self._requests.values() if item.execution_id == execution_id),
                key=lambda item: (item.created_at, item.approval_id),
            )
        )

    def put_request(self, request: SensitiveActionApprovalRequest) -> SensitiveActionApprovalRequest:
        existing = self._requests.get(request.approval_id)
        if existing is not None:
            if existing.fingerprint != request.fingerprint:
                raise ValueError("sensitive-action approval request identity conflict")
            return existing
        self.context(request.execution_id)
        self._append(self.requests_path, request)
        self._requests[request.approval_id] = request
        return request

    def decision(self, approval_id: str) -> SensitiveActionApprovalDecision | None:
        self.request(approval_id)
        return self._decisions.get(approval_id)

    def decide(self, decision: SensitiveActionApprovalDecision) -> SensitiveActionApprovalDecision:
        self.request(decision.approval_id)
        existing = self._decisions.get(decision.approval_id)
        if existing is not None:
            if existing.decision != decision.decision:
                raise ValueError("sensitive-action approval already has a conflicting decision")
            return existing
        self._append(self.decisions_path, decision)
        self._decisions[decision.approval_id] = decision
        return decision

    def release(self, approval_id: str) -> SensitiveActionApprovalRelease | None:
        self.request(approval_id)
        return self._releases.get(approval_id)

    def consume_release(self, approval_id: str) -> SensitiveActionApprovalRelease:
        request = self.request(approval_id)
        decision = self._decisions.get(approval_id)
        if decision is None or decision.decision != ApprovalDecision.APPROVE:
            raise ValueError("sensitive action cannot release without approval")
        if approval_id in self._releases:
            raise ValueError("sensitive-action approval has already been released")
        release = SensitiveActionApprovalRelease(approval_id=request.approval_id, released_at=_now())
        self._append(self.releases_path, release)
        self._releases[approval_id] = release
        return release

    def _load(self) -> None:
        for path, model, target in (
            (self.contexts_path, ApprovalExecutionContext, self._contexts),
            (self.requests_path, SensitiveActionApprovalRequest, self._requests),
            (self.decisions_path, SensitiveActionApprovalDecision, self._decisions),
            (self.releases_path, SensitiveActionApprovalRelease, self._releases),
        ):
            if not path.exists():
                continue
            for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not raw_line.strip():
                    raise ValueError(f"blank approval ledger row {path.name}:{line_number}")
                raw = json.loads(raw_line)
                stored_fingerprint = str(raw.get("fingerprint", ""))
                item = model.model_validate(raw)
                if stored_fingerprint != item.fingerprint:
                    raise ValueError(f"approval ledger fingerprint mismatch: {path.name}:{line_number}")
                key = item.execution_id if isinstance(item, ApprovalExecutionContext) else item.approval_id
                if key in target:
                    raise ValueError(f"duplicate approval ledger identity: {key}")
                target[key] = item

        for context in self._contexts.values():
            output_root = str(Path(context.output_root).resolve())
            if output_root in self._output_roots:
                raise ValueError("duplicate approval output-root binding")
            self._output_roots[output_root] = context.execution_id
        for request in self._requests.values():
            if request.execution_id not in self._contexts:
                raise ValueError("approval request references unknown execution context")
        for approval_id in self._decisions:
            if approval_id not in self._requests:
                raise ValueError("approval decision references unknown request")
        for approval_id in self._releases:
            decision = self._decisions.get(approval_id)
            if decision is None or decision.decision != ApprovalDecision.APPROVE:
                raise ValueError("approval release lacks an approve decision")

    @staticmethod
    def _append(path: Path, item: BaseModel) -> None:
        payload = _canonical(item.model_dump(mode="json"))
        with path.open("ab") as handle:
            handle.write(payload + b"\n")
            handle.flush()
            os.fsync(handle.fileno())


class SensitiveActionApprovalBroker:
    """Serialize exact approval decisions and wake blocked conversation tool calls."""

    def __init__(self, root: str | Path) -> None:
        self.store = SensitiveActionApprovalStore(root)
        self._condition = threading.Condition(threading.RLock())
        self._interrupted = False

    def register_execution(
        self,
        *,
        execution_id: str,
        project_id: str,
        chat_id: str,
        output_root: str | Path,
        created_at: datetime,
    ) -> ApprovalExecutionContext:
        with self._condition:
            return self.store.put_context(
                ApprovalExecutionContext(
                    execution_id=execution_id,
                    project_id=project_id,
                    chat_id=chat_id,
                    output_root=str(Path(output_root).resolve()),
                    created_at=created_at,
                )
            )

    def context_for_output_root(self, output_root: str | Path) -> ApprovalExecutionContext | None:
        with self._condition:
            return self.store.context_for_output_root(output_root)

    def projections_for_execution(self, execution_id: str) -> tuple[SensitiveActionApprovalProjection, ...]:
        with self._condition:
            self.store.context(execution_id)
            return tuple(self._projection(item) for item in self.store.requests_for_execution(execution_id))

    def projection(self, approval_id: str) -> SensitiveActionApprovalProjection:
        with self._condition:
            return self._projection(self.store.request(approval_id))

    def decide(self, approval_id: str, decision: ApprovalDecision) -> SensitiveActionApprovalProjection:
        with self._condition:
            stored = self.store.decide(
                SensitiveActionApprovalDecision(
                    approval_id=approval_id,
                    decision=decision,
                    decided_at=_now(),
                )
            )
            self._condition.notify_all()
            return self._projection(self.store.request(stored.approval_id))

    def interrupt_waiters(self) -> None:
        with self._condition:
            self._interrupted = True
            self._condition.notify_all()

    def authorize(
        self,
        *,
        context: ApprovalExecutionContext,
        session_id: str,
        proposal: ActionProposal,
        definition: ToolDefinition,
        parsed_input: BaseModel,
    ) -> str | None:
        if not self._sensitive(proposal, parsed_input):
            return None
        action_fingerprint = self._action_fingerprint(proposal)
        approval_id = f"approval_{_sha256({'execution_id': context.execution_id, 'action': action_fingerprint})[:32]}"
        summary, details = self._display_metadata(proposal, parsed_input)
        request = SensitiveActionApprovalRequest(
            approval_id=approval_id,
            execution_id=context.execution_id,
            project_id=context.project_id,
            chat_id=context.chat_id,
            session_id=session_id,
            candidate_id=str(proposal.candidate_id),
            action_fingerprint=action_fingerprint,
            tool_name=definition.spec.name,
            summary=summary,
            details=details,
            created_at=_now(),
        )
        with self._condition:
            request = self.store.put_request(request)
            while True:
                decision = self.store.decision(request.approval_id)
                if decision is not None:
                    if decision.decision == ApprovalDecision.REJECT:
                        return "operator_rejected_sensitive_action"
                    try:
                        self.store.consume_release(request.approval_id)
                    except ValueError as exc:
                        return f"sensitive_action_release_denied:{exc}"
                    self._condition.notify_all()
                    return None
                if self._interrupted:
                    return "sensitive_action_approval_wait_interrupted"
                self._condition.wait(timeout=0.5)

    def _projection(self, request: SensitiveActionApprovalRequest) -> SensitiveActionApprovalProjection:
        decision = self.store.decision(request.approval_id)
        release = self.store.release(request.approval_id)
        if release is not None:
            status = ApprovalStatus.RELEASED
        elif decision is None:
            status = ApprovalStatus.PENDING
        elif decision.decision == ApprovalDecision.APPROVE:
            status = ApprovalStatus.APPROVED
        else:
            status = ApprovalStatus.REJECTED
        return SensitiveActionApprovalProjection(
            approval_id=request.approval_id,
            execution_id=request.execution_id,
            project_id=request.project_id,
            chat_id=request.chat_id,
            session_id=request.session_id,
            candidate_id=request.candidate_id,
            action_fingerprint=request.action_fingerprint,
            tool_name=request.tool_name,
            summary=request.summary,
            details=request.details,
            status=status,
            decision=None if decision is None else decision.decision,
            created_at=request.created_at,
            decided_at=None if decision is None else decision.decided_at,
            released_at=None if release is None else release.released_at,
        )

    @staticmethod
    def _sensitive(proposal: ActionProposal, parsed_input: BaseModel) -> bool:
        if proposal.provenance.source_kind != SourceKind.MODEL:
            return False
        if proposal.tool_name == "process_run":
            return True
        if proposal.tool_name == "workspace_write":
            return bool(getattr(parsed_input, "overwrite", False))
        return False

    @staticmethod
    def _action_fingerprint(proposal: ActionProposal) -> str:
        return _sha256(proposal.model_dump(mode="json"))

    @staticmethod
    def _display_metadata(
        proposal: ActionProposal,
        parsed_input: BaseModel,
    ) -> tuple[str, dict[str, str | int | bool]]:
        if proposal.tool_name == "process_run":
            argv = tuple(str(item) for item in getattr(parsed_input, "argv", ()))
            command = " ".join(argv)
            if len(command) > 1000:
                command = command[:997] + "..."
            cwd = str(getattr(parsed_input, "cwd", "."))[:1000]
            return (
                f"Run local process: {command or '(empty command)'}",
                {"command": command, "cwd": cwd, "argument_count": len(argv)},
            )
        if proposal.tool_name == "workspace_write":
            path = str(getattr(parsed_input, "path", ""))[:1000]
            content = str(getattr(parsed_input, "content", ""))
            return (
                f"Replace existing file contents: {path}",
                {"path": path, "overwrite": True, "content_characters": len(content)},
            )
        return (f"Sensitive action: {proposal.tool_name}", {"tool_name": proposal.tool_name})


class ConversationSensitiveActionGate:
    def __init__(
        self,
        broker: SensitiveActionApprovalBroker,
        context: ApprovalExecutionContext,
        session_id: str,
    ) -> None:
        self.broker = broker
        self.context = context
        self.session_id = session_id

    def authorize(
        self,
        proposal: ActionProposal,
        definition: ToolDefinition,
        parsed_input: BaseModel,
    ) -> str | None:
        return self.broker.authorize(
            context=self.context,
            session_id=self.session_id,
            proposal=proposal,
            definition=definition,
            parsed_input=parsed_input,
        )


_ACTIVE_GATE: ContextVar[ConversationSensitiveActionGate | None] = ContextVar(
    "harness_x_sensitive_action_approval_gate",
    default=None,
)


@contextmanager
def activate_sensitive_action_gate(gate: ConversationSensitiveActionGate):
    token = _ACTIVE_GATE.set(gate)
    try:
        yield
    finally:
        _ACTIVE_GATE.reset(token)


class ApprovalAwareToolExecutor(ToolExecutor):
    """Frozen ToolExecutor ordering plus M72 approval after schema validation, before budget/use."""

    def execute(
        self,
        proposal: ActionProposal,
        *,
        routine_allowed_tools: tuple[str, ...],
        granted_permissions: frozenset[str],
    ) -> ToolResult:
        definition = self.registry.get(proposal.tool_name)
        permission = self.permission_evaluator.evaluate(
            proposal,
            definition,
            routine_allowed_tools=routine_allowed_tools,
            granted_permissions=granted_permissions,
        )
        self.recorder.emit(
            "tool_permission_checked",
            "tools.permission",
            input_refs=(str(proposal.candidate_id),),
            metadata={
                "proposal_tool": proposal.tool_name,
                "decision": permission.model_dump(mode="json"),
            },
        )
        if not permission.allowed:
            status = ToolStatus.NOT_FOUND if permission.reason == "tool_not_registered" else ToolStatus.DENIED
            return self._finish(
                proposal,
                definition,
                status=status,
                error=permission.reason,
                executed=False,
            )
        assert definition is not None

        try:
            parsed_input = definition.input_model.model_validate(proposal.arguments)
        except ValidationError as exc:
            return self._finish(
                proposal,
                definition,
                status=ToolStatus.INVALID_INPUT,
                error=str(exc),
                executed=False,
            )

        gate = _ACTIVE_GATE.get()
        if gate is not None:
            try:
                approval_error = gate.authorize(proposal, definition, parsed_input)
            except Exception as exc:
                approval_error = f"sensitive_action_approval_failed:{type(exc).__name__}:{str(exc)[:900]}"
            if approval_error is not None:
                return self._finish(
                    proposal,
                    definition,
                    status=ToolStatus.DENIED,
                    error=approval_error,
                    executed=False,
                )

        try:
            self.orchestrator.consume_budget(
                BudgetDelta(tool_actions=1),
                reason=f"tool:{definition.spec.name}",
            )
        except Exception as exc:
            return self._finish(
                proposal,
                definition,
                status=ToolStatus.BUDGET_BLOCKED,
                error=str(exc),
                executed=False,
            )

        started = time.perf_counter()
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="harness-x-tool")
        future = pool.submit(definition.handler, parsed_input)
        try:
            raw_output = future.result(timeout=definition.spec.timeout_seconds)
        except FutureTimeoutError:
            duration = (time.perf_counter() - started) * 1000.0
            future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            return self._finish(
                proposal,
                definition,
                status=ToolStatus.TIMEOUT,
                error=(
                    f"tool exceeded {definition.spec.timeout_seconds}s timeout; "
                    "in-process handler cancellation is not guaranteed"
                ),
                duration_ms=duration,
                executed=True,
                execution_may_continue=True,
            )
        except Exception as exc:
            duration = (time.perf_counter() - started) * 1000.0
            pool.shutdown(wait=True, cancel_futures=True)
            return self._finish(
                proposal,
                definition,
                status=ToolStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration,
                executed=True,
            )
        else:
            pool.shutdown(wait=True)

        duration = (time.perf_counter() - started) * 1000.0
        try:
            validated = definition.output_model.model_validate(raw_output)
        except ValidationError as exc:
            return self._finish(
                proposal,
                definition,
                status=ToolStatus.INVALID_OUTPUT,
                error=str(exc),
                duration_ms=duration,
                executed=True,
            )
        return self._finish(
            proposal,
            definition,
            status=ToolStatus.SUCCEEDED,
            output=validated.model_dump(mode="json"),
            duration_ms=duration,
            executed=True,
        )
