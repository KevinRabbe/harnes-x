"""Declared tool registry, permission evaluation, and normalized execution boundary."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from harness_x.core.contracts import ActionProposal, Observation
from harness_x.core.events import EventType
from harness_x.core.provenance import Provenance, SourceKind, VerificationState
from harness_x.orchestrator import BudgetDelta, TaskOrchestrator
from harness_x.telemetry import TraceRecorder


class SideEffectLevel(StrEnum):
    NONE = "none"
    REVERSIBLE = "reversible"
    PERSISTENT = "persistent"


class ToolStatus(StrEnum):
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    NOT_FOUND = "not_found"
    INVALID_INPUT = "invalid_input"
    TIMEOUT = "timeout"
    FAILED = "failed"
    INVALID_OUTPUT = "invalid_output"
    BUDGET_BLOCKED = "budget_blocked"


class ToolSpec(BaseModel):
    """Portable declaration of one externally callable capability."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permissions: tuple[str, ...] = ()
    side_effect_level: SideEffectLevel = SideEffectLevel.NONE
    cost_class: str = Field(default="low", min_length=1)
    timeout_seconds: float = Field(default=5.0, gt=0.0)
    idempotent: bool = True

    @field_validator("name", "version", "cost_class")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("tool text fields cannot be blank")
        return value

    @field_validator("permissions")
    @classmethod
    def normalize_permissions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("tool permissions cannot contain blanks")
        if len(normalized) != len(set(normalized)):
            raise ValueError("tool permissions must be unique")
        return normalized


class PermissionDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    tool_name: str
    routine_allows: bool
    required_permissions: tuple[str, ...] = ()
    missing_permissions: tuple[str, ...] = ()
    reason: str


class ToolResult(BaseModel):
    """Normalized result; raw handler values never cross the boundary directly."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    tool_name: str
    tool_version: str | None = None
    status: ToolStatus
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: float = Field(default=0.0, ge=0.0)
    side_effect_level: SideEffectLevel | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == ToolStatus.SUCCEEDED


ToolHandler = Callable[[BaseModel], BaseModel | dict[str, Any]]


@dataclass(frozen=True)
class ToolDefinition:
    spec: ToolSpec
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler

    def __post_init__(self) -> None:
        if self.spec.input_schema != self.input_model.model_json_schema():
            raise ValueError(f"input schema mismatch for tool {self.spec.name}")
        if self.spec.output_schema != self.output_model.model_json_schema():
            raise ValueError(f"output schema mismatch for tool {self.spec.name}")


class ToolRegistry:
    """Authoritative declaration registry; names are unique and version is explicit."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        name = definition.spec.name
        if name in self._definitions:
            raise ValueError(f"tool {name!r} is already registered")
        self._definitions[name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def require(self, name: str) -> ToolDefinition:
        definition = self.get(name)
        if definition is None:
            raise KeyError(f"tool {name!r} is not registered")
        return definition

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._definitions[name].spec for name in sorted(self._definitions))


class ToolPermissionEvaluator:
    """Pure permission decision: proposal does not imply execution authority."""

    def evaluate(
        self,
        proposal: ActionProposal,
        definition: ToolDefinition | None,
        *,
        routine_allowed_tools: tuple[str, ...],
        granted_permissions: frozenset[str],
    ) -> PermissionDecision:
        if definition is None:
            return PermissionDecision(
                allowed=False,
                tool_name=proposal.tool_name,
                routine_allows=False,
                reason="tool_not_registered",
            )
        routine_allows = proposal.tool_name in routine_allowed_tools
        missing = tuple(
            permission
            for permission in definition.spec.permissions
            if permission not in granted_permissions
        )
        if not routine_allows:
            reason = "routine_did_not_declare_tool"
        elif missing:
            reason = "missing_permission"
        else:
            reason = "allowed"
        return PermissionDecision(
            allowed=routine_allows and not missing,
            tool_name=proposal.tool_name,
            routine_allows=routine_allows,
            required_permissions=definition.spec.permissions,
            missing_permissions=missing,
            reason=reason,
        )


