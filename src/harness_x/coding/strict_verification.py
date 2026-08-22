"""Strict evidence semantics for M25 file-content verification.

A bounded workspace_read can prove that a needle is present in the observed prefix, but
it cannot prove absence when the read was truncated. This subclass converts those
otherwise ambiguous results into explicit verification errors instead of allowing a
false pass/fail conclusion.
"""

from __future__ import annotations

from harness_x.core import EventType, TaskId
from harness_x.core.provenance import Provenance
from harness_x.telemetry import TraceRecorder
from harness_x.tools import ToolExecutor

from .verification import (
    FileContainsVerificationCheck,
    VerificationCheck,
    VerificationCheckResult,
    VerificationCheckStatus,
    VerificationPlatform,
    VerificationRun,
)


class StrictVerificationPlatform(VerificationPlatform):
    """VerificationPlatform with fail-closed bounded content evidence."""

    def execute(
        self,
        *,
        run_kind: str,
        executor: ToolExecutor,
        recorder: TraceRecorder,
        task_id: TaskId,
        routine_allowed_tools: tuple[str, ...],
        granted_permissions: frozenset[str],
    ) -> VerificationRun:
        run = super().execute(
            run_kind=run_kind,
            executor=executor,
            recorder=recorder,
            task_id=task_id,
            routine_allowed_tools=routine_allowed_tools,
            granted_permissions=granted_permissions,
        )
        recorder.emit(
            EventType.VERIFICATION_COMPLETED,
            "coding.verification",
            output_refs=(f"verification-run:{run.run_fingerprint}",),
            metadata={
                "run_id": run.run_id,
                "run_kind": run.run_kind,
                "verdict": run.verdict.value,
                "plan_fingerprint": run.plan_fingerprint,
                "workspace_stable": run.workspace_stable,
                "required_failures": list(run.required_failures),
                "advisory_failures": list(run.advisory_failures),
            },
        )
        return run

    def _execute_check(
        self,
        check: VerificationCheck,
        *,
        executor: ToolExecutor,
        task_id: TaskId,
        provenance: Provenance,
        routine_allowed_tools: tuple[str, ...],
        granted_permissions: frozenset[str],
    ) -> VerificationCheckResult:
        result = super()._execute_check(
            check,
            executor=executor,
            task_id=task_id,
            provenance=provenance,
            routine_allowed_tools=routine_allowed_tools,
            granted_permissions=granted_permissions,
        )
        if not isinstance(check, FileContainsVerificationCheck):
            return result

        read_truncated = bool(result.evidence.get("read_truncated", False))
        matched = bool(result.evidence.get("matched", False))
        if not read_truncated or matched:
            # Finding the needle is conclusive even if later bytes were omitted. This
            # proves a positive assertion and disproves a negative assertion.
            return result

        evidence = dict(result.evidence)
        evidence["indeterminate_reason"] = (
            "needle was not observed, but workspace_read ended before the file did"
        )
        return VerificationCheckResult(
            check_id=result.check_id,
            name=result.name,
            kind=result.kind,
            requirement=result.requirement,
            status=VerificationCheckStatus.ERROR,
            applicable=result.applicable,
            failure_code="file_content_indeterminate_truncated",
            evidence=evidence,
        )
