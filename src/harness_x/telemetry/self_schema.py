"""Ground-truth self-schema generated from authoritative Harness X state."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from harness_x.config import HarnessConfig
from harness_x.core.events import EventType
from harness_x.memory import (
    EpisodicMemory,
    ErrorBuffer,
    ErrorStatus,
    GoalMemory,
    ProceduralMemory,
    SemanticMemory,
    WorkingState,
)
from harness_x.orchestrator import TaskOrchestrator
from harness_x.routines import RoutineEngine
from harness_x.tools import ToolRegistry

from .metrics import MetricsSample, RuntimeMetrics, derive_runtime_metrics
from .trace_store import TraceRecorder


class ComponentSelfDescription(BaseModel):
    model_config = ConfigDict(frozen=True)
    component: str
    kind: str
    version: str


class MemorySelfDescription(BaseModel):
    model_config = ConfigDict(frozen=True)
    memory_class: str
    item_count: int = Field(ge=0)
    capacity_units: int | None = Field(default=None, gt=0)
    used_units: int | None = Field(default=None, ge=0)
    utilization: float | None = Field(default=None, ge=0.0, le=1.0)
    pressure: float | None = Field(default=None, ge=0.0, le=1.0)
    unresolved_count: int = Field(default=0, ge=0)
    contradiction_count: int = Field(default=0, ge=0)


class GateSelfDescription(BaseModel):
    model_config = ConfigDict(frozen=True)
    gate_id: str
    policy_version: str
    configuration: dict[str, Any]


class ToolSelfDescription(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    version: str
    permissions: tuple[str, ...]
    side_effect_level: str
    cost_class: str
    timeout_seconds: float
    idempotent: bool


class SystemSelfSchema(BaseModel):
    """Machine-readable description of what the running system actually contains."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "system-self-schema-v1"
    generated_at: str
    system_version: str
    task_id: str
    trace_id: str
    operating_mode: str
    active_routine: str | None
    budget: dict[str, Any]
    budget_usage: dict[str, Any]
    components: tuple[ComponentSelfDescription, ...]
    memories: tuple[MemorySelfDescription, ...]
    gates: tuple[GateSelfDescription, ...]
    tools: tuple[ToolSelfDescription, ...]
    granted_permissions: tuple[str, ...]
    recent_errors: tuple[dict[str, Any], ...]
    metrics: RuntimeMetrics
    known_limitations: tuple[str, ...]
    state_fingerprint: str


