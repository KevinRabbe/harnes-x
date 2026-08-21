"""M24 isolated wrapper around the qualified M23 repository-aware runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from harness_x.reasoning import ReasoningCore

from .isolation import (
    IsolationResult,
    IsolationRetention,
    TaskWorkspaceIsolationManager,
)
from .repository_runtime import RepositoryAwareAutonomousCodingTaskRuntime
from .runtime import CodingTaskReport


class IsolatedCodingTaskReport(CodingTaskReport):
    """Existing coding report plus the immutable source/task-workspace boundary."""

    schema_version: str = "coding-task-report-v2-isolated"
    isolation: IsolationResult


class IsolatedRepositoryCodingTaskRuntime:
    """Prepare an isolated workspace, run M23 there, then export the task delta.

    This wrapper does not change M22/M23 reasoning, tool, verification, phase, or
    completion semantics. It changes only the filesystem root handed to that runtime.
    """

    def __init__(
        self,
        source_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        isolation_root: str | Path | None = None,
        retention: IsolationRetention = IsolationRetention.ALWAYS,
        support_paths: Iterable[str] = (),
        max_reasoning_steps: int = 32,
        max_tool_actions: int = 48,
        max_output_tokens: int = 65536,
        system_version: str = "0.1.0a0+coding24-isolation",
        allowed_executables: frozenset[str] | None = None,
        baseline_verification: bool = True,
        max_idle_turns: int = 3,
        max_inspection_streak: int = 6,
        max_no_progress_streak: int = 4,
        max_same_failure_count: int = 3,
    ) -> None:
        self.source_root = Path(source_root).resolve()
        if not self.source_root.is_dir():
            raise ValueError("coding source workspace must be an existing directory")
        self.core = core
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.isolation_root = isolation_root
        self.retention = IsolationRetention(retention)
        self.support_paths = tuple(support_paths)
        self.max_reasoning_steps = max_reasoning_steps
        self.max_tool_actions = max_tool_actions
        self.max_output_tokens = max_output_tokens
        self.system_version = system_version
        self.allowed_executables = allowed_executables
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
    ) -> IsolatedCodingTaskReport:
        manager = TaskWorkspaceIsolationManager(
            self.source_root,
            self.output_root / "isolation",
            isolation_root=self.isolation_root,
            retention=self.retention,
            support_paths=self.support_paths,
        )
        prepared = manager.prepare()
        runtime = RepositoryAwareAutonomousCodingTaskRuntime(
            prepared.workspace_root,
            self.core,
            self.output_root,
            max_reasoning_steps=self.max_reasoning_steps,
            max_tool_actions=self.max_tool_actions,
            max_output_tokens=self.max_output_tokens,
            system_version=self.system_version,
            allowed_executables=self.allowed_executables,
            baseline_verification=self.baseline_verification,
            max_idle_turns=self.max_idle_turns,
            max_inspection_streak=self.max_inspection_streak,
            max_no_progress_streak=self.max_no_progress_streak,
            max_same_failure_count=self.max_same_failure_count,
        )
        try:
            report = runtime.run(
                task,
                verification_commands=verification_commands,
            )
        except BaseException:
            manager.finalize(succeeded=False)
            raise

        isolation = manager.finalize(succeeded=report.succeeded)
        payload = report.model_dump(mode="python")
        payload["schema_version"] = "coding-task-report-v2-isolated"
        payload["isolation"] = isolation
        return IsolatedCodingTaskReport.model_validate(payload)