class ToolExecutor:
    """Permissioned execution boundary with schema checks, budgets, and trace events."""

    def __init__(
        self,
        registry: ToolRegistry,
        recorder: TraceRecorder,
        orchestrator: TaskOrchestrator,
        *,
        permission_evaluator: ToolPermissionEvaluator | None = None,
    ) -> None:
        if recorder is not orchestrator.recorder:
            raise ValueError("tool executor and orchestrator must share one TraceRecorder")
        self.registry = registry
        self.recorder = recorder
        self.orchestrator = orchestrator
        self.permission_evaluator = permission_evaluator or ToolPermissionEvaluator()

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
            EventType.TOOL_PERMISSION_CHECKED,
            "tools.permission",
            input_refs=(str(proposal.candidate_id),),
            metadata={
                "proposal_tool": proposal.tool_name,
                "decision": permission.model_dump(mode="json"),
            },
        )
        if not permission.allowed:
            status = (
                ToolStatus.NOT_FOUND
                if permission.reason == "tool_not_registered"
                else ToolStatus.DENIED
            )
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
        try:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="harness-x-tool") as pool:
                future = pool.submit(definition.handler, parsed_input)
                raw_output = future.result(timeout=definition.spec.timeout_seconds)
        except FutureTimeoutError:
            duration = (time.perf_counter() - started) * 1000.0
            return self._finish(
                proposal,
                definition,
                status=ToolStatus.TIMEOUT,
                error=f"tool exceeded {definition.spec.timeout_seconds}s timeout",
                duration_ms=duration,
                executed=True,
            )
        except Exception as exc:
            duration = (time.perf_counter() - started) * 1000.0
            return self._finish(
                proposal,
                definition,
                status=ToolStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration,
                executed=True,
            )

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

    def observation_from(self, result: ToolResult) -> Observation:
        if not result.succeeded:
            raise ValueError("only successful tool results can become observations")
        provenance = Provenance(
            source_kind=SourceKind.TOOL,
            source_ref=f"tool:{result.tool_name}@{result.tool_version}",
            created_at=self.recorder.clock.now(),
            system_version=self.recorder.system_version,
            trace_id=self.recorder.trace_id,
            verification=VerificationState.UNVERIFIED,
        )
        return Observation(
            task_id=self.recorder.task_id,
            kind=f"tool_result:{result.tool_name}",
            content=result.output,
            provenance=provenance,
        )

    def _finish(
        self,
        proposal: ActionProposal,
        definition: ToolDefinition | None,
        *,
        status: ToolStatus,
        output: dict[str, Any] | None = None,
        error: str | None = None,
        duration_ms: float = 0.0,
        executed: bool,
    ) -> ToolResult:
        result = ToolResult(
            candidate_id=str(proposal.candidate_id),
            tool_name=proposal.tool_name,
            tool_version=definition.spec.version if definition else None,
            status=status,
            output=output or {},
            error=error,
            duration_ms=duration_ms,
            side_effect_level=(definition.spec.side_effect_level if definition else None),
        )
        self.recorder.emit(
            EventType.TOOL_EXECUTION_FINISHED,
            "tools.executor",
            input_refs=(str(proposal.candidate_id),),
            output_refs=(str(proposal.candidate_id),),
            metadata={
                "executed": executed,
                "result": result.model_dump(mode="json"),
            },
        )
        if executed:
            self.recorder.emit(
                EventType.ACTION_EXECUTED,
                f"tool.{proposal.tool_name}",
                input_refs=(str(proposal.candidate_id),),
                output_refs=(str(proposal.candidate_id),),
                metadata={
                    "tool_name": proposal.tool_name,
                    "tool_version": result.tool_version,
                    "status": status.value,
                    "side_effect_level": (
                        result.side_effect_level.value if result.side_effect_level else None
                    ),
                    "external_side_effect": (
                        result.side_effect_level not in {None, SideEffectLevel.NONE}
                    ),
                },
            )
        return result