class SelfSchemaBuilder:
    """Builds self-knowledge without model inference or free-form introspection."""

    MEMORY_VERSIONS = {
        "goal": "goal-v1",
        "working": "working-v1",
        "episodic": "episodic-v1",
        "error": "error-v1",
        "semantic": "semantic-v1",
        "procedural": "procedural-v1",
    }

    def __init__(
        self,
        *,
        config: HarnessConfig,
        recorder: TraceRecorder,
        orchestrator: TaskOrchestrator,
        goals: GoalMemory,
        working: WorkingState,
        episodic: EpisodicMemory,
        errors: ErrorBuffer,
        semantic: SemanticMemory,
        procedural: ProceduralMemory,
        engine: RoutineEngine,
        registry: ToolRegistry,
        granted_permissions: frozenset[str],
        known_limitations: tuple[str, ...] = (),
    ) -> None:
        self.config = config
        self.recorder = recorder
        self.orchestrator = orchestrator
        self.goals = goals
        self.working = working
        self.episodic = episodic
        self.errors = errors
        self.semantic = semantic
        self.procedural = procedural
        self.engine = engine
        self.registry = registry
        self.granted_permissions = granted_permissions
        self.known_limitations = known_limitations

    def build(self) -> SystemSelfSchema:
        events = self.recorder.store.events(trace_id=self.recorder.trace_id)
        metrics = derive_runtime_metrics(
            events,
            working=self.working,
            errors=self.errors,
            semantic=self.semantic,
        )
        active_routine = self._active_routine(events)
        memories = self._memories()
        gates = self._gates()
        tools = tuple(
            ToolSelfDescription(
                name=spec.name,
                version=spec.version,
                permissions=spec.permissions,
                side_effect_level=spec.side_effect_level.value,
                cost_class=spec.cost_class,
                timeout_seconds=spec.timeout_seconds,
                idempotent=spec.idempotent,
            )
            for spec in self.registry.specs()
        )
        components = self._components(gates, tools)
        recent_errors = tuple(
            {
                "memory_id": str(record.memory_id),
                "severity": record.severity.value,
                "status": record.status.value,
                "anomaly": record.anomaly,
                "revision": record.revision,
            }
            for record in self.errors.all()[-10:]
        )
        payload = {
            "system_version": str(self.orchestrator.session.system_version),
            "task_id": str(self.orchestrator.session.task_id),
            "trace_id": str(self.orchestrator.session.trace_id),
            "operating_mode": self.orchestrator.session.mode.value,
            "active_routine": active_routine,
            "budget": self.orchestrator.session.budget.model_dump(mode="json"),
            "budget_usage": self.orchestrator.session.usage.model_dump(mode="json"),
            "components": [item.model_dump(mode="json") for item in components],
            "memories": [item.model_dump(mode="json") for item in memories],
            "gates": [item.model_dump(mode="json") for item in gates],
            "tools": [item.model_dump(mode="json") for item in tools],
            "granted_permissions": sorted(self.granted_permissions),
            "recent_errors": list(recent_errors),
            "metrics": metrics.model_dump(mode="json"),
            "known_limitations": list(self.known_limitations),
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        fingerprint = hashlib.sha256(canonical).hexdigest()
        return SystemSelfSchema(
            generated_at=self.recorder.clock.now().isoformat(),
            state_fingerprint=fingerprint,
            **payload,
        )

    def metrics_sample(self) -> MetricsSample:
        schema = self.build()
        events = self.recorder.store.events(trace_id=self.recorder.trace_id)
        return MetricsSample(
            system_version=schema.system_version,
            task_id=schema.task_id,
            trace_id=schema.trace_id,
            step=events[-1].step if events else 0,
            timestamp=schema.generated_at,
            metrics=schema.metrics,
        )

    def _memories(self) -> tuple[MemorySelfDescription, ...]:
        unresolved = sum(
            record.status in {ErrorStatus.OPEN, ErrorStatus.INVESTIGATING}
            for record in self.errors.all()
        )
        pressure = self.working.pressure
        values = (
            MemorySelfDescription(memory_class="goal", item_count=len(self.goals.all())),
            MemorySelfDescription(
                memory_class="working",
                item_count=len(self.working.items()),
                capacity_units=self.working.capacity_units,
                used_units=self.working.used_units,
                utilization=min(1.0, self.working.used_units / self.working.capacity_units),
                pressure=min(1.0, pressure.pressure),
            ),
            MemorySelfDescription(memory_class="episodic", item_count=len(self.episodic.all())),
            MemorySelfDescription(
                memory_class="error",
                item_count=len(self.errors.all()),
                unresolved_count=unresolved,
            ),
            MemorySelfDescription(
                memory_class="semantic",
                item_count=len(self.semantic.all()),
                contradiction_count=len(self.semantic.contradictions()),
            ),
            MemorySelfDescription(memory_class="procedural", item_count=len(self.procedural.all())),
        )
        return values

    def _gates(self) -> tuple[GateSelfDescription, ...]:
        gates = (
            ("retrieval", self.config.gates.retrieval),
            ("write", self.config.gates.write),
            ("focus", self.config.gates.focus),
            ("compute", self.config.gates.compute),
            ("maintenance", self.config.gates.maintenance),
        )
        return tuple(
            GateSelfDescription(
                gate_id=name,
                policy_version=value.policy_version,
                configuration=value.model_dump(mode="json"),
            )
            for name, value in gates
        )

    def _components(
        self,
        gates: tuple[GateSelfDescription, ...],
        tools: tuple[ToolSelfDescription, ...],
    ) -> tuple[ComponentSelfDescription, ...]:
        result = [
            ComponentSelfDescription(component="orchestrator", kind="controller", version="orchestrator-v1"),
            ComponentSelfDescription(component="trace", kind="telemetry", version="trace-v1"),
        ]
        result.extend(
            ComponentSelfDescription(
                component=f"memory.{name}", kind="memory", version=version
            )
            for name, version in sorted(self.MEMORY_VERSIONS.items())
        )
        result.extend(
            ComponentSelfDescription(
                component=f"gate.{gate.gate_id}", kind="gate", version=gate.policy_version
            )
            for gate in gates
        )
        result.extend(
            ComponentSelfDescription(
                component=f"routine.{spec.name}", kind="routine", version=spec.version
            )
            for spec in self.engine.specs()
        )
        result.extend(
            ComponentSelfDescription(
                component=f"tool.{tool.name}", kind="tool", version=tool.version
            )
            for tool in tools
        )
        return tuple(sorted(result, key=lambda item: item.component))

    @staticmethod
    def _active_routine(events) -> str | None:
        stack: list[tuple[str, str]] = []
        for event in events:
            if event.event_type == EventType.ROUTINE_STARTED:
                routine_id = str(event.metadata.get("routine_id", ""))
                routine_name = str(event.metadata.get("routine_name", ""))
                stack.append((routine_id, routine_name))
            elif event.event_type == EventType.ROUTINE_FINISHED:
                routine_id = str(event.metadata.get("routine_id", ""))
                for index in range(len(stack) - 1, -1, -1):
                    if stack[index][0] == routine_id:
                        del stack[index]
                        break
        return stack[-1][1] if stack else None
