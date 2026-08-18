"""Deterministic built-in routines used before any real reasoning model exists."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.core.contracts import ActionProposal, Observation, VerificationResult
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


def _candidate_id(
    task_id: TaskId,
    action_name: str,
    arguments: dict[str, Any],
    call_index: int,
) -> CandidateId:
    canonical = json.dumps(
        {
            "task_id": str(task_id),
            "action_name": action_name,
            "arguments": arguments,
            "call_index": call_index,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()[:32]
    return CandidateId(value=f"candidate_{digest}")


class ScriptedReasoningStub:
    """Deterministic in-process decision stub; it performs no model inference."""

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
        action_name = action_name.strip()
        if not action_name:
            raise ValueError("scripted action name cannot be blank")
        call_index = self.calls
        self.calls += 1
        return ActionProposal(
            candidate_id=_candidate_id(task_id, action_name, arguments, call_index),
            task_id=task_id,
            tool_name=f"scripted:{action_name}",
            arguments=arguments,
            provenance=provenance,
        )


class VerificationRoutineRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: CandidateId
    actual: dict[str, Any]
    expected: dict[str, Any]
    provenance: Provenance
    required_keys: tuple[str, ...] = ()


class VerificationRoutine(ScriptedRoutine):
    spec = RoutineSpec(
        routine_id=RoutineId(value="routine_verification_v0"),
        name="verification",
        version="verification-v0",
        precondition_modes=(OperatingMode.VERIFY,),
        required_state_views=("candidate", "expected_result"),
        allowed_tools=(),
        allowed_memory_writes=(),
        step_policy=("validate", "report"),
        verification_requirements=("deterministic_checks",),
        termination_rule="return an explicit accepted/rejected result",
    )
    request_type = VerificationRoutineRequest

    def run(
        self,
        context: RoutineExecutionContext,
        request: VerificationRoutineRequest,
    ) -> RoutineResult:
        checks: list[str] = []
        failures: list[str] = []

        for key in request.required_keys:
            check = f"required_key:{key}"
            checks.append(check)
            if key not in request.actual:
                failures.append(check)

        checks.append("exact_expected_match")
        if request.actual != request.expected:
            failures.append("exact_expected_match")

        accepted = not failures
        verification = VerificationResult(
            candidate_id=request.candidate_id,
            accepted=accepted,
            checks=checks,
            reason=None if accepted else "failed:" + ",".join(failures),
            provenance=request.provenance,
        )
        event = context.recorder.emit(
            EventType.VERIFICATION_COMPLETED,
            "routine.verification",
            input_refs=(str(request.candidate_id),),
            output_refs=(str(request.candidate_id),),
            metadata={
                "accepted": accepted,
                "checks": checks,
                "failures": failures,
                "result": verification.model_dump(mode="json"),
            },
        )
        return RoutineResult(
            status=RoutineStatus.SUCCEEDED if accepted else RoutineStatus.BLOCKED,
            output_refs=(str(event.event_id), str(request.candidate_id)),
            data={
                "accepted": accepted,
                "verification_event_id": str(event.event_id),
                "verification": verification.model_dump(mode="json"),
            },
        )


class TaskRoutineRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    goal_id: GoalId
    observation: Observation
    observation_priority: float = Field(default=0.65, ge=0.0, le=1.0)
    observation_size_units: int = Field(default=1, gt=0)
    uncertainty: bool = False
    unresolved_entities: tuple[str, ...] = ()
    recent_retrieval_count: int = Field(default=0, ge=0)
    repeated_failure_count: int = Field(default=0, ge=0)
    action_name: str = Field(min_length=1)
    action_arguments: dict[str, Any] = Field(default_factory=dict)
    action_result: dict[str, Any] = Field(default_factory=dict)
    expected_result: dict[str, Any] = Field(default_factory=dict)
    required_result_keys: tuple[str, ...] = ()
    episode_summary: str = Field(min_length=1)

    @field_validator("action_name", "episode_summary")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task routine text fields cannot be blank")
        return value


class TaskRoutine(ScriptedRoutine):
    spec = RoutineSpec(
        routine_id=RoutineId(value="routine_task_v0"),
        name="task",
        version="task-v0",
        precondition_modes=(OperatingMode.TASK_ACTIVE,),
        required_state_views=(
            "active_goal",
            "working_state",
            "episodic_memory",
            "error_buffer",
            "budget",
        ),
        allowed_tools=(),
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
            "act",
            "verify",
            "store",
        ),
        verification_requirements=("verification_routine_accepts",),
        termination_rule="complete verified goal or enter recovery/maintenance",
    )
    request_type = TaskRoutineRequest

    def run(
        self,
        context: RoutineExecutionContext,
        request: TaskRoutineRequest,
    ) -> RoutineResult:
        b = context.bindings
        session = b.orchestrator.session
        if request.observation.task_id != session.task_id:
            raise RoutineError("observation task_id does not match active task")

        goal = b.goals.get(request.goal_id)
        if goal.task_id != session.task_id:
            raise RoutineError("goal belongs to a different task")
        if goal.status != GoalStatus.ACTIVE:
            raise RoutineError("task routine requires an active goal")

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
                data={
                    "reason": "maintenance_required",
                    "maintenance": maintenance.decision,
                },
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
                    episodes = b.episodic.search(
                        query=request.observation.kind,
                        limit=limit,
                    )
                    retrieved_refs.extend(str(item.memory_id) for item in episodes)
                elif target == MemoryClass.ERROR.value:
                    errors = b.errors.unresolved()
                    retrieved_refs.extend(str(item.memory_id) for item in errors[:limit])
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
        compute_action = compute.decision.get("action")
        if compute_action == "suspend":
            b.orchestrator.suspend("compute_gate_budget_exhausted")
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                output_refs=(str(working_item.memory_id),),
                data={"reason": "budget_exhausted"},
            )
        if compute_action == "stop":
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                output_refs=(str(working_item.memory_id),),
                data={"reason": compute.decision.get("reason")},
            )
        b.orchestrator.consume_budget(
            BudgetDelta(reasoning_steps=1),
            reason="task_routine_decision",
        )

        proposal = b.reasoning_stub.propose_action(
            task_id=session.task_id,
            action_name=request.action_name,
            arguments=request.action_arguments,
            provenance=request.observation.provenance,
        )
        context.recorder.emit(
            EventType.ACTION_PROPOSED,
            "routine.task.reasoning_stub",
            input_refs=(str(request.goal_id), *retrieved_refs),
            output_refs=(str(proposal.candidate_id),),
            metadata={
                "proposal": proposal.model_dump(mode="json"),
                "model_inference": False,
            },
        )
        action_event = context.recorder.emit(
            EventType.ACTION_EXECUTED,
            "routine.task.scripted_action",
            input_refs=(str(proposal.candidate_id),),
            output_refs=(str(proposal.candidate_id),),
            metadata={
                "action_name": request.action_name,
                "arguments": request.action_arguments,
                "result": request.action_result,
                "execution_boundary": "deterministic_in_process_stub",
                "external_side_effect": False,
            },
        )

        b.orchestrator.enter_verification("scripted_action_ready")
        verification_execution = context.invoke(
            "verification",
            VerificationRoutineRequest(
                candidate_id=proposal.candidate_id,
                actual=request.action_result,
                expected=request.expected_result,
                provenance=request.observation.provenance,
                required_keys=request.required_result_keys,
            ),
        )
        verification_data = verification_execution.result.data
        accepted = bool(verification_data["accepted"])
        verification_event_id = EventId(
            value=str(verification_data["verification_event_id"])
        )

        if not accepted:
            write_error = b.write_gate.evaluate(
                WriteRequest(
                    accepted=True,
                    kind="failure",
                    source_ref=str(action_event.event_id),
                    verification_ref=str(verification_event_id),
                )
            )
            if write_error.decision.get("memory_class") != MemoryClass.ERROR.value:
                raise RoutineError("verification failures must route to error memory")
            context.require_memory_write(MemoryClass.ERROR)
            error = b.errors.record(
                anomaly=f"verification failed for {request.action_name}",
                source_event_id=verification_event_id,
                severity=ErrorSeverity.ERROR,
                provenance=request.observation.provenance,
            )

            failure_episode = self._store_episode(
                context,
                request,
                proposal.candidate_id,
                verification_event_id,
                outcome=EpisodeOutcome.FAILURE,
                summary=f"FAILED: {request.episode_summary}",
            )
            b.orchestrator.enter_recovery("verification_failed")
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                output_refs=(
                    str(error.memory_id),
                    str(failure_episode.memory_id),
                    str(proposal.candidate_id),
                ),
                data={
                    "reason": "verification_failed",
                    "error_memory_id": str(error.memory_id),
                    "episode_memory_id": str(failure_episode.memory_id),
                    "verification": verification_data,
                },
            )

        episode = self._store_episode(
            context,
            request,
            proposal.candidate_id,
            verification_event_id,
            outcome=EpisodeOutcome.SUCCESS,
            summary=request.episode_summary,
        )
        b.goals.update_status(
            request.goal_id,
            GoalStatus.COMPLETE,
            reason="verified_task_result",
        )
        b.orchestrator.complete("verified_task_result")
        return RoutineResult(
            status=RoutineStatus.SUCCEEDED,
            output_refs=(
                str(request.goal_id),
                str(episode.memory_id),
                str(proposal.candidate_id),
                str(verification_event_id),
            ),
            data={
                "episode_memory_id": str(episode.memory_id),
                "candidate_id": str(proposal.candidate_id),
                "verification": verification_data,
                "retrieved_refs": retrieved_refs,
                "focus": focus.decision,
            },
        )

    def _store_episode(
        self,
        context: RoutineExecutionContext,
        request: TaskRoutineRequest,
        candidate_id: CandidateId,
        verification_event_id: EventId,
        *,
        outcome: EpisodeOutcome,
        summary: str,
    ):
        b = context.bindings
        write_episode = b.write_gate.evaluate(
            WriteRequest(
                accepted=True,
                kind="outcome",
                source_ref=str(candidate_id),
                verification_ref=str(verification_event_id),
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
            tags=("task_routine", request.action_name, outcome.value),
            entities=request.unresolved_entities,
            metadata={
                "goal_id": str(request.goal_id),
                "candidate_id": str(candidate_id),
                "verification_event_id": str(verification_event_id),
            },
            provenance=request.observation.provenance,
        )


class RecoveryRoutineRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    error_memory_id: MemoryId
    query: str | None = None
    priority: float = Field(default=0.9, ge=0.0, le=1.0)
    size_units: int = Field(default=1, gt=0)


class RecoveryRoutine(ScriptedRoutine):
    spec = RoutineSpec(
        routine_id=RoutineId(value="routine_recovery_v0"),
        name="recovery",
        version="recovery-v0",
        precondition_modes=(OperatingMode.RECOVERY,),
        required_state_views=("error_buffer", "episodic_memory", "working_state"),
        allowed_tools=(),
        allowed_memory_writes=(MemoryClass.WORKING,),
        step_policy=("inspect_failure", "retrieve_prior_attempt", "reconstruct_state"),
        verification_requirements=(),
        termination_rule="restore coherent working context or remain blocked",
    )
    request_type = RecoveryRoutineRequest

    def run(
        self,
        context: RoutineExecutionContext,
        request: RecoveryRoutineRequest,
    ) -> RoutineResult:
        b = context.bindings
        error = b.errors.get(request.error_memory_id)
        if error.status not in {ErrorStatus.OPEN, ErrorStatus.INVESTIGATING}:
            raise RoutineError("recovery requires an unresolved error record")

        retrieval = b.retrieval_gate.evaluate(
            RetrievalRequest(
                current_routine="recovery",
                unresolved_entities=(str(request.error_memory_id),),
                uncertainty=True,
                working_pressure=min(1.0, b.working.pressure.pressure),
                recent_retrieval_count=0,
                query=request.query,
            )
        )
        limit = int(retrieval.decision.get("limit", 5))
        episodes = b.episodic.search(
            query=request.query,
            outcome=EpisodeOutcome.FAILURE,
            limit=limit,
        )
        if not episodes:
            return RoutineResult(
                status=RoutineStatus.BLOCKED,
                output_refs=(str(request.error_memory_id),),
                data={"reason": "no_failed_episode_available"},
            )

        episode = episodes[0]
        write = b.write_gate.evaluate(
            WriteRequest(
                accepted=True,
                kind="working",
                source_ref=str(episode.memory_id),
            )
        )
        if write.decision.get("memory_class") != MemoryClass.WORKING.value:
            raise RoutineError("recovery context must route to working memory")
        context.require_memory_write(MemoryClass.WORKING)
        reconstructed = b.working.add(
            kind="recovery_context",
            content={
                "source_episode_id": str(episode.memory_id),
                "summary": episode.summary,
                "outcome": episode.outcome.value,
                "error_memory_id": str(request.error_memory_id),
            },
            priority=request.priority,
            size_units=request.size_units,
            source=str(episode.memory_id),
            provenance=episode.provenance,
        )
        b.orchestrator.transition(
            OperatingMode.TASK_ACTIVE,
            "recovery_state_reconstructed",
        )
        return RoutineResult(
            status=RoutineStatus.SUCCEEDED,
            output_refs=(str(reconstructed.memory_id), str(episode.memory_id)),
            data={
                "reconstructed_memory_id": str(reconstructed.memory_id),
                "source_episode_id": str(episode.memory_id),
            },
        )


class ConsolidationRoutineRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    episode_ids: tuple[MemoryId, ...]

    @field_validator("episode_ids")
    @classmethod
    def require_episode(cls, value: tuple[MemoryId, ...]) -> tuple[MemoryId, ...]:
        if not value:
            raise ValueError("consolidation requires at least one episode")
        return value


class ConsolidationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_episode_ids: tuple[MemoryId, ...]
    outcome_counts: dict[str, int]
    summaries: tuple[str, ...]
    fingerprint: str = Field(min_length=64, max_length=64)
    promoted: bool = False


class ConsolidationRoutine(ScriptedRoutine):
    spec = RoutineSpec(
        routine_id=RoutineId(value="routine_consolidation_v0"),
        name="consolidation",
        version="consolidation-v0",
        precondition_modes=(OperatingMode.CONSOLIDATION,),
        required_state_views=("episodic_memory",),
        allowed_tools=(),
        allowed_memory_writes=(),
        step_policy=("collect_episodes", "summarize", "return_candidate"),
        verification_requirements=("no_semantic_promotion",),
        termination_rule="return structured summary to maintenance without promotion",
    )
    request_type = ConsolidationRoutineRequest

    def run(
        self,
        context: RoutineExecutionContext,
        request: ConsolidationRoutineRequest,
    ) -> RoutineResult:
        episodes = tuple(
            context.bindings.episodic.get(memory_id)
            for memory_id in request.episode_ids
        )
        canonical = json.dumps(
            [episode.model_dump(mode="json") for episode in episodes],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        summary = ConsolidationSummary(
            source_episode_ids=tuple(episode.memory_id for episode in episodes),
            outcome_counts=dict(
                sorted(Counter(episode.outcome.value for episode in episodes).items())
            ),
            summaries=tuple(episode.summary for episode in episodes),
            fingerprint=hashlib.sha256(canonical).hexdigest(),
            promoted=False,
        )
        context.bindings.orchestrator.transition(
            OperatingMode.MAINTENANCE,
            "consolidation_summary_created",
        )
        summary_ref = f"consolidation:{summary.fingerprint}"
        return RoutineResult(
            status=RoutineStatus.SUCCEEDED,
            output_refs=(summary_ref, *tuple(str(item) for item in request.episode_ids)),
            data={"summary": summary.model_dump(mode="json")},
        )


def build_scripted_routine_engine(bindings: RoutineBindings) -> RoutineEngine:
    engine = RoutineEngine(bindings)
    engine.register(TaskRoutine())
    engine.register(VerificationRoutine())
    engine.register(RecoveryRoutine())
    engine.register(ConsolidationRoutine())
    return engine
