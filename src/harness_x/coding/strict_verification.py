"""Strict evidence semantics for M25 file-content verification.

A bounded workspace_read can prove that a needle is present in the observed prefix, but
it cannot prove absence when the read was truncated. This subclass converts those
otherwise ambiguous results into explicit verification errors instead of allowing a
false pass/fail conclusion.
"""

from __future__ import annotations

from harness_x.core import TaskId
from harness_x.core.provenance import Provenance
from harness_x.tools import ToolExecutor

from .verification import (
    FileContainsVerificationCheck,
    VerificationCheck,
    VerificationCheckResult,
    VerificationCheckStatus,
    VerificationPlatform,
)


class StrictVerificationPlatform(VerificationPlatform):
    """VerificationPlatform with fail-closed bounded content evidence."""

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
