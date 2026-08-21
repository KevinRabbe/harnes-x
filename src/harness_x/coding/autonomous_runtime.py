"""Controller-owned coding loop for real model-driven repository work."""

from __future__ import annotations

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

from .runtime import (
    _CODING_PERMISSIONS,
    _CODING_ROUTINE_ID,
    CodingTaskReport,
    CodingTaskRuntime,
    CodingVerificationResult,
)

_MUTATING_TOOLS = frozenset({"workspace_write", "workspace_patch"})


class AutonomousCodingTaskRuntime(CodingTaskRuntime):
    """Coding runtime whose controller owns verification and stall handling.

    The original M21 reference loop only ran verification after the model explicitly
    emitted ``status=complete``. Small models can instead emit a no-op ``continue``
    after making the correct edit, causing budget exhaustion without ever reaching the
    verifier. This runtime treats verification scheduling as Harness X authority:

    * establish a baseline before the first reasoning turn;
    * after a successful mutation, a no-action turn triggers verification;
    * fresh passing verification is returned to the model as authoritative evidence;
    * repeated no-action continuation is bounded by a deterministic stall guard;
    * ``complete`` still requires fresh software-owned passing verification.

    Passing tests alone do not semantically complete a task. The model must still emit
    ``complete`` after seeing fresh verifier evidence, so a generic build command cannot
    silently stand in for the user's full coding request.
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
        system_version: str = "0.1.0a0+coding21-control",
        allowed_executables: frozenset[str] | None = None,
        baseline_verification: bool = True,
        max_idle_turns: int = 3,
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
        self.baseline_verification = baseline_verification
        self.max_idle_turns = max_idle_turns

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
        goal = goals.create_goal(
            normalized_task,
            user_provenance,
            governing_constraints=(
                "Only operate through declared Harness X tools and permissions",
                "Keep filesystem writes inside the configured workspace",
                "Software owns verification scheduling and completion authority",
                "Do not claim completion until software-owned verification passes",
            ),
            completion_criteria=(
                "Requested coding task is implemented",
                "All configured verification commands return exit code 0",
            ),
        )
        orchestrator.start("coding_task_started")

        service = ReasoningService(recorder, self.core)
        executor = ToolExecutor(self.registry, recorder, orchestrator)
        verification_attempts = 0
        latest_verification: tuple[CodingVerificationResult, ...] = ()
        verification_fresh = False
        dirty_since_verification = False
        idle_turns = 0
        final_model_status: str | None = None
        failure_reason: str | None = None

        if self.baseline_verification:
            verification_attempts += 1
            latest_verification = self._verify(commands, executor, recorder, task_id)
            verification_fresh = all(
                item.returncode == 0 for item in latest_verification
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
                    "iteration": orchestrator.session.usage.reasoning_steps,
                    "verification_attempts": verification_attempts,
                    "verification_fresh": verification_fresh,
                    "dirty_since_verification": dirty_since_verification,
                    "idle_turns": idle_turns,
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
                if not verification_fresh or dirty_since_verification:
                    verification_attempts += 1
                    latest_verification = self._verify(
                        commands, executor, recorder, task_id
                    )
                    verification_fresh = all(
                        item.returncode == 0 for item in latest_verification
                    )
                    dirty_since_verification = False
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
                if verification_fresh:
                    goals.update_status(
                        goal.goal_id,
                        GoalStatus.COMPLETE,
                        reason="coding_verification_passed",
                    )
                    orchestrator.complete("coding_verification_passed")
                    report = self._report(
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
                verification_attempts += 1
                latest_verification = self._verify(
                    commands, executor, recorder, task_id
                )
                verification_fresh = all(
                    item.returncode == 0 for item in latest_verification
                )
                dirty_since_verification = False
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

            idle_turns += 1
            self._remember_control_directive(
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
            verification_attempts += 1
            latest_verification = self._verify(commands, executor, recorder, task_id)
            verification_fresh = all(
                item.returncode == 0 for item in latest_verification
            )
            dirty_since_verification = False
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

        if orchestrator.session.mode not in {
            OperatingMode.COMPLETE,
            OperatingMode.FAILED,
        }:
            goals.update_status(
                goal.goal_id, GoalStatus.FAILED, reason="coding_task_failed"
            )
            orchestrator.fail("coding_task_failed")
        report = self._report(
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
            failure_reason=failure_reason or "reasoning_budget_exhausted",
        )
        self._write_report(report)
        return report

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

    def _remember_control_directive(
        self,
        working: WorkingState,
        recorder: TraceRecorder,
        *,
        idle_turns: int,
        verification_fresh: bool,
    ) -> None:
        provenance = Provenance(
            source_kind=SourceKind.SYSTEM,
            source_ref="coding-controller",
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
