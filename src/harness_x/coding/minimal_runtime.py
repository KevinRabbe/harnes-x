"""M34 MINIMAL arm: M21 loop with matched repository tools, isolation, and M25 verifier.

The minimal arm intentionally excludes M22 persistent coding control and M27+ durable/project
memory machinery. It keeps the same external repository/workspace/process action plane and the
same software-owned typed verification boundary used by the FULL arm.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from harness_x.core import TaskId
from harness_x.reasoning import ReasoningCore
from harness_x.repository import RepositoryIntelligenceService, RepositorySemanticProvider
from harness_x.telemetry import TraceRecorder
from harness_x.tools import ToolExecutor
from harness_x.tools.coding_repository import build_repository_coding_registry

from .isolation import IsolationResult, IsolationRetention, TaskWorkspaceIsolationManager
from .repository_runtime import RepositoryContextReasoningCore
from .runtime import CodingTaskReport, CodingTaskRuntime, CodingVerificationResult, _CODING_PERMISSIONS
from .strict_verification import StrictVerificationPlatform
from .verification import (
    VerificationCheckStatus,
    VerificationPlan,
    VerificationRequirement,
    VerificationRun,
    command_verification_plan,
)
from .verified_runtime import VerificationContextReasoningCore


class MinimalVerifiedCodingTaskReport(CodingTaskReport):
    schema_version: str = "coding-task-report-v23-minimal-verification"
    verification_plan: VerificationPlan
    verification_runs: tuple[VerificationRun, ...] = ()


class MinimalVerifiedIsolatedCodingTaskReport(MinimalVerifiedCodingTaskReport):
    schema_version: str = "coding-task-report-v24-isolated-minimal-verification"
    isolation: IsolationResult


class MinimalVerifiedRepositoryCodingTaskRuntime(CodingTaskRuntime):
    """M21 cognitive loop with M23 repository ACI and M25 typed verification.

    There is deliberately no M22 CodingControlController, persistent plan, commitment ledger,
    horizon intervention, M27 task state, M28 project memory, M29 reliability, or M30 revision.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        verification_plan: VerificationPlan | None = None,
        semantic_provider: RepositorySemanticProvider | None = None,
        max_reasoning_steps: int = 32,
        max_tool_actions: int = 48,
        max_output_tokens: int = 65536,
        system_version: str = "0.1.0a0+coding34-minimal",
        allowed_executables: frozenset[str] | None = None,
        baseline_verification: bool = False,
    ) -> None:
        if baseline_verification:
            raise ValueError(
                "M34 minimal runtime does not perform baseline verification; the matched "
                "experiment contract requires baseline_verification=False in both arms"
            )
        workspace = Path(workspace_root).resolve()
        platform = StrictVerificationPlatform(workspace, verification_plan)
        verification_core = VerificationContextReasoningCore(core, platform)
        repository = RepositoryIntelligenceService(workspace)
        repository.snapshot()
        registry = build_repository_coding_registry(
            workspace,
            allowed_executables=allowed_executables,
            repository_service=repository,
            semantic_provider=semantic_provider,
        )
        repository_core = RepositoryContextReasoningCore(
            verification_core,
            repository,
            tool_specs=registry.specs(),
        )
        super().__init__(
            workspace,
            repository_core,
            output_root,
            max_reasoning_steps=max_reasoning_steps,
            max_tool_actions=max_tool_actions,
            max_output_tokens=max_output_tokens,
            system_version=system_version,
            allowed_executables=allowed_executables,
        )
        self.repository = repository
        self.semantic_provider = semantic_provider
        self.registry = registry
        self.allowed_tools = tuple(spec.name for spec in registry.specs())
        self.verification_platform = platform
        self.verification_runs: list[VerificationRun] = []
        self.baseline_verification = False

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> MinimalVerifiedCodingTaskReport:
        commands = tuple(tuple(item for item in command) for command in verification_commands)
        if self.verification_platform.plan is None:
            self.verification_platform.configure(command_verification_plan(commands))
        plan = self.verification_platform.plan
        assert plan is not None
        self._write_verification_artifacts()

        projected_commands = commands or (("verification-plan", plan.fingerprint[:16]),)
        report = super().run(task, verification_commands=projected_commands)
        enhanced = MinimalVerifiedCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v23-minimal-verification",
                "verification_plan": plan,
                "verification_runs": tuple(self.verification_runs),
            }
        )
        self._write_report(enhanced)
        return enhanced

    def _instruction(
        self,
        task: str,
        commands: tuple[tuple[str, ...], ...],
    ) -> str:
        plan = self.verification_platform.plan
        if plan is None:
            return super()._instruction(task, commands)
        checks = [
            {
                "check_id": item.check_id,
                "name": item.name,
                "kind": item.kind,
                "requirement": item.requirement.value,
                "when_changed": list(item.when_changed),
            }
            for item in plan.checks
        ]
        return (
            "Perform this coding task autonomously inside the supplied workspace.\n"
            f"TASK: {task}\n"
            "Repository intelligence and the coding ACI are available. Inspect before editing, "
            "prefer targeted navigation and small verifiable changes, and use only declared "
            "Harness X tools. There is no persistent software-owned coding plan, commitment "
            "ledger, long-horizon task state, or project memory in this MINIMAL arm. "
            "Verification remains independent software authority: return status=complete only "
            "when you believe the task is ready for the typed verification plan. If software "
            "verification fails, use the returned working-state evidence to diagnose and repair.\n"
            f"VERIFICATION_PLAN_FINGERPRINT: {plan.fingerprint}\n"
            f"VERIFICATION_CHECKS: {checks}"
        )

    def _verify(
        self,
        commands: tuple[tuple[str, ...], ...],
        executor: ToolExecutor,
        recorder: TraceRecorder,
        task_id: TaskId,
    ) -> tuple[CodingVerificationResult, ...]:
        run = self.verification_platform.execute(
            run_kind="completion",
            executor=executor,
            recorder=recorder,
            task_id=task_id,
            routine_allowed_tools=self.allowed_tools,
            granted_permissions=_CODING_PERMISSIONS,
        )
        self.verification_runs.append(run)
        self._write_verification_artifacts()
        return self._legacy_projection(run)

    @staticmethod
    def _legacy_projection(run: VerificationRun) -> tuple[CodingVerificationResult, ...]:
        rows: list[CodingVerificationResult] = []
        for result in run.results:
            evidence = result.evidence
            argv = tuple(str(item) for item in evidence.get("argv", ()))
            if not argv:
                argv = ("harness-x-verification", result.check_id)
            blocking = (
                result.requirement == VerificationRequirement.REQUIRED
                and result.status
                in {
                    VerificationCheckStatus.FAILED,
                    VerificationCheckStatus.ERROR,
                }
            )
            returncode = 1 if blocking else 0
            if blocking and result.status == VerificationCheckStatus.ERROR:
                returncode = 125
            stdout = evidence.get("stdout", "")
            stderr = evidence.get("stderr", "")
            stdout = stdout if isinstance(stdout, str) else ""
            stderr = stderr if isinstance(stderr, str) else ""
            if result.failure_code:
                detail = json.dumps(
                    evidence,
                    sort_keys=True,
                    ensure_ascii=False,
                    default=str,
                )
                note = f"{result.check_id}:{result.failure_code} {detail[:1800]}"
                stderr = f"{stderr}\n{note}".strip()
            rows.append(
                CodingVerificationResult(
                    argv=argv,
                    returncode=returncode,
                    stdout=stdout,
                    stderr=stderr,
                )
            )
        return tuple(rows)

    def _write_verification_artifacts(self) -> None:
        plan = self.verification_platform.plan
        if plan is not None:
            self._atomic_json(
                self.output_root / "verification-plan.json",
                plan.model_dump(mode="json"),
            )
        self._atomic_json(
            self.output_root / "verification-runs.json",
            [item.model_dump(mode="json") for item in self.verification_runs],
        )

    @staticmethod
    def _atomic_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)


