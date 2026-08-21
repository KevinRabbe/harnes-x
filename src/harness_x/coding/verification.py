"""Typed, software-owned verification plans and evidence for coding tasks."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from harness_x.core import (
    ActionProposal,
    CandidateId,
    SourceKind,
    TaskId,
    VerificationState,
)
from harness_x.core.provenance import Provenance
from harness_x.telemetry import TraceRecorder
from harness_x.tools import ToolExecutor


class VerificationRequirement(StrEnum):
    REQUIRED = "required"
    ADVISORY = "advisory"


class VerificationCheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class VerificationVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class CommandVerificationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["command"] = "command"
    check_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    name: str = Field(min_length=1, max_length=160)
    requirement: VerificationRequirement = VerificationRequirement.REQUIRED
    argv: tuple[str, ...] = Field(min_length=1, max_length=32)
    cwd: str = "."
    timeout_seconds: float = Field(default=120.0, gt=0.0, le=300.0)
    max_output_chars: int = Field(default=20000, ge=1000, le=100000)
    when_changed: tuple[str, ...] = ()

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("verification argv cannot contain blank entries")
        return value


class FileExistsVerificationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["file_exists"] = "file_exists"
    check_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    name: str = Field(min_length=1, max_length=160)
    requirement: VerificationRequirement = VerificationRequirement.REQUIRED
    path: str = Field(min_length=1, max_length=1000)
    when_changed: tuple[str, ...] = ()


class FileContainsVerificationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["file_contains"] = "file_contains"
    check_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    name: str = Field(min_length=1, max_length=160)
    requirement: VerificationRequirement = VerificationRequirement.REQUIRED
    path: str = Field(min_length=1, max_length=1000)
    needle: str = Field(min_length=1, max_length=4000)
    should_contain: bool = True
    case_sensitive: bool = True
    max_bytes: int = Field(default=262144, ge=1024, le=1048576)
    when_changed: tuple[str, ...] = ()


VerificationCheck = Annotated[
    CommandVerificationCheck
    | FileExistsVerificationCheck
    | FileContainsVerificationCheck,
    Field(discriminator="kind"),
]
_VERIFICATION_CHECK_ADAPTER = TypeAdapter(VerificationCheck)


class VerificationPlan(BaseModel):
    """Immutable software-owned verification policy for one coding task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "coding-verification-plan-v1"
    name: str = Field(default="coding verification", min_length=1, max_length=160)
    checks: tuple[VerificationCheck, ...] = Field(min_length=1, max_length=64)
    fail_fast_required: bool = True
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "VerificationPlan":
        ids = [item.check_id for item in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError("verification check IDs must be unique")
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        digest = hashlib.sha256(_canonical_bytes(material)).hexdigest()
        object.__setattr__(self, "fingerprint", digest)
        return self


class VerificationCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    check_id: str
    name: str
    kind: str
    requirement: VerificationRequirement
    status: VerificationCheckStatus
    applicable: bool
    failure_code: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def blocking_failure(self) -> bool:
        return self.requirement == VerificationRequirement.REQUIRED and self.status in {
            VerificationCheckStatus.FAILED,
            VerificationCheckStatus.ERROR,
        }


class VerificationRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "coding-verification-run-v1"
    run_id: str
    run_kind: str
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    workspace_fingerprint_before: str = Field(min_length=64, max_length=64)
    workspace_fingerprint_after: str = Field(min_length=64, max_length=64)
    workspace_stable: bool
    changed_files: tuple[str, ...] = ()
    verdict: VerificationVerdict
    results: tuple[VerificationCheckResult, ...]
    required_failures: tuple[str, ...] = ()
    advisory_failures: tuple[str, ...] = ()
    failure_signature: str | None = None
    run_fingerprint: str = Field(min_length=64, max_length=64)


_VERIFICATION_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".harness-x",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".next",
        ".turbo",
        "dist",
        "build",
        "coverage",
    }
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def _workspace_manifest(root: Path, *, max_files: int = 20000) -> dict[str, tuple[str, int]]:
    manifest: dict[str, tuple[str, int]] = {}
    count = 0
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in _VERIFICATION_IGNORED_DIRS)
        base = Path(current)
        for name in sorted(names):
            path = base / name
            relative = path.relative_to(root)
            if any(part in _VERIFICATION_IGNORED_DIRS for part in relative.parts):
                continue
            count += 1
            if count > max_files:
                raise RuntimeError(
                    f"verification workspace exceeds exact fingerprint limit of {max_files} files"
                )
            if path.is_symlink():
                payload = os.readlink(path).encode("utf-8", errors="surrogateescape")
                manifest[relative.as_posix()] = (
                    hashlib.sha256(b"symlink\0" + payload).hexdigest(),
                    len(payload),
                )
            elif path.is_file():
                try:
                    manifest[relative.as_posix()] = _hash_file(path)
                except OSError as exc:
                    raise RuntimeError(
                        f"cannot fingerprint verification file {relative.as_posix()}: {exc}"
                    ) from exc
    return manifest


