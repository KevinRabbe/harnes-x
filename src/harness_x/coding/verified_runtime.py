"""M25 verification-platform integration for repository-aware isolated coding."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Iterable

from harness_x.core import TaskId
from harness_x.reasoning import RawReasoningOutput, ReasoningCore, ReasoningCoreInfo
from harness_x.reasoning.context_builder import ContextBuildResult
from harness_x.telemetry import TraceRecorder
from harness_x.tools import ToolExecutor

from .isolated_runtime import IsolatedCodingTaskReport
from .isolation import IsolationResult, IsolationRetention, TaskWorkspaceIsolationManager
from .repository_runtime import RepositoryAwareAutonomousCodingTaskRuntime
from .runtime import CodingTaskReport, CodingVerificationResult, _CODING_PERMISSIONS
from .strict_verification import StrictVerificationPlatform
from .verification import (
    VerificationCheckStatus,
    VerificationPlan,
    VerificationRequirement,
    VerificationRun,
    command_verification_plan,
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _bounded_projection(
    platform: StrictVerificationPlatform, *, max_chars: int = 7600
) -> dict:
    projection = copy.deepcopy(platform.context_projection())
    serialized = _canonical(projection)
    if len(serialized) <= max_chars:
        return projection

    latest = projection.get("latest_run")
    if isinstance(latest, dict):
        results = latest.get("results")
        if isinstance(results, list):
            for row in results:
                if not isinstance(row, dict):
                    continue
                evidence = row.get("evidence")
                if not isinstance(evidence, dict):
                    continue
                for key in ("stdout", "stderr"):
                    value = evidence.get(key)
                    if isinstance(value, str) and len(value) > 500:
                        evidence[key] = value[:500] + "...<truncated>"
            latest["results"] = results[:16]
    serialized = _canonical(projection)
    if len(serialized) <= max_chars:
        return projection

    if isinstance(latest, dict):
        latest.pop("results", None)
        changed = latest.get("changed_files")
        if isinstance(changed, list):
            latest["changed_files"] = changed[:32]
    serialized = _canonical(projection)
    if len(serialized) <= max_chars:
        return projection

    plan = projection.get("plan")
    if isinstance(plan, dict):
        checks = plan.get("checks")
        if isinstance(checks, list):
            plan["checks"] = [
                {
                    "check_id": row.get("check_id"),
                    "kind": row.get("kind"),
                    "requirement": row.get("requirement"),
                    "name": row.get("name"),
                }
                for row in checks[:32]
                if isinstance(row, dict)
            ]
    return projection


class VerificationContextReasoningCore:
    """Add bounded software-owned verification state to model context."""

    def __init__(
        self, core: ReasoningCore, platform: StrictVerificationPlatform
    ) -> None:
        self.core = core
        self.platform = platform

    @property
    def info(self) -> ReasoningCoreInfo:
        return self.core.info

    def generate(self, context: ContextBuildResult) -> RawReasoningOutput:
        payload = copy.deepcopy(context.payload)
        sections = payload.setdefault("sections", {})
        sections["verification_platform"] = {
            "authority": "software_owned_verification_authority",
            "rule": (
                "Only Harness X verification results establish check status. A pass is fresh "
                "only for the exact plan and workspace fingerprints reported here."
            ),
            "data": _bounded_projection(self.platform),
        }
        serialized = _canonical(payload)
        fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        enriched = context.model_copy(
            update={
                "payload": payload,
                "serialized": serialized,
                "fingerprint": fingerprint,
                "char_count": len(serialized),
            }
        )
        return self.core.generate(enriched)

    def close(self) -> None:
        close = getattr(self.core, "close", None)
        if callable(close):
            close()


class VerifiedCodingTaskReport(CodingTaskReport):
    schema_version: str = "coding-task-report-v3-verification-platform"
    verification_plan: VerificationPlan
    verification_runs: tuple[VerificationRun, ...] = ()


class VerifiedIsolatedCodingTaskReport(VerifiedCodingTaskReport):
    schema_version: str = "coding-task-report-v4-isolated-verification-platform"
    isolation: IsolationResult


class VerifiedRepositoryCodingTaskRuntime(RepositoryAwareAutonomousCodingTaskRuntime):
    """M23/M22 runtime whose verifier is backed by a typed M25 plan."""

    def __init__(
        self,
        workspace_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        verification_plan: VerificationPlan | None = None,
        max_reasoning_steps: int = 32,
        max_tool_actions: int = 48,
        max_output_tokens: int = 65536,
        system_version: str = "0.1.0a0+coding25-verification",
        allowed_executables: frozenset[str] | None = None,
        baseline_verification: bool = True,
        max_idle_turns: int = 3,
        max_inspection_streak: int = 6,
        max_no_progress_streak: int = 4,
        max_same_failure_count: int = 3,
    ) -> None:
        platform = StrictVerificationPlatform(workspace_root, verification_plan)
        verification_core = VerificationContextReasoningCore(core, platform)
        super().__init__(
            workspace_root,
            verification_core,
            output_root,
            max_reasoning_steps=max_reasoning_steps,
            max_tool_actions=max_tool_actions,
            max_output_tokens=max_output_tokens,
            system_version=system_version,
            allowed_executables=allowed_executables,
            baseline_verification=baseline_verification,
            max_idle_turns=max_idle_turns,
            max_inspection_streak=max_inspection_streak,
            max_no_progress_streak=max_no_progress_streak,
            max_same_failure_count=max_same_failure_count,
        )
        self.verification_platform = platform
        self.verification_runs: list[VerificationRun] = []
        self._verification_call_count = 0

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> VerifiedCodingTaskReport:
        commands = tuple(tuple(item for item in command) for command in verification_commands)
        if self.verification_platform.plan is None:
            self.verification_platform.configure(command_verification_plan(commands))
        plan = self.verification_platform.plan
        assert plan is not None
        self._write_verification_artifacts()

        # AutonomousCodingTaskRuntime currently requires a non-empty command projection.
        # The M25 verifier ignores this tuple and executes the typed plan instead. Preserve
        # real command checks when available for backward-compatible active-state display;
        # otherwise use a non-executed sentinel and expose the authoritative plan through
        # the verification_platform context section.
        projected_commands = commands or (("verification-plan", plan.fingerprint[:16]),)
        report = super().run(task, verification_commands=projected_commands)
        enhanced = VerifiedCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v3-verification-platform",
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
            "Repository intelligence and the coding ACI are available. Prefer targeted "
            "navigation and small verifiable edits. Verification is software-owned: the "
            "typed verification_platform context section is authoritative over any legacy "
            "verification_commands projection. Advisory failures do not block completion, "
            "but required failures do. Do not claim a check passed unless Harness X reports "
            "it passed.\n"
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
        self._verification_call_count += 1
        run_kind = (
            "baseline"
            if self.baseline_verification and self._verification_call_count == 1
            else "completion"
        )
        run = self.verification_platform.execute(
            run_kind=run_kind,
            executor=executor,
            recorder=recorder,
            task_id=task_id,
            routine_allowed_tools=self.allowed_tools,
            granted_permissions=_CODING_PERMISSIONS,
        )
        self.verification_runs.append(run)
        self._write_verification_artifacts()
        return self._legacy_projection(run)

    def _legacy_projection(
        self, run: VerificationRun
    ) -> tuple[CodingVerificationResult, ...]:
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
            stdout = ""
            stderr = ""
            if isinstance(evidence.get("stdout"), str):
                stdout = evidence["stdout"]
            if isinstance(evidence.get("stderr"), str):
                stderr = evidence["stderr"]
            if result.failure_code:
                detail = json.dumps(
                    evidence, sort_keys=True, ensure_ascii=False, default=str
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

    def _verification_failure_signature(
        self, verification: tuple[CodingVerificationResult, ...]
    ) -> str | None:
        """Use M25's canonical typed failure identity for M22 repetition control."""

        if self.verification_runs:
            return self.verification_runs[-1].failure_signature
        return super()._verification_failure_signature(verification)

    def _completion_evidence_refs(
        self,
        verification: tuple[CodingVerificationResult, ...],
        *,
        context_fingerprint: str | None,
    ) -> tuple[str, ...]:
        """Bind completion to the exact typed run, plan, and verified workspace state."""

        if not self.verification_runs:
            return super()._completion_evidence_refs(
                verification, context_fingerprint=context_fingerprint
            )
        run = self.verification_runs[-1]
        refs = [
            f"verification-run:{run.run_fingerprint}",
            f"verification-plan:{run.plan_fingerprint}",
            f"workspace:{run.workspace_fingerprint_after}",
        ]
        if context_fingerprint:
            refs.append(f"reasoning:{context_fingerprint}")
        return tuple(refs)

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


