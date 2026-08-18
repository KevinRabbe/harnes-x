"""Benchmark-only routines that stress the real Harness X control boundaries."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.core.contracts import Observation
from harness_x.core.events import EventType
from harness_x.core.ids import EventId, GoalId, MemoryId, RoutineId
from harness_x.core.provenance import VerificationState
from harness_x.gates import (
    ComputeRequest,
    FocusCandidate,
    FocusRequest,
    MaintenanceRequest,
    RetrievalRequest,
    WriteRequest,
)
from harness_x.memory import EpisodeOutcome, ErrorSeverity, GoalStatus, MemoryClass
from harness_x.orchestrator import BudgetDelta, OperatingMode
from harness_x.routines.base import (
    RoutineError,
    RoutineExecutionContext,
    RoutineResult,
    RoutineSpec,
    RoutineStatus,
    ScriptedRoutine,
)
from harness_x.routines.scripted import VerificationRoutineRequest


class BenchmarkStepRequest(BaseModel):
    """One deterministic long-horizon action without completing the root task."""

    model_config = ConfigDict(frozen=True)

    goal_id: GoalId
    step_key: str = Field(min_length=1)
    dependencies: tuple[str, ...] = ()
    observation: Observation
    tool_name: str = Field(min_length=1)
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    expected_result: dict[str, Any] = Field(default_factory=dict)
    required_result_keys: tuple[str, ...] = ()
    observation_priority: float = Field(default=0.55, ge=0.0, le=1.0)
    observation_size_units: int = Field(default=1, gt=0)
    repeated_failure_count: int = Field(default=0, ge=0)

    @field_validator("step_key", "tool_name")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("benchmark step text cannot be blank")
        return value


class BenchmarkStepRoutine(ScriptedRoutine):
    """Repeatable task step using the production gates/tool/verifier boundaries."""

    spec = RoutineSpec(
        routine_id=RoutineId(value="routine_benchmark_step_v1"),
        name="benchmark_step",
        version="benchmark-step-v1",
        precondition_modes=(OperatingMode.TASK_ACTIVE,),
        required_state_views=(
            "active_goal",
            "working_state",
            "episodic_memory",
            "error_buffer",
            "budget",
            "tool_registry",
        ),
        allowed_tools=("calculator", "kv_read", "sandbox_write", "unreliable"),
        allowed_memory_writes=(
            MemoryClass.WORKING,
            MemoryClass.EPISODIC,
            MemoryClass.ERROR,
        ),
        step_policy=(
            "check_dependencies",
            "observe",
            "update_working",
            "check_maintenance",
            "focus",
            "budget",
            "propose",
            "authorize",
            "execute",
            "verify",
            "store_episode",
        ),
        verification_requirements=("tool_schema_valid", "verification_routine_accepts"),
        termination_rule="return to TASK_ACTIVE after a verified step or enter recoverable state",
    )
    request_type = BenchmarkStepRequest

    def run(
        self,
        context: RoutineExecutionContext,
        request: BenchmarkStepRequest,
    ) -> RoutineResult:
        b = context.bindings
        session = b.orchestrator.session
        if request.observation.task_id != session.task_id:
            raise RoutineError("benchmark observation belongs to another task")

        goal = b.goals.retrieve(request.goal_id)
        if goal.task_id != session.task_id or goal.status != GoalStatus.ACTIVE:
            raise RoutineError("benchmark step requires the active root goal")

        retrieval = b.retrieval_gate.evaluate(
            RetrievalRequest(
                current_routine="benchmark_step",
                unresolved_entities=request.dependencies,
                uncertainty=bool(request.dependencies),
                working_pressure=min(1.0, b.working.pressure.pressure),
                recent_retrieval_count=0,
                query=request.step_key,
            )
        )
        missing: list[str] = []
        dependency_refs: list[str] = []
        if request.dependencies:
            if not retrieval.decision.get("retrieve"):
                raise RoutineError("dependency check was suppressed by the retrieval gate")
            for dependency in request.dependencies:
                matches = b.episodic.search(
                    tags=("benchmark_step", dependency, EpisodeOutcome.SUCCESS.value),
                    limit=1,
                )
                if not matches:
                    missing.append(dependency)
                else:
                    dependency_refs.append(str(matches[0].memory_id))
        if missing:
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                data={"reason": "unmet_dependency", "missing": missing},
            )

        observed = context.recorder.emit(
            EventType.OBSERVATION_RECEIVED,
            "routine.benchmark_step",
            input_refs=(str(request.goal_id), *dependency_refs),
            metadata={
                "step_key": request.step_key,
                "kind": request.observation.kind,
                "content": request.observation.content,
                "provenance": request.observation.provenance.model_dump(mode="json"),
            },
        )
        write = b.write_gate.evaluate(
            WriteRequest(
                accepted=True,
                kind="observation",
                source_ref=str(observed.event_id),
            )
        )
        if write.decision.get("memory_class") != MemoryClass.WORKING.value:
            raise RoutineError("benchmark observation must route to working memory")
        context.require_memory_write(MemoryClass.WORKING)
        working_item = b.working.add(
            kind=request.observation.kind,
            content={
                **request.observation.content,
                "benchmark_step_key": request.step_key,
            },
            priority=request.observation_priority,
            size_units=request.observation_size_units,
            source=str(observed.event_id),
            provenance=request.observation.provenance,
        )

        unresolved_count = len(b.errors.unresolved())
        maintenance = b.maintenance_gate.evaluate(
            MaintenanceRequest(
                working_pressure=min(1.0, b.working.pressure.pressure),
                unresolved_error_count=unresolved_count,
                repeated_failure_count=request.repeated_failure_count,
            )
        )
        if maintenance.decision.get("trigger"):
            b.orchestrator.enter_maintenance("benchmark_maintenance_gate_triggered")
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                output_refs=(str(working_item.memory_id),),
                data={
                    "reason": "maintenance_required",
                    "maintenance": maintenance.decision,
                },
            )

        focus = b.focus_gate.evaluate(
            FocusRequest(
                candidates=tuple(
                    FocusCandidate(
                        memory_id=item.memory_id,
                        priority=item.priority,
                        pinned=item.pinned,
                        created_step=item.created_step,
                        last_used_step=item.last_used_step,
                    )
                    for item in b.working.items()
                )
            )
        )
        for memory_id in focus.decision.get("proposed_pin_ids", []):
            context.require_memory_write(MemoryClass.WORKING)
            b.working.set_pinned(
                MemoryId(value=memory_id),
                True,
                reason="benchmark_focus_gate_proposal",
            )

        requested_budget = BudgetDelta(reasoning_steps=1)
        compute = b.compute_gate.evaluate(
            ComputeRequest(
                budget=b.orchestrator.session.budget,
                usage=b.orchestrator.session.usage,
                requested=requested_budget,
            )
        )
        if compute.decision.get("action") == "suspend":
            b.orchestrator.suspend("benchmark_compute_budget_exhausted")
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                data={"reason": "budget_exhausted"},
            )
        if compute.decision.get("action") == "stop":
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                data={"reason": compute.decision.get("reason", "compute_gate_stop")},
            )
        b.orchestrator.consume_budget(
            requested_budget,
            reason=f"benchmark_step:{request.step_key}",
        )

        proposal = b.reasoning_stub.propose_action(
            task_id=session.task_id,
            action_name=request.tool_name,
            arguments=request.tool_arguments,
            provenance=request.observation.provenance,
        )
        context.recorder.emit(
            EventType.ACTION_PROPOSED,
            "routine.benchmark_step.reasoning_stub",
            input_refs=(str(request.goal_id), *dependency_refs),
            output_refs=(str(proposal.candidate_id),),
            metadata={
                "step_key": request.step_key,
                "proposal": proposal.model_dump(mode="json"),
                "model_inference": False,
            },
        )

        tool_result = context.execute_tool(proposal)
        if not tool_result.succeeded:
            return self._record_failure(
                context,
                request,
                candidate_id=str(proposal.candidate_id),
                reason="tool_execution_failed",
                detail=tool_result.error or tool_result.status.value,
                tool_status=tool_result.status.value,
            )

        tool_observation = b.tool_executor.observation_from(tool_result)
        tool_event = context.recorder.emit(
            EventType.OBSERVATION_RECEIVED,
            "routine.benchmark_step.tool_result",
            input_refs=(str(proposal.candidate_id),),
            metadata={
                "step_key": request.step_key,
                "kind": tool_observation.kind,
                "content": tool_observation.content,
                "provenance": tool_observation.provenance.model_dump(mode="json"),
            },
        )

        b.orchestrator.enter_verification(f"benchmark_verify:{request.step_key}")
        verification_execution = context.invoke(
            "verification",
            VerificationRoutineRequest(
                candidate_id=proposal.candidate_id,
                actual=tool_result.output,
                expected=request.expected_result,
                provenance=tool_observation.provenance,
                required_keys=request.required_result_keys,
            ),
        )
        verification_data = verification_execution.result.data
        verification_event_id = EventId(
            value=str(verification_data["verification_event_id"])
        )
        if not bool(verification_data["accepted"]):
            return self._record_failure(
                context,
                request,
                candidate_id=str(proposal.candidate_id),
                reason="verification_failed",
                detail=str(verification_data["verification"].get("reason")),
                tool_status=tool_result.status.value,
                verification_event_id=verification_event_id,
            )

        b.orchestrator.transition(
            OperatingMode.TASK_ACTIVE,
            f"benchmark_verified:{request.step_key}",
        )
        verified_provenance = tool_observation.provenance.model_copy(
            update={"verification": VerificationState.VERIFIED}
        )
        episode = self._store_episode(
            context,
            request,
            outcome=EpisodeOutcome.SUCCESS,
            summary=f"benchmark step {request.step_key} succeeded",
            provenance=verified_provenance,
            metadata={
                "step_key": request.step_key,
                "candidate_id": str(proposal.candidate_id),
                "tool_name": request.tool_name,
                "tool_status": tool_result.status.value,
                "tool_observation_event_id": str(tool_event.event_id),
                "verification_event_id": str(verification_event_id),
            },
        )
        return RoutineResult(
            status=RoutineStatus.SUCCEEDED,
            output_refs=(str(episode.memory_id), str(proposal.candidate_id)),
            data={
                "step_key": request.step_key,
                "episode_memory_id": str(episode.memory_id),
                "tool_result": tool_result.model_dump(mode="json"),
                "verification_event_id": str(verification_event_id),
            },
        )

    def _record_failure(
        self,
        context: RoutineExecutionContext,
        request: BenchmarkStepRequest,
        *,
        candidate_id: str,
        reason: str,
        detail: str,
        tool_status: str,
        verification_event_id: EventId | None = None,
    ) -> RoutineResult:
        b = context.bindings
        source_event = (
            verification_event_id
            if verification_event_id is not None
            else context.recorder.store.events(trace_id=context.recorder.trace_id)[-1].event_id
        )
        context.require_memory_write(MemoryClass.ERROR)
        error = b.errors.record(
            anomaly=f"benchmark step {request.step_key}: {reason}",
            source_event_id=source_event,
            severity=ErrorSeverity.ERROR,
            provenance=request.observation.provenance,
        )
        episode = self._store_episode(
            context,
            request,
            outcome=EpisodeOutcome.FAILURE,
            summary=f"benchmark step {request.step_key} failed: {reason}",
            provenance=request.observation.provenance,
            metadata={
                "step_key": request.step_key,
                "candidate_id": candidate_id,
                "tool_name": request.tool_name,
                "tool_status": tool_status,
                "failure_reason": reason,
                "detail": detail,
                "verification_event_id": (
                    str(verification_event_id)
                    if verification_event_id is not None
                    else None
                ),
            },
        )
        if b.orchestrator.session.mode != OperatingMode.SUSPENDED:
            if b.orchestrator.session.mode in {
                OperatingMode.VERIFY,
                OperatingMode.TASK_ACTIVE,
            }:
                b.orchestrator.enter_recovery(reason)
        return RoutineResult(
            status=RoutineStatus.BLOCKED,
            output_refs=(str(error.memory_id), str(episode.memory_id), candidate_id),
            data={
                "reason": reason,
                "detail": detail,
                "error_memory_id": str(error.memory_id),
                "episode_memory_id": str(episode.memory_id),
            },
        )

    def _store_episode(
        self,
        context: RoutineExecutionContext,
        request: BenchmarkStepRequest,
        *,
        outcome: EpisodeOutcome,
        summary: str,
        provenance,
        metadata: dict[str, Any],
    ):
        b = context.bindings
        write = b.write_gate.evaluate(
            WriteRequest(
                accepted=True,
                kind="outcome",
                source_ref=request.step_key,
                verification_ref=metadata.get("verification_event_id"),
            )
        )
        if write.decision.get("memory_class") != MemoryClass.EPISODIC.value:
            raise RoutineError("benchmark outcomes must route to episodic memory")
        context.require_memory_write(MemoryClass.EPISODIC)
        end_step = context.recorder.store.next_step(context.recorder.trace_id) - 1
        return b.episodic.record(
            start_step=context.started_step,
            end_step=end_step,
            summary=summary,
            outcome=outcome,
            tags=("benchmark_step", request.step_key, outcome.value),
            entities=request.dependencies,
            metadata=metadata,
            provenance=provenance,
        )


class BenchmarkMaintenanceRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_pressure: float = Field(default=0.50, ge=0.0, le=1.0)


class BenchmarkMaintenanceRoutine(ScriptedRoutine):
    """Deterministically clears only unpinned working state until pressure is safe."""

    spec = RoutineSpec(
        routine_id=RoutineId(value="routine_benchmark_maintenance_v1"),
        name="benchmark_maintenance",
        version="benchmark-maintenance-v1",
        precondition_modes=(OperatingMode.MAINTENANCE,),
        required_state_views=("working_state",),
        allowed_tools=(),
        allowed_memory_writes=(MemoryClass.WORKING,),
        step_policy=("rank_unpinned", "evict_until_target", "resume_task"),
        verification_requirements=("pinned_state_survives",),
        termination_rule="return to TASK_ACTIVE only after working pressure is at target",
    )
    request_type = BenchmarkMaintenanceRequest

    def run(
        self,
        context: RoutineExecutionContext,
        request: BenchmarkMaintenanceRequest,
    ) -> RoutineResult:
        working = context.bindings.working
        removed: list[str] = []
        candidates = sorted(
            (item for item in working.items() if not item.pinned),
            key=lambda item: (
                item.priority,
                item.last_used_step,
                item.created_step,
                str(item.memory_id),
            ),
        )
        for item in candidates:
            if working.pressure.pressure <= request.target_pressure:
                break
            context.require_memory_write(MemoryClass.WORKING)
            working.remove(
                item.memory_id,
                reason="benchmark_maintenance_pressure_relief",
            )
            removed.append(str(item.memory_id))

        if working.pressure.pressure > request.target_pressure:
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                output_refs=tuple(removed),
                data={
                    "reason": "pinned_state_prevents_pressure_relief",
                    "pressure": working.pressure.pressure,
                },
            )
        context.bindings.orchestrator.transition(
            OperatingMode.TASK_ACTIVE,
            "benchmark_maintenance_complete",
        )
        return RoutineResult(
            status=RoutineStatus.SUCCEEDED,
            output_refs=tuple(removed),
            data={
                "removed": removed,
                "pressure": working.pressure.pressure,
            },
        )