def _workspace_fingerprint(manifest: dict[str, tuple[str, int]]) -> str:
    material = [(path, digest, size) for path, (digest, size) in sorted(manifest.items())]
    return hashlib.sha256(_canonical_bytes(material)).hexdigest()


def _changed_files(
    baseline: dict[str, tuple[str, int]], current: dict[str, tuple[str, int]]
) -> tuple[str, ...]:
    changed: list[str] = []
    for path in sorted(set(baseline) | set(current)):
        if baseline.get(path) != current.get(path):
            changed.append(path)
    return tuple(changed)


def _applicable(patterns: tuple[str, ...], changed: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    return any(fnmatch.fnmatch(path, pattern) for path in changed for pattern in patterns)


def command_verification_plan(
    commands: tuple[tuple[str, ...], ...],
    *,
    name: str = "coding verification",
) -> VerificationPlan:
    if not commands:
        raise ValueError("at least one verification command is required")
    checks = tuple(
        CommandVerificationCheck(
            check_id=f"command_{index:03d}",
            name=" ".join(command),
            argv=command,
        )
        for index, command in enumerate(commands, 1)
    )
    return VerificationPlan(name=name, checks=checks)


def load_verification_plan(path: str | Path) -> VerificationPlan:
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read verification plan {target}: {exc}") from exc
    try:
        return VerificationPlan.model_validate_json(text)
    except Exception as exc:
        raise ValueError(f"invalid verification plan {target}: {exc}") from exc


class VerificationPlatform:
    """Execute one immutable verification plan against exact workspace state."""

    def __init__(self, workspace_root: str | Path, plan: VerificationPlan | None = None) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        if not self.workspace_root.is_dir():
            raise ValueError("verification workspace must be an existing directory")
        self.plan = plan
        self._baseline_manifest = _workspace_manifest(self.workspace_root)
        self.latest_run: VerificationRun | None = None

    def configure(self, plan: VerificationPlan) -> None:
        if self.latest_run is not None:
            raise RuntimeError("verification plan cannot change after verification has started")
        self.plan = plan

    def latest_is_fresh(self) -> bool:
        run = self.latest_run
        plan = self.plan
        if run is None or plan is None or run.verdict != VerificationVerdict.PASS:
            return False
        if run.plan_fingerprint != plan.fingerprint or not run.workspace_stable:
            return False
        current = _workspace_fingerprint(_workspace_manifest(self.workspace_root))
        return current == run.workspace_fingerprint_after

    def context_projection(self) -> dict[str, Any]:
        plan = self.plan
        if plan is None:
            return {"configured": False}
        checks: list[dict[str, Any]] = []
        for item in plan.checks:
            row: dict[str, Any] = {
                "check_id": item.check_id,
                "name": item.name,
                "kind": item.kind,
                "requirement": item.requirement.value,
                "when_changed": list(item.when_changed),
            }
            if isinstance(item, CommandVerificationCheck):
                row.update({"argv": list(item.argv), "cwd": item.cwd})
            else:
                row["path"] = item.path
                if isinstance(item, FileContainsVerificationCheck):
                    row.update(
                        {
                            "should_contain": item.should_contain,
                            "needle": item.needle,
                            "case_sensitive": item.case_sensitive,
                        }
                    )
            checks.append(row)
        latest = None
        if self.latest_run is not None:
            latest = {
                "run_id": self.latest_run.run_id,
                "run_kind": self.latest_run.run_kind,
                "verdict": self.latest_run.verdict.value,
                "fresh": self.latest_is_fresh(),
                "workspace_stable": self.latest_run.workspace_stable,
                "changed_files": list(self.latest_run.changed_files[:80]),
                "required_failures": list(self.latest_run.required_failures),
                "advisory_failures": list(self.latest_run.advisory_failures),
                "failure_signature": self.latest_run.failure_signature,
                "results": [
                    {
                        "check_id": result.check_id,
                        "status": result.status.value,
                        "failure_code": result.failure_code,
                        "evidence": result.evidence,
                    }
                    for result in self.latest_run.results
                ],
            }
        return {
            "configured": True,
            "plan": {
                "name": plan.name,
                "fingerprint": plan.fingerprint,
                "fail_fast_required": plan.fail_fast_required,
                "checks": checks,
            },
            "latest_run": latest,
            "freshness_rule": (
                "A pass is authoritative only while both the verification-plan fingerprint "
                "and exact source-relevant workspace fingerprint remain unchanged."
            ),
        }

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
        plan = self.plan
        if plan is None:
            raise RuntimeError("verification platform has no configured plan")
        before_manifest = _workspace_manifest(self.workspace_root)
        before_fingerprint = _workspace_fingerprint(before_manifest)
        changed = _changed_files(self._baseline_manifest, before_manifest)
        results: list[VerificationCheckResult] = []
        blocked = False

        provenance = Provenance(
            source_kind=SourceKind.SYSTEM,
            source_ref=f"verification-plan:{plan.fingerprint}",
            created_at=recorder.clock.now(),
            system_version=recorder.system_version,
            trace_id=recorder.trace_id,
            verification=VerificationState.VERIFIED,
        )

        for check in plan.checks:
            applicable = _applicable(check.when_changed, changed)
            if blocked:
                results.append(
                    VerificationCheckResult(
                        check_id=check.check_id,
                        name=check.name,
                        kind=check.kind,
                        requirement=check.requirement,
                        status=VerificationCheckStatus.SKIPPED,
                        applicable=applicable,
                        failure_code="fail_fast",
                    )
                )
                continue
            if not applicable:
                results.append(
                    VerificationCheckResult(
                        check_id=check.check_id,
                        name=check.name,
                        kind=check.kind,
                        requirement=check.requirement,
                        status=VerificationCheckStatus.SKIPPED,
                        applicable=False,
                        evidence={"when_changed": list(check.when_changed)},
                    )
                )
                continue

            result = self._execute_check(
                check,
                executor=executor,
                task_id=task_id,
                provenance=provenance,
                routine_allowed_tools=routine_allowed_tools,
                granted_permissions=granted_permissions,
            )
            results.append(result)
            if plan.fail_fast_required and result.blocking_failure:
                blocked = True

        after_manifest = _workspace_manifest(self.workspace_root)
        after_fingerprint = _workspace_fingerprint(after_manifest)
        stable = before_fingerprint == after_fingerprint
        if not stable:
            results.append(
                VerificationCheckResult(
                    check_id="__workspace_stability__",
                    name="workspace remains source-stable during verification",
                    kind="workspace_stability",
                    requirement=VerificationRequirement.REQUIRED,
                    status=VerificationCheckStatus.FAILED,
                    applicable=True,
                    failure_code="workspace_mutated_during_verification",
                    evidence={
                        "before": before_fingerprint,
                        "after": after_fingerprint,
                    },
                )
            )

        required_failures = tuple(
            result.check_id for result in results if result.blocking_failure
        )
        advisory_failures = tuple(
            result.check_id
            for result in results
            if result.requirement == VerificationRequirement.ADVISORY
            and result.status in {VerificationCheckStatus.FAILED, VerificationCheckStatus.ERROR}
        )
        verdict = (
            VerificationVerdict.FAIL if required_failures else VerificationVerdict.PASS
        )
        failure_signature = None
        if required_failures:
            failure_material = [
                (result.check_id, result.failure_code, result.status.value)
                for result in results
                if result.check_id in required_failures
            ]
            failure_signature = hashlib.sha256(
                _canonical_bytes(failure_material)
            ).hexdigest()[:24]

        run_material = {
            "plan": plan.fingerprint,
            "kind": run_kind,
            "before": before_fingerprint,
            "after": after_fingerprint,
            "results": [item.model_dump(mode="json") for item in results],
        }
        run = VerificationRun(
            run_id=f"verification_{uuid.uuid4().hex[:24]}",
            run_kind=run_kind,
            plan_fingerprint=plan.fingerprint,
            workspace_fingerprint_before=before_fingerprint,
            workspace_fingerprint_after=after_fingerprint,
            workspace_stable=stable,
            changed_files=changed,
            verdict=verdict,
            results=tuple(results),
            required_failures=required_failures,
            advisory_failures=advisory_failures,
            failure_signature=failure_signature,
            run_fingerprint=hashlib.sha256(_canonical_bytes(run_material)).hexdigest(),
        )
        self.latest_run = run
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
        if isinstance(check, CommandVerificationCheck):
            proposal = ActionProposal(
                candidate_id=CandidateId.new(),
                task_id=task_id,
                tool_name="process_run",
                arguments={
                    "argv": list(check.argv),
                    "cwd": check.cwd,
                    "timeout_seconds": check.timeout_seconds,
                    "max_output_chars": check.max_output_chars,
                },
                provenance=provenance,
            )
            tool_result = executor.execute(
                proposal,
                routine_allowed_tools=routine_allowed_tools,
                granted_permissions=granted_permissions,
            )
            if not tool_result.succeeded:
                return VerificationCheckResult(
                    check_id=check.check_id,
                    name=check.name,
                    kind=check.kind,
                    requirement=check.requirement,
                    status=VerificationCheckStatus.ERROR,
                    applicable=True,
                    failure_code=f"tool_{tool_result.status.value}",
                    evidence={"error": tool_result.error or tool_result.status.value},
                )
            output = tool_result.output
            returncode = int(output.get("returncode", 125))
            return VerificationCheckResult(
                check_id=check.check_id,
                name=check.name,
                kind=check.kind,
                requirement=check.requirement,
                status=(
                    VerificationCheckStatus.PASSED
                    if returncode == 0
                    else VerificationCheckStatus.FAILED
                ),
                applicable=True,
                failure_code=(None if returncode == 0 else f"exit_{returncode}"),
                evidence={
                    "argv": list(check.argv),
                    "cwd": check.cwd,
                    "returncode": returncode,
                    "stdout": str(output.get("stdout", "")),
                    "stderr": str(output.get("stderr", "")),
                    "output_truncated": bool(output.get("output_truncated", False)),
                },
            )

        path = check.path
        proposal = ActionProposal(
            candidate_id=CandidateId.new(),
            task_id=task_id,
            tool_name="workspace_read",
            arguments={
                "path": path,
                "start_line": 1,
                "max_lines": 1000,
                "max_bytes": (
                    check.max_bytes
                    if isinstance(check, FileContainsVerificationCheck)
                    else 1048576
                ),
            },
            provenance=provenance,
        )
        tool_result = executor.execute(
            proposal,
            routine_allowed_tools=routine_allowed_tools,
            granted_permissions=granted_permissions,
        )
        if isinstance(check, FileExistsVerificationCheck):
            passed = tool_result.succeeded
            return VerificationCheckResult(
                check_id=check.check_id,
                name=check.name,
                kind=check.kind,
                requirement=check.requirement,
                status=(
                    VerificationCheckStatus.PASSED
                    if passed
                    else VerificationCheckStatus.FAILED
                ),
                applicable=True,
                failure_code=(None if passed else "file_missing_or_unreadable"),
                evidence={"path": path, "exists_and_readable": passed},
            )

        assert isinstance(check, FileContainsVerificationCheck)
        if not tool_result.succeeded:
            return VerificationCheckResult(
                check_id=check.check_id,
                name=check.name,
                kind=check.kind,
                requirement=check.requirement,
                status=VerificationCheckStatus.ERROR,
                applicable=True,
                failure_code="file_missing_or_unreadable",
                evidence={"path": path, "error": tool_result.error or tool_result.status.value},
            )
        content = str(tool_result.output.get("content", ""))
        haystack = content if check.case_sensitive else content.casefold()
        needle = check.needle if check.case_sensitive else check.needle.casefold()
        contains = needle in haystack
        passed = contains == check.should_contain
        return VerificationCheckResult(
            check_id=check.check_id,
            name=check.name,
            kind=check.kind,
            requirement=check.requirement,
            status=(VerificationCheckStatus.PASSED if passed else VerificationCheckStatus.FAILED),
            applicable=True,
            failure_code=(None if passed else "content_expectation_failed"),
            evidence={
                "path": path,
                "should_contain": check.should_contain,
                "needle": check.needle,
                "matched": contains,
                "case_sensitive": check.case_sensitive,
                "read_truncated": bool(tool_result.output.get("truncated", False)),
            },
        )


def parse_verification_check(payload: dict[str, Any]) -> VerificationCheck:
    """Public helper for callers that construct plan fragments dynamically."""

    return _VERIFICATION_CHECK_ADAPTER.validate_python(payload)
