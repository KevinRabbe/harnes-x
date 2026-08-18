"""Milestone 6 task routine using the declared tool execution boundary."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.core.contracts import ActionProposal, Observation
from harness_x.core.events import EventType
from harness_x.core.ids import CandidateId, EventId, GoalId, MemoryId, RoutineId, TaskId
from harness_x.core.provenance import Provenance
from harness_x.gates import (
    ComputeRequest,
    FocusCandidate,
    FocusRequest,
    MaintenanceRequest,
    RetrievalRequest,
    WriteRequest,
)
from harness_x.memory import (
    EpisodeOutcome,
    ErrorSeverity,
    ErrorStatus,
    GoalStatus,
    MemoryClass,
)
from harness_x.orchestrator import BudgetDelta, OperatingMode
from harness_x.tools import ToolStatus

from .base import (
    RoutineBindings,
    RoutineError,
    RoutineExecutionContext,
    RoutineResult,
    RoutineSpec,
    RoutineStatus,
    ScriptedRoutine,
)
from .engine import RoutineEngine
from .scripted import (
    ConsolidationRoutine,
    RecoveryRoutine,
    VerificationRoutine,
    VerificationRoutineRequest,
)


def _candidate_id(
    task_id: TaskId,
    tool_name: str,
    arguments: dict[str, Any],
    call_index: int,
) -> CandidateId:
    canonical = json.dumps(
        {
            "task_id": str(task_id),
            "tool_name": tool_name,
            "arguments": arguments,
            "call_index": call_index,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return CandidateId(value=f"candidate_{hashlib.sha256(canonical).hexdigest()[:32]}")


class ToolAwareScriptedReasoningStub:
    """Deterministic proposer that names registered tools but never executes them."""

    def __init__(self) -> None:
        self.calls = 0

    def propose_action(
        self,
        *,
        task_id: TaskId,
        action_name: str,
        arguments: dict[str, Any],
        provenance: Provenance,
    ) -> ActionProposal:
        tool_name = action_name.strip()
        if not tool_name:
            raise ValueError("tool name cannot be blank")
        call_index = self.calls
        self.calls += 1
        return ActionProposal(
            candidate_id=_candidate_id(task_id, tool_name, arguments, call_index),
            task_id=task_id,
            tool_name=tool_name,
            arguments=arguments,
            provenance=provenance,
        )


class ToolTaskRoutineRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    goal_id: GoalId
    observation: Observation
    observation_priority: float = Field(default=0.65, ge=0.0, le=1.0)
    observation_size_units: int = Field(default=1, gt=0)
    uncertainty: bool = False
    unresolved_entities: tuple[str, ...] = ()
    recent_retrieval_count: int = Field(default=0, ge=0)
    repeated_failure_count: int = Field(default=0, ge=0)
    tool_name: str = Field(min_length=1)
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    expected_result: dict[str, Any] = Field(default_factory=dict)
    required_result_keys: tuple[str, ...] = ()
    episode_summary: str = Field(min_length=1)

    @field_validator("tool_name", "episode_summary")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("tool task text fields cannot be blank")
        return value


class ToolTaskRoutine(ScriptedRoutine):
    """Full task path where proposals must cross the Milestone 6 tool boundary."""

    spec = RoutineSpec(
        routine_id=RoutineId(value="routine_tool_task_v1"),
        name="task",
        version="task-v1-tools",
        precondition_modes=(OperatingMode.TASK_ACTIVE,),
        required_state_views=(
            "active_goal",
            "working_state",
            "episodic_memory",
            "error_buffer",
            "budget",
            "tool_registry",
            "tool_permissions",
        ),
        allowed_tools=("calculator", "kv_read", "sandbox_write", "unreliable"),
        allowed_memory_writes=(
            MemoryClass.WORKING,
            MemoryClass.EPISODIC,
            MemoryClass.ERROR,
        ),
        step_policy=(
            "observe",
            "update",
            "retrieve",
            "decide",
            "propose_tool",
            "authorize_tool",
            "execute_tool",
            "normalize_observation",
            "verify",
            "store",
        ),
        verification_requirements=("tool_schema_valid", "verification_routine_accepts"),
        termination_rule="complete verified goal or enter recoverable failure mode",
    )
    request_type = ToolTaskRoutineRequest

    def run(
        self,
        context: RoutineExecutionContext,
        request: ToolTaskRoutineRequest,
    ) -> RoutineResult:
        b = context.bindings
        session = b.orchestrator.session
        if request.observation.task_id != session.task_id:
            raise RoutineError("observation task_id does not match active task")
        goal = b.goals.get(request.goal_id)
        if goal.task_id != session.task_id or goal.status != GoalStatus.ACTIVE:
            raise RoutineError("tool task requires an active goal owned by this task")

        observation_event = context.recorder.emit(
            EventType.OBSERVATION_RECEIVED,
            "routine.task",
            input_refs=(str(request.goal_id),),
            metadata={
                "kind": request.observation.kind,
                "content": request.observation.content,
                "provenance": request.observation.provenance.model_dump(mode="json"),
            },
        )
        write_observation = b.write_gate.evaluate(
            WriteRequest(
                accepted=True,
                kind="observation",
                source_ref=str(observation_event.event_id),
            )
        )
        if write_observation.decision.get("memory_class") != MemoryClass.WORKING.value:
            raise RoutineError("task observation must route to working memory")
        context.require_memory_write(MemoryClass.WORKING)
        working_item = b.working.add(
            kind=request.observation.kind,
            content=request.observation.content,
            priority=request.observation_priority,
            size_units=request.observation_size_units,
            source=str(observation_event.event_id),
            provenance=request.observation.provenance,
        )

        unresolved_count = sum(
            record.status in {ErrorStatus.OPEN, ErrorStatus.INVESTIGATING}
            for record in b.errors.all()
        )
        maintenance = b.maintenance_gate.evaluate(
            MaintenanceRequest(
                working_pressure=min(1.0, b.working.pressure.pressure),
                unresolved_error_count=unresolved_count,
                repeated_failure_count=request.repeated_failure_count,
            )
        )
        if maintenance.decision.get("trigger"):
            b.orchestrator.enter_maintenance("maintenance_gate_triggered")
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                output_refs=(str(working_item.memory_id),),
                data={"reason": "maintenance_required", "maintenance": maintenance.decision},
            )

        retrieval = b.retrieval_gate.evaluate(
            RetrievalRequest(
                current_routine="task",
                unresolved_entities=request.unresolved_entities,
                uncertainty=request.uncertainty,
                working_pressure=min(1.0, b.working.pressure.pressure),
                recent_retrieval_count=request.recent_retrieval_count,
                query=request.observation.kind,
            )
        )
        retrieved_refs: list[str] = []
        if retrieval.decision.get("retrieve"):
            limit = int(retrieval.decision.get("limit", 0))
            for target in retrieval.decision.get("targets", []):
                if target == MemoryClass.EPISODIC.value:
                    retrieved_refs.extend(
                        str(item.memory_id)
                        for item in b.episodic.search(query=request.observation.kind, limit=limit)
                    )
                elif target == MemoryClass.ERROR.value:
                    retrieved_refs.extend(
                        str(item.memory_id) for item in b.errors.unresolved()[:limit]
                    )
                elif target == MemoryClass.GOAL.value:
                    retrieved_refs.append(str(b.goals.retrieve(request.goal_id).goal_id))

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
                reason="focus_gate_proposal",
            )

        compute = b.compute_gate.evaluate(
            ComputeRequest(
                budget=b.orchestrator.session.budget,
                usage=b.orchestrator.session.usage,
                requested=BudgetDelta(reasoning_steps=1),
            )
        )
        if compute.decision.get("action") == "suspend":
            b.orchestrator.suspend("compute_gate_budget_exhausted")
            return RoutineResult(status=RoutineStatus.BLOCKED, data={"reason": "budget_exhausted"})
        if compute.decision.get("action") == "stop":
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                data={"reason": compute.decision.get("reason")},
            )
        b.orchestrator.consume_budget(
            BudgetDelta(reasoning_steps=1),
            reason="task_routine_decision",
        )

        proposal = b.reasoning_stub.propose_action(
            task_id=session.task_id,
            action_name=request.tool_name,
            arguments=request.tool_arguments,
            provenance=request.observation.provenance,
        )
        context.recorder.emit(
            EventType.ACTION_PROPOSED,
            "routine.task.reasoning_stub",
            input_refs=(str(request.goal_id), *retrieved_refs),
            output_refs=(str(proposal.candidate_id),),
            metadata={"proposal": proposal.model_dump(mode="json"), "model_inference": False},
        )

        # Proposal and execution are intentionally separate. The context first checks
        # the routine authority envelope; the executor then performs registry,
        # permission, schema, budget, timeout, and result normalization checks.
        try:
            tool_result = context.execute_tool(proposal)
        except RoutineError as exc:
            return self._tool_failure(
                context,
                request,
                proposal.candidate_id,
                status="routine_denied",
                detail=str(exc),
            )

        if not tool_result.succeeded:
            return self._tool_failure(
                context,
                request,
                proposal.candidate_id,
                status=tool_result.status.value,
                detail=tool_result.error or tool_result.status.value,
            )

        tool_observation = b.tool_executor.observation_from(tool_result)
        tool_observation_event = context.recorder.emit(
            EventType.OBSERVATION_RECEIVED,
            "routine.task.tool_result",
            input_refs=(str(proposal.candidate_id),),
            metadata={
                "kind": tool_observation.kind,
                "content": tool_observation.content,
                "provenance": tool_observation.provenance.model_dump(mode="json"),
            },
        )

        b.orchestrator.enter_verification("tool_result_ready")
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
        accepted = bool(verification_data["accepted"])
        verification_event_id = EventId(value=str(verification_data["verification_event_id"]))

        if not accepted:
            context.require_memory_write(MemoryClass.ERROR)
            error = b.errors.record(
                anomaly=f"verification failed for tool {request.tool_name}",
                source_event_id=verification_event_id,
                severity=ErrorSeverity.ERROR,
                provenance=tool_observation.provenance,
            )
            episode = self._store_episode(
                context,
                request,
                proposal.candidate_id,
                outcome=EpisodeOutcome.FAILURE,
                summary=f"FAILED: {request.episode_summary}",
                metadata={
                    "tool_name": request.tool_name,
                    "tool_status": tool_result.status.value,
                    "verification_event_id": str(verification_event_id),
                    "tool_observation_event_id": str(tool_observation_event.event_id),
                },
            )
            b.orchestrator.enter_recovery("verification_failed")
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                output_refs=(str(error.memory_id), str(episode.memory_id)),
                data={
                    "reason": "verification_failed",
                    "error_memory_id": str(error.memory_id),
                    "episode_memory_id": str(episode.memory_id),
                    "tool_result": tool_result.model_dump(mode="json"),
                    "verification": verification_data,
                },
            )

        episode = self._store_episode(
            context,
            request,
            proposal.candidate_id,
            outcome=EpisodeOutcome.SUCCESS,
            summary=request.episode_summary,
            metadata={
                "tool_name": request.tool_name,
                "tool_status": tool_result.status.value,
                "verification_event_id": str(verification_event_id),
                "tool_observation_event_id": str(tool_observation_event.event_id),
            },
        )
        b.goals.update_status(request.goal_id, GoalStatus.COMPLETE, reason="verified_tool_result")
        b.orchestrator.complete("verified_tool_result")
        return RoutineResult(
            status=RoutineStatus.SUCCEEDED,
            output_refs=(str(request.goal_id), str(episode.memory_id), str(proposal.candidate_id)),
            data={
                "episode_memory_id": str(episode.memory_id),
                "candidate_id": str(proposal.candidate_id),
                "tool_result": tool_result.model_dump(mode="json"),
                "verification": verification_data,
                "retrieved_refs": retrieved_refs,
                "focus": focus.decision,
            },
        )

    def _tool_failure(
        self,
        context: RoutineExecutionContext,
        request: ToolTaskRoutineRequest,
        candidate_id: CandidateId,
        *,
        status: str,
        detail: str,
    ) -> RoutineResult:
        b = context.bindings
        events = context.recorder.store.events(trace_id=context.recorder.trace_id)
        source_event = events[-1]
        context.require_memory_write(MemoryClass.ERROR)
        error = b.errors.record(
            anomaly=f"tool {request.tool_name} failed: {status}",
            source_event_id=source_event.event_id,
            severity=ErrorSeverity.ERROR,
            provenance=request.observation.provenance,
        )
        episode = self._store_episode(
            context,
            request,
            candidate_id,
            outcome=EpisodeOutcome.FAILURE,
            summary=f"FAILED: {request.episode_summary}",
            metadata={"tool_name": request.tool_name, "tool_status": status, "detail": detail},
        )
        # Budget refusal may already have suspended the authoritative task. Do not
        # bypass exact-mode resume semantics by forcing RECOVERY in that case.
        if b.orchestrator.session.mode != OperatingMode.SUSPENDED:
            b.orchestrator.enter_recovery("tool_execution_failed")
        return RoutineResult(
            status=RoutineStatus.BLOCKED,
            output_refs=(str(error.memory_id), str(episode.memory_id), str(candidate_id)),
            data={
                "reason": "tool_execution_failed",
                "tool_status": status,
                "detail": detail,
                "error_memory_id": str(error.memory_id),
                "episode_memory_id": str(episode.memory_id),
            },
        )

    def _store_episode(
        self,
        context: RoutineExecutionContext,
        request: ToolTaskRoutineRequest,
        candidate_id: CandidateId,
        *,
        outcome: EpisodeOutcome,
        summary: str,
        metadata: dict[str, Any],
    ):
        b = context.bindings
        write_episode = b.write_gate.evaluate(
            WriteRequest(
                accepted=True,
                kind="outcome",
                source_ref=str(candidate_id),
            )
        )
        if write_episode.decision.get("memory_class") != MemoryClass.EPISODIC.value:
            raise RoutineError("task outcomes must route to episodic memory")
        context.require_memory_write(MemoryClass.EPISODIC)
        end_step = context.recorder.store.next_step(context.recorder.trace_id) - 1
        return b.episodic.record(
            start_step=context.started_step,
            end_step=end_step,
            summary=summary,
            outcome=outcome,
            tags=("tool_task", request.tool_name, outcome.value),
            entities=request.unresolved_entities,
            metadata={"goal_id": str(request.goal_id), "candidate_id": str(candidate_id), **metadata},
            provenance=request.observation.provenance,
        )


def build_tool_routine_engine(bindings: RoutineBindings) -> RoutineEngine:
    """Build the Milestone 6 engine; the legacy scripted action path is not registered."""

    engine = RoutineEngine(bindings)
    engine.register(ToolTaskRoutine())
    engine.register(VerificationRoutine())
    engine.register(RecoveryRoutine())
    engine.register(ConsolidationRoutine())
    return engine
