"""Controller-owned coding loop for real model-driven repository work."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from harness_x.core import (
    ComputeBudget,
    ReasoningRequest,
    SourceKind,
    SystemClock,
    SystemVersion,
    TaskId,
    TraceId,
    VerificationState,
)
from harness_x.core.provenance import Provenance
from harness_x.memory import GoalMemory, GoalStatus, WorkingState
from harness_x.orchestrator import BudgetDelta, OperatingMode, TaskOrchestrator
from harness_x.reasoning import ReasoningCore, ReasoningService
from harness_x.telemetry import TraceRecorder, TraceStore
from harness_x.tools import ToolExecutor

from .control import (
    CodingControlController,
    CodingPhase,
    ControlIntervention,
    InterventionKind,
)
from .runtime import (
    _CODING_PERMISSIONS,
    _CODING_ROUTINE_ID,
    CodingTaskReport,
    CodingTaskRuntime,
    CodingVerificationResult,
)

_MUTATING_TOOLS = frozenset({"workspace_write", "workspace_patch"})


class AutonomousCodingTaskRuntime(CodingTaskRuntime):
    """Coding runtime with software-owned progress, verification, and completion.

    M21 established a bounded real-model coding loop. M22 adds an explicit control
    plane around that loop: coding phases, a versioned plan, durable commitments,
    deterministic progress/stuck assessment, and a remaining-horizon posture. The
    model receives those projections but cannot mutate them.

    Verification remains independent Harness X authority. A model can report that it
    believes the task is complete, but completion is accepted only after fresh passing
    verification and discharge of the root commitment.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        max_reasoning_steps: int = 32,
        max_tool_actions: int = 48,
        max_output_tokens: int = 65536,
        system_version: str = "0.1.0a0+coding22-control",
        allowed_executables: frozenset[str] | None = None,
        baseline_verification: bool = True,
        max_idle_turns: int = 3,
        max_inspection_streak: int = 6,
        max_no_progress_streak: int = 4,
        max_same_failure_count: int = 3,
    ) -> None:
        super().__init__(
            workspace_root,
            core,
            output_root,
            max_reasoning_steps=max_reasoning_steps,
            max_tool_actions=max_tool_actions,
            max_output_tokens=max_output_tokens,
            system_version=system_version,
            allowed_executables=allowed_executables,
        )
        if max_idle_turns < 1:
            raise ValueError("max_idle_turns must be positive")
        if max_inspection_streak < 1:
            raise ValueError("max_inspection_streak must be positive")
        if max_no_progress_streak < 1:
            raise ValueError("max_no_progress_streak must be positive")
        if max_same_failure_count < 1:
            raise ValueError("max_same_failure_count must be positive")
        self.baseline_verification = baseline_verification
        self.max_idle_turns = max_idle_turns
        self.max_inspection_streak = max_inspection_streak
        self.max_no_progress_streak = max_no_progress_streak
        self.max_same_failure_count = max_same_failure_count

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]],
    ) -> CodingTaskReport:
        normalized_task = task.strip()
        if not normalized_task:
            raise ValueError("coding task cannot be blank")
        commands = tuple(tuple(part for part in command) for command in verification_commands)
        if not commands or any(not command for command in commands):
            raise ValueError(
                "coding tasks require at least one non-empty verification command"
            )

        task_id = TaskId.new()
        trace_id = TraceId.new()
        trace_path = self.output_root / f"{trace_id.value}.jsonl"
        recorder = TraceRecorder(
            TraceStore(trace_path),
            trace_id,
            task_id,
            self.system_version,
            SystemClock(),
        )
        orchestrator = TaskOrchestrator.create(recorder, budget=self.budget)
        goals = GoalMemory(recorder)
        working = WorkingState(recorder, capacity_units=48)
        user_provenance = Provenance(
            source_kind=SourceKind.USER,
            source_ref="coding-task",
            created_at=recorder.clock.now(),
            system_version=recorder.system_version,
            trace_id=recorder.trace_id,
            verification=VerificationState.VERIFIED,
        )
        governing_constraints = (
            "Only operate through declared Harness X tools and permissions",
            "Keep filesystem writes inside the configured workspace",
            "Software owns coding phase, progress, verification, and completion authority",
            "Do not claim completion until software-owned verification passes",
        )
        completion_criteria = (
            "Requested coding task is implemented",
            "All configured verification commands return exit code 0",
        )
        goal = goals.create_goal(
            normalized_task,
            user_provenance,
            governing_constraints=governing_constraints,
            completion_criteria=completion_criteria,
        )
        orchestrator.start("coding_task_started")

        control = CodingControlController(
            task_id=task_id,
            task=normalized_task,
            constraints=governing_constraints,
            acceptance_requirements=completion_criteria,
            reasoning_limit=self.budget.max_reasoning_steps,
            tool_action_limit=self.budget.max_tool_actions,
            plan_path=self.output_root / f"{task_id.value}.coding-plan.json",
            recorder=recorder,
            max_inspection_streak=self.max_inspection_streak,
            max_no_progress_streak=self.max_no_progress_streak,
            max_same_failure_count=self.max_same_failure_count,
        )
        service = ReasoningService(recorder, self.core)
        executor = ToolExecutor(self.registry, recorder, orchestrator)
        verification_attempts = 0
        latest_verification: tuple[CodingVerificationResult, ...] = ()
        verification_fresh = False
        dirty_since_verification = False
        idle_turns = 0
        final_model_status: str | None = None
        failure_reason: str | None = None
        last_intervention_key: tuple[str, str] | None = None

        if self.baseline_verification:
            verification_attempts += 1
            latest_verification = self._verify(commands, executor, recorder, task_id)
            verification_fresh = all(
                item.returncode == 0 for item in latest_verification
            )
            control.record_verification(
                passed=verification_fresh,
                failure_signature=self._verification_failure_signature(latest_verification),
                step=0,
                baseline=True,
            )
            self._remember_verification_snapshot(
                working,
                recorder,
                kind="verification_baseline",
                verification=latest_verification,
                priority=0.90,
            )

        while orchestrator.session.usage.reasoning_steps < self.budget.max_reasoning_steps:
            try:
                orchestrator.consume_budget(
                    BudgetDelta(reasoning_steps=1), reason="coding_reasoning_step"
                )
            except Exception as exc:
                failure_reason = f"reasoning_budget_exhausted: {exc}"
                break

            usage = orchestrator.session.usage
            control_snapshot = control.snapshot(
                reasoning_used=usage.reasoning_steps,
                tool_actions_used=usage.tool_actions,
            )
            intervention = control_snapshot.intervention
            phase_before = control.plan.phase
            control.apply_intervention(
                intervention,
                step=usage.reasoning_steps,
            )
            if control.plan.phase != phase_before:
                control_snapshot = control_snapshot.model_copy(
                    update={"plan": control.plan}
                )
            intervention_key = (intervention.kind.value, intervention.reason)
            if intervention.kind == InterventionKind.NONE:
                last_intervention_key = None
            elif intervention_key != last_intervention_key:
                self._remember_control_intervention(
                    working,
                    recorder,
                    intervention=intervention,
                    phase=control.plan.phase,
                    reasoning_step=usage.reasoning_steps,
                )
                last_intervention_key = intervention_key

            recent_working = sorted(
                working.items(), key=lambda item: item.created_step
            )[-16:]
            request = ReasoningRequest(
                task_id=task_id,
                goal_id=goal.goal_id,
                routine_id=_CODING_ROUTINE_ID,
                instruction=self._instruction(normalized_task, commands),
                active_goal=goals.get(goal.goal_id).model_dump(mode="json"),
                working_state=[
                    item.model_dump(mode="json") for item in recent_working
                ],
                available_actions=[
                    spec.model_dump(mode="json") for spec in self.registry.specs()
                ],
                active_state={
                    "workspace_root": str(self.workspace_root),
                    "verification_commands": [list(command) for command in commands],
                    "iteration": usage.reasoning_steps,
                    "verification_attempts": verification_attempts,
                    "verification_fresh": verification_fresh,
                    "dirty_since_verification": dirty_since_verification,
                    "idle_turns": idle_turns,
                    "coding_control": control_snapshot.model_dump(mode="json"),
                },
                budget=orchestrator.session.budget,
            )
            try:
                result = service.invoke(request)
            except Exception as exc:
                failure_reason = (
                    f"reasoning_core_failed: {type(exc).__name__}: {exc}"
                )
                break
            final_model_status = result.status

            if result.actions:
                action = result.actions[0]
                tool_result = executor.execute(
                    action,
                    routine_allowed_tools=self.allowed_tools,
                    granted_permissions=_CODING_PERMISSIONS,
                )
                self._remember_tool_result(working, recorder, tool_result)
                control.record_tool_result(
                    tool_name=action.tool_name,
                    arguments=dict(action.arguments),
                    succeeded=tool_result.succeeded,
                    output=tool_result.output,
                    step=usage.reasoning_steps,
                )
                if tool_result.execution_may_continue:
                    failure_reason = "tool_timeout_may_still_be_running"
                    break
                idle_turns = 0
                if tool_result.succeeded and action.tool_name in _MUTATING_TOOLS:
                    dirty_since_verification = True
                    verification_fresh = False
                continue

            if result.proposals or result.observations:
                working.add(
                    kind="reasoning_note",
                    content={
                        "status": result.status,
                        "proposals": [
                            proposal.model_dump(mode="json")
                            for proposal in result.proposals
                        ],
                        "observations": list(result.observations),
                    },
                    priority=0.55,
                    size_units=1,
                    source=f"reasoning:{result.context_fingerprint or 'unknown'}",
                    provenance=user_provenance,
                )

            if result.status == "complete":
                # A baseline pass is not enough to establish the VERIFY -> REVIEW phase
                # transition for a semantic completion claim. Reuse only genuinely fresh
                # post-implementation evidence already represented by REVIEW.
                if (
                    not verification_fresh
                    or dirty_since_verification
                    or control.plan.phase != CodingPhase.REVIEW
                ):
                    control.begin_verification(
                        step=usage.reasoning_steps,
                        reason="model_requested_completion",
                    )
                    verification_attempts += 1
                    latest_verification = self._verify(
                        commands, executor, recorder, task_id
                    )
                    verification_fresh = all(
                        item.returncode == 0 for item in latest_verification
                    )
                    dirty_since_verification = False
                    control.record_verification(
                        passed=verification_fresh,
                        failure_signature=self._verification_failure_signature(
                            latest_verification
                        ),
                        step=usage.reasoning_steps,
                    )
                    self._remember_verification_snapshot(
                        working,
                        recorder,
                        kind=(
                            "verification_passed"
                            if verification_fresh
                            else "verification_failure"
                        ),
                        verification=latest_verification,
                        priority=0.97,
                    )
                if verification_fresh and control.plan.phase == CodingPhase.REVIEW:
                    control.mark_root_satisfied(
                        step=usage.reasoning_steps,
                        evidence_refs=self._completion_evidence_refs(
                            latest_verification,
                            context_fingerprint=result.context_fingerprint,
                        ),
                    )
                    control.transition_phase(
                        CodingPhase.COMPLETE,
                        reason="verified_semantic_completion",
                        step=usage.reasoning_steps,
                    )
                    goals.update_status(
                        goal.goal_id,
                        GoalStatus.COMPLETE,
                        reason="coding_verification_passed",
                    )
                    orchestrator.complete("coding_verification_passed")
                    report = self._with_control_report(
                        self._report(
                            succeeded=True,
                            status="complete",
                            task=normalized_task,
                            goal_id=str(goal.goal_id),
                            recorder=recorder,
                            orchestrator=orchestrator,
                            trace_path=trace_path,
                            verification_attempts=verification_attempts,
                            verification=latest_verification,
                            final_model_status=final_model_status,
                        ),
                        control,
                        orchestrator,
                    )
                    self._write_report(report)
                    return report
                idle_turns = 0
                continue

            if result.status == "blocked":
                failure_reason = "model_reported_blocked_without_action"
                break

            # A no-action continuation after a mutation is a controller signal to
            # verify the current workspace rather than spend another blind model turn.
            if dirty_since_verification:
                control.begin_verification(
                    step=usage.reasoning_steps,
                    reason="model_stopped_after_workspace_mutation",
                )
                verification_attempts += 1
                latest_verification = self._verify(
                    commands, executor, recorder, task_id
                )
                verification_fresh = all(
                    item.returncode == 0 for item in latest_verification
                )
                dirty_since_verification = False
                control.record_verification(
                    passed=verification_fresh,
                    failure_signature=self._verification_failure_signature(
                        latest_verification
                    ),
                    step=usage.reasoning_steps,
                )
                self._remember_verification_snapshot(
                    working,
                    recorder,
                    kind=(
                        "verification_passed"
                        if verification_fresh
                        else "verification_failure"
                    ),
                    verification=latest_verification,
                    priority=0.97,
                )
                idle_turns = 0
                continue

            # This remains a hard protocol fallback for non-coding cores that can emit
            # continue-without-action. Productive/stalled tool trajectories are handled
            # by CodingControlController rather than by this counter.
            idle_turns += 1
            self._remember_idle_directive(
                working,
                recorder,
                idle_turns=idle_turns,
                verification_fresh=verification_fresh,
            )
            if idle_turns >= self.max_idle_turns:
                failure_reason = "coding_model_stalled_without_action"
                break

        # Do not discard an unverified final edit just because the reasoning budget
        # ended. Run one final controller-owned verification and preserve the evidence.
        if dirty_since_verification:
            usage = orchestrator.session.usage
            control.begin_verification(
                step=usage.reasoning_steps,
                reason="final_budget_boundary_verification",
            )
            verification_attempts += 1
            latest_verification = self._verify(commands, executor, recorder, task_id)
            verification_fresh = all(
                item.returncode == 0 for item in latest_verification
            )
            dirty_since_verification = False
            control.record_verification(
                passed=verification_fresh,
                failure_signature=self._verification_failure_signature(
                    latest_verification
                ),
                step=usage.reasoning_steps,
            )
            self._remember_verification_snapshot(
                working,
                recorder,
                kind=(
                    "verification_passed"
                    if verification_fresh
                    else "verification_failure"
                ),
                verification=latest_verification,
                priority=0.97,
            )
            if verification_fresh and failure_reason is None:
                failure_reason = "verification_passed_but_completion_unconfirmed"

        failure_reason = failure_reason or "reasoning_budget_exhausted"
        usage = orchestrator.session.usage
        try:
            control.mark_root_blocked(step=usage.reasoning_steps, reason=failure_reason)
            if control.plan.phase != CodingPhase.BLOCKED:
                control.transition_phase(
                    CodingPhase.BLOCKED,
                    reason=failure_reason,
                    step=usage.reasoning_steps,
                )
        except ValueError:
            # If a terminal control state was already reached, preserve it rather than
            # rewriting history merely to fit the generic failed task envelope.
            pass

        if orchestrator.session.mode not in {
            OperatingMode.COMPLETE,
            OperatingMode.FAILED,
        }:
            goals.update_status(
                goal.goal_id, GoalStatus.FAILED, reason="coding_task_failed"
            )
            orchestrator.fail("coding_task_failed")
        report = self._with_control_report(
            self._report(
                succeeded=False,
                status="failed",
                task=normalized_task,
                goal_id=str(goal.goal_id),
                recorder=recorder,
                orchestrator=orchestrator,
                trace_path=trace_path,
                verification_attempts=verification_attempts,
                verification=latest_verification,
                final_model_status=final_model_status,
                failure_reason=failure_reason,
            ),
            control,
            orchestrator,
        )
        self._write_report(report)
        return report

    @staticmethod
    def _verification_failure_signature(
        verification: tuple[CodingVerificationResult, ...],
    ) -> str | None:
        for item in verification:
            if item.returncode == 0:
                continue
            material = (
                " ".join(item.argv)
                + f"\nreturncode={item.returncode}\n"
                + item.stdout[-1200:]
                + "\n"
                + item.stderr[-1200:]
            )
            digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
            return f"{' '.join(item.argv)}:rc={item.returncode}:sha256={digest}"
        return None

    @staticmethod
    def _completion_evidence_refs(
        verification: tuple[CodingVerificationResult, ...],
        *,
        context_fingerprint: str | None,
    ) -> tuple[str, ...]:
        verification_material = "|".join(
            f"{' '.join(item.argv)}:{item.returncode}" for item in verification
        )
        digest = hashlib.sha256(verification_material.encode("utf-8")).hexdigest()[:16]
        refs = [f"verification:{digest}"]
        if context_fingerprint:
            refs.append(f"reasoning:{context_fingerprint}")
        return tuple(refs)

    def _remember_verification_snapshot(
        self,
        working: WorkingState,
        recorder: TraceRecorder,
        *,
        kind: str,
        verification: tuple[CodingVerificationResult, ...],
        priority: float,
    ) -> None:
        provenance = Provenance(
            source_kind=SourceKind.SYSTEM,
            source_ref=f"coding-verifier:{kind}",
            created_at=recorder.clock.now(),
            system_version=recorder.system_version,
            trace_id=recorder.trace_id,
            verification=VerificationState.VERIFIED,
        )
        working.add(
            kind=kind,
            content={
                "commands": [item.model_dump(mode="json") for item in verification],
                "all_passed": all(item.returncode == 0 for item in verification),
            },
            priority=priority,
            size_units=2,
            source=f"verification:{recorder.store.next_step(recorder.trace_id)}",
            provenance=provenance,
        )

    def _remember_control_intervention(
        self,
        working: WorkingState,
        recorder: TraceRecorder,
        *,
        intervention: ControlIntervention,
        phase: CodingPhase,
        reasoning_step: int,
    ) -> None:
        provenance = Provenance(
            source_kind=SourceKind.SYSTEM,
            source_ref="coding-control-plane",
            created_at=recorder.clock.now(),
            system_version=recorder.system_version,
            trace_id=recorder.trace_id,
            verification=VerificationState.VERIFIED,
        )
        working.add(
            kind="coding_control_directive",
            content={
                "phase": phase.value,
                "reasoning_step": reasoning_step,
                "intervention": intervention.model_dump(mode="json"),
            },
            priority=1.0,
            size_units=1,
            source=f"controller:{recorder.store.next_step(recorder.trace_id)}",
            provenance=provenance,
        )

    def _remember_idle_directive(
        self,
        working: WorkingState,
        recorder: TraceRecorder,
        *,
        idle_turns: int,
        verification_fresh: bool,
    ) -> None:
        provenance = Provenance(
            source_kind=SourceKind.SYSTEM,
            source_ref="coding-controller-protocol-fallback",
            created_at=recorder.clock.now(),
            system_version=recorder.system_version,
            trace_id=recorder.trace_id,
            verification=VerificationState.VERIFIED,
        )
        directive = (
            "Fresh software-owned verification has passed. Return status=complete if "
            "the user task is fully satisfied; otherwise propose one concrete tool action."
            if verification_fresh
            else "A continue turn without a tool action is not progress. Propose one concrete "
            "inspection/edit/execute action, return complete if the task is done, or blocked "
            "if no safe action exists."
        )
        working.add(
            kind="coding_control_directive",
            content={"idle_turns": idle_turns, "directive": directive},
            priority=1.0,
            size_units=1,
            source=f"controller:{recorder.store.next_step(recorder.trace_id)}",
            provenance=provenance,
        )

    def _with_control_report(
        self,
        report: CodingTaskReport,
        control: CodingControlController,
        orchestrator: TaskOrchestrator,
    ) -> CodingTaskReport:
        usage = orchestrator.session.usage
        snapshot = control.snapshot(
            reasoning_used=usage.reasoning_steps,
            tool_actions_used=usage.tool_actions,
        )
        return report.model_copy(
            update={
                "control_plan_path": (
                    str(control.plan_path) if control.plan_path is not None else None
                ),
                "final_coding_phase": control.plan.phase.value,
                "pending_commitments": len(control.plan.pending_commitments),
                "coding_progress": snapshot.progress.model_dump(mode="json"),
                "horizon_mode": snapshot.horizon.mode.value,
            }
        )