class VerifiedIsolatedRepositoryCodingTaskRuntime:
    """M24 isolation around the M25 typed verification runtime."""

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
        max_reasoning_steps: int = 32,
        max_tool_actions: int = 48,
        max_output_tokens: int = 65536,
        system_version: str = "0.1.0a0+coding25-isolated-verification",
        allowed_executables: frozenset[str] | None = None,
        baseline_verification: bool = True,
        max_idle_turns: int = 3,
        max_inspection_streak: int = 6,
        max_no_progress_streak: int = 4,
        max_same_failure_count: int = 3,
    ) -> None:
        self.source_root = Path(source_root).resolve()
        self.core = core
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.verification_plan = verification_plan
        self.isolation_root = isolation_root
        self.retention = IsolationRetention(retention)
        self.support_paths = tuple(support_paths)
        self.runtime_kwargs = {
            "max_reasoning_steps": max_reasoning_steps,
            "max_tool_actions": max_tool_actions,
            "max_output_tokens": max_output_tokens,
            "system_version": system_version,
            "allowed_executables": allowed_executables,
            "baseline_verification": baseline_verification,
            "max_idle_turns": max_idle_turns,
            "max_inspection_streak": max_inspection_streak,
            "max_no_progress_streak": max_no_progress_streak,
            "max_same_failure_count": max_same_failure_count,
        }

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> VerifiedIsolatedCodingTaskReport:
        manager = TaskWorkspaceIsolationManager(
            self.source_root,
            self.output_root / "isolation",
            isolation_root=self.isolation_root,
            retention=self.retention,
            support_paths=self.support_paths,
        )
        prepared = manager.prepare()
        runtime = VerifiedRepositoryCodingTaskRuntime(
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
                    "M25 isolation finalization also failed: "
                    f"{type(finalize_exc).__name__}: {finalize_exc}"
                )
            raise

        isolation = manager.finalize(succeeded=report.succeeded)
        enhanced = VerifiedIsolatedCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v4-isolated-verification-platform",
                "isolation": isolation,
            }
        )
        (self.output_root / "coding-task-report.json").write_text(
            enhanced.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return enhanced