class MinimalVerifiedIsolatedRepositoryCodingTaskRuntime:
    """M24 isolation around the M34 MINIMAL repository+M25 runtime."""

    def __init__(
        self,
        source_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        verification_plan: VerificationPlan | None = None,
        isolation_root: str | Path | None = None,
        retention: IsolationRetention = IsolationRetention.ALWAYS,
        support_paths: Iterable[str] = (),
        **runtime_kwargs,
    ) -> None:
        self.source_root = Path(source_root).resolve()
        self.core = core
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.verification_plan = verification_plan
        self.isolation_root = isolation_root
        self.retention = IsolationRetention(retention)
        self.support_paths = tuple(support_paths)
        self.runtime_kwargs = dict(runtime_kwargs)

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> MinimalVerifiedIsolatedCodingTaskReport:
        manager = TaskWorkspaceIsolationManager(
            self.source_root,
            self.output_root / "isolation",
            isolation_root=self.isolation_root,
            retention=self.retention,
            support_paths=self.support_paths,
        )
        prepared = manager.prepare()
        runtime = MinimalVerifiedRepositoryCodingTaskRuntime(
            prepared.workspace_root,
            self.core,
            self.output_root,
            verification_plan=self.verification_plan,
            **self.runtime_kwargs,
        )
        try:
            report = runtime.run(task, verification_commands=verification_commands)
        except BaseException as exc:
            try:
                manager.finalize(succeeded=False)
            except Exception as finalize_exc:
                exc.add_note(
                    "M34 minimal isolation finalization also failed: "
                    f"{type(finalize_exc).__name__}: {finalize_exc}"
                )
            raise
        isolation = manager.finalize(succeeded=report.succeeded)
        enhanced = MinimalVerifiedIsolatedCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v24-isolated-minimal-verification",
                "isolation": isolation,
            }
        )
        (self.output_root / "coding-task-report.json").write_text(
            enhanced.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        return enhanced
