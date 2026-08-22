"""Harness X coding-task runtime built on the existing reasoning and tool boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from harness_x.core import (
    ActionProposal,
    CandidateId,
    ComputeBudget,
    EventType,
    ReasoningRequest,
    RoutineId,
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
from harness_x.tools import ToolExecutor, ToolResult
from harness_x.tools.coding import build_coding_registry

_CODING_ROUTINE_ID = RoutineId(value="routine_coding_task_v1")
_CODING_PERMISSIONS = frozenset(
    {"workspace.read", "workspace.write", "workspace.execute"}
)


class CodingVerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CodingTaskReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "coding-task-report-v1"
    succeeded: bool
    status: str
    task: str
    workspace_root: str
    task_id: str
    goal_id: str
    trace_id: str
    trace_path: str
    reasoning_steps: int = Field(ge=0)
    tool_actions: int = Field(ge=0)
    verification_attempts: int = Field(ge=0)
    verification: tuple[CodingVerificationResult, ...] = ()
    final_model_status: str | None = None
    failure_reason: str | None = None
    control_plan_path: str | None = None
    final_coding_phase: str | None = None
    pending_commitments: int = Field(default=0, ge=0)
    coding_progress: dict[str, Any] = Field(default_factory=dict)
    horizon_mode: str | None = None


class CodingTaskRuntime:
    """Iterative coding loop with software-owned budgets and tool execution.

    The model can only propose structured actions. Workspace/process actions cross
    ToolExecutor and its permission/schema/budget boundary. Verification commands are
    software-owned and cross the same boundary before the task may finish.
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
        system_version: str = "0.1.0a0+coding21",
        allowed_executables: frozenset[str] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        if not self.workspace_root.is_dir():
            raise ValueError("coding workspace must be an existing directory")
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.core = core
        self.budget = ComputeBudget(
            max_reasoning_steps=max_reasoning_steps,
            max_tool_actions=max_tool_actions,
            max_output_tokens=max_output_tokens,
        )
        self.system_version = SystemVersion(value=system_version)
        registry_kwargs = (
            {}
            if allowed_executables is None
            else {"allowed_executables": allowed_executables}
        )
        self.registry = build_coding_registry(self.workspace_root, **registry_kwargs)
        self.allowed_tools = tuple(spec.name for spec in self.registry.specs())

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
        provenance = Provenance(
            source_kind=SourceKind.USER,
            source_ref="coding-task",
            created_at=recorder.clock.now(),
            system_version=recorder.system_version,
            trace_id=recorder.trace_id,
            verification=VerificationState.VERIFIED,
        )
        goal = goals.create_goal(
            normalized_task,
            provenance,
            governing_constraints=(
                "Only operate through declared Harness X tools and permissions",
                "Keep filesystem writes inside the configured workspace",
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
        final_model_status: str | None = None
        failure_reason: str | None = None

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
                    provenance=provenance,
                )

            if result.status == "complete":
                verification_attempts += 1
                latest_verification = self._verify(
                    commands, executor, recorder, task_id
                )
                if all(item.returncode == 0 for item in latest_verification):
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
                self._remember_verification_failure(
                    working, recorder, provenance, latest_verification
                )
                continue

            if result.status == "blocked":
                failure_reason = "model_reported_blocked_without_action"
                break

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

    def _instruction(
        self,
        task: str,
        commands: tuple[tuple[str, ...], ...],
    ) -> str:
        return (
            "Perform this coding task autonomously inside the supplied workspace.\n"
            f"TASK: {task}\n"
            "Inspect the repository before changing it. Use workspace_read/list/search "
            "for inspection, workspace_write for new files, workspace_patch for exact "
            "bounded edits, and process_run for allowed local commands. Prefer small, "
            "verifiable edits. You do not own filesystem or process authority; only "
            "propose actions. Return status=complete only when you believe the "
            "implementation is ready for the software-owned verification commands. "
            "If verification fails, use the resulting working-state evidence to "
            "diagnose and repair it.\n"
            f"VERIFICATION: {[list(command) for command in commands]}"
        )

    def _remember_tool_result(
        self,
        working: WorkingState,
        recorder: TraceRecorder,
        result: ToolResult,
    ) -> None:
        provenance = Provenance(
            source_kind=(SourceKind.TOOL if result.succeeded else SourceKind.SYSTEM),
            source_ref=(
                f"tool:{result.tool_name}@{result.tool_version}"
                if result.succeeded
                else f"tool-error:{result.tool_name}"
            ),
            created_at=recorder.clock.now(),
            system_version=recorder.system_version,
            trace_id=recorder.trace_id,
            verification=(
                VerificationState.UNVERIFIED
                if result.succeeded
                else VerificationState.VERIFIED
            ),
        )
        working.add(
            kind="tool_result" if result.succeeded else "tool_error",
            content=result.model_dump(mode="json"),
            priority=0.75,
            size_units=1,
            source=f"tool:{result.candidate_id}",
            provenance=provenance,
        )

    def _verify(
        self,
        commands: tuple[tuple[str, ...], ...],
        executor: ToolExecutor,
        recorder: TraceRecorder,
        task_id: TaskId,
    ) -> tuple[CodingVerificationResult, ...]:
        results: list[CodingVerificationResult] = []
        provenance = Provenance(
            source_kind=SourceKind.SYSTEM,
            source_ref="coding-verifier",
            created_at=recorder.clock.now(),
            system_version=recorder.system_version,
            trace_id=recorder.trace_id,
            verification=VerificationState.VERIFIED,
        )
        for command in commands:
            proposal = ActionProposal(
                candidate_id=CandidateId.new(),
                task_id=task_id,
                tool_name="process_run",
                arguments={"argv": list(command), "cwd": "."},
                provenance=provenance,
            )
            tool_result = executor.execute(
                proposal,
                routine_allowed_tools=self.allowed_tools,
                granted_permissions=_CODING_PERMISSIONS,
            )
            if not tool_result.succeeded:
                results.append(
                    CodingVerificationResult(
                        argv=command,
                        returncode=125,
                        stderr=tool_result.error or tool_result.status.value,
                    )
                )
                break
            output = tool_result.output
            item = CodingVerificationResult(
                argv=command,
                returncode=int(output.get("returncode", 125)),
                stdout=str(output.get("stdout", "")),
                stderr=str(output.get("stderr", "")),
            )
            results.append(item)
            if item.returncode != 0:
                break
        completed = tuple(results)
        recorder.emit(
            EventType.VERIFICATION_COMPLETED,
            "coding.verifier",
            metadata={
                "configured_commands": len(commands),
                "executed_commands": len(completed),
                "passed": len(completed) == len(commands)
                and all(item.returncode == 0 for item in completed),
                "returncodes": [item.returncode for item in completed],
            },
        )
        return completed

    def _remember_verification_failure(
        self,
        working: WorkingState,
        recorder: TraceRecorder,
        provenance: Provenance,
        verification: tuple[CodingVerificationResult, ...],
    ) -> None:
        working.add(
            kind="verification_failure",
            content={
                "commands": [item.model_dump(mode="json") for item in verification]
            },
            priority=0.95,
            size_units=2,
            source=f"verification:{recorder.store.next_step(recorder.trace_id)}",
            provenance=provenance,
        )

    def _report(
        self,
        *,
        succeeded: bool,
        status: str,
        task: str,
        goal_id: str,
        recorder: TraceRecorder,
        orchestrator: TaskOrchestrator,
        trace_path: Path,
        verification_attempts: int,
        verification: tuple[CodingVerificationResult, ...],
        final_model_status: str | None,
        failure_reason: str | None = None,
    ) -> CodingTaskReport:
        usage = orchestrator.session.usage
        return CodingTaskReport(
            succeeded=succeeded,
            status=status,
            task=task,
            workspace_root=str(self.workspace_root),
            task_id=str(recorder.task_id),
            goal_id=goal_id,
            trace_id=str(recorder.trace_id),
            trace_path=str(trace_path),
            reasoning_steps=usage.reasoning_steps,
            tool_actions=usage.tool_actions,
            verification_attempts=verification_attempts,
            verification=verification,
            final_model_status=final_model_status,
            failure_reason=failure_reason,
        )

    def _write_report(self, report: CodingTaskReport) -> None:
        (self.output_root / "coding-task-report.json").write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
