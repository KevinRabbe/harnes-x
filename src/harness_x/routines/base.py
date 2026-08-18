"""Versioned routine contracts and execution authority boundaries."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.core.errors import HarnessError
from harness_x.core.ids import RoutineId
from harness_x.gates import ComputeGate, FocusGate, MaintenanceGate, RetrievalGate, WriteGate
from harness_x.memory import EpisodicMemory, ErrorBuffer, GoalMemory, MemoryClass, WorkingState
from harness_x.orchestrator import OperatingMode, TaskOrchestrator

if TYPE_CHECKING:
    from .engine import RoutineEngine
    from .scripted import ScriptedReasoningStub


class RoutineError(HarnessError):
    """A routine cannot execute under the current contracts or authority."""


class RoutineStatus(StrEnum):
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"


class RoutineSpec(BaseModel):
    """Immutable description of how one routine may operate."""

    model_config = ConfigDict(frozen=True)

    routine_id: RoutineId
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    precondition_modes: tuple[OperatingMode, ...]
    required_state_views: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    allowed_memory_writes: tuple[MemoryClass, ...] = ()
    step_policy: tuple[str, ...] = ()
    verification_requirements: tuple[str, ...] = ()
    termination_rule: str = Field(min_length=1)

    @field_validator("name", "version", "termination_rule")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("routine text fields cannot be blank")
        return value


class RoutineResult(BaseModel):
    """Structured result returned by a routine implementation."""

    model_config = ConfigDict(frozen=True)

    status: RoutineStatus
    output_refs: tuple[str, ...] = ()
    data: dict[str, Any] = Field(default_factory=dict)


class RoutineExecution(BaseModel):
    """Trace-linked result of one routine invocation."""

    model_config = ConfigDict(frozen=True)

    routine_id: RoutineId
    routine_name: str
    routine_version: str
    request_fingerprint: str = Field(min_length=64, max_length=64)
    started_step: int = Field(ge=1)
    finished_step: int = Field(ge=1)
    result: RoutineResult


@dataclass
class RoutineBindings:
    """Authoritative owners/controllers available to scripted routines."""

    orchestrator: TaskOrchestrator
    goals: GoalMemory
    working: WorkingState
    episodic: EpisodicMemory
    errors: ErrorBuffer
    retrieval_gate: RetrievalGate
    write_gate: WriteGate
    focus_gate: FocusGate
    compute_gate: ComputeGate
    maintenance_gate: MaintenanceGate
    reasoning_stub: "ScriptedReasoningStub"

    def __post_init__(self) -> None:
        recorders = {
            id(self.orchestrator.recorder),
            id(self.goals.recorder),
            id(self.working.recorder),
            id(self.episodic.recorder),
            id(self.errors.recorder),
            id(self.retrieval_gate.recorder),
            id(self.write_gate.recorder),
            id(self.focus_gate.recorder),
            id(self.compute_gate.recorder),
            id(self.maintenance_gate.recorder),
        }
        if len(recorders) != 1:
            raise RoutineError("routine bindings must share one TraceRecorder")


def routine_request_fingerprint(request: BaseModel) -> str:
    payload = request.model_dump(mode="json")
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class RoutineExecutionContext:
    """Authority-scoped view passed to one active routine."""

    def __init__(
        self,
        engine: "RoutineEngine",
        bindings: RoutineBindings,
        spec: RoutineSpec,
        *,
        started_step: int,
    ) -> None:
        self.engine = engine
        self.bindings = bindings
        self.spec = spec
        self.started_step = started_step

    @property
    def recorder(self):
        return self.bindings.orchestrator.recorder

    def require_memory_write(self, memory_class: MemoryClass) -> None:
        if memory_class not in self.spec.allowed_memory_writes:
            raise RoutineError(
                f"routine {self.spec.name} may not write {memory_class.value} memory"
            )

    def require_tool(self, tool_name: str) -> None:
        if tool_name not in self.spec.allowed_tools:
            raise RoutineError(
                f"routine {self.spec.name} may not use undeclared tool {tool_name!r}"
            )

    def invoke(self, routine_name: str, request: BaseModel) -> RoutineExecution:
        return self.engine.execute(routine_name, request)


class ScriptedRoutine(ABC):
    spec: RoutineSpec
    request_type: type[BaseModel]

    @abstractmethod
    def run(
        self,
        context: RoutineExecutionContext,
        request: BaseModel,
    ) -> RoutineResult:
        raise NotImplementedError
