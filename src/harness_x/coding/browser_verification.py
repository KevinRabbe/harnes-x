"""Typed browser/application verification layered on M25 code evidence."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Annotated, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from harness_x.browser import ApplicationBrowserSession, BrowserSelector

from .verification import (
    VerificationCheckStatus,
    VerificationRequirement,
    VerificationVerdict,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


class BrowserPageVerificationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["browser_page"] = "browser_page"
    check_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    name: str = Field(min_length=1, max_length=160)
    requirement: VerificationRequirement = VerificationRequirement.REQUIRED
    path: str = Field(default="/", min_length=1, max_length=1000)
    title_contains: str | None = Field(default=None, max_length=500)
    snapshot_contains: tuple[str, ...] = Field(default=(), max_length=20)
    snapshot_excludes: tuple[str, ...] = Field(default=(), max_length=20)


class BrowserConsoleVerificationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["browser_console"] = "browser_console"
    check_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    name: str = Field(min_length=1, max_length=160)
    requirement: VerificationRequirement = VerificationRequirement.REQUIRED
    path: str = Field(default="/", min_length=1, max_length=1000)
    forbidden_console_levels: tuple[str, ...] = ("error",)
    require_no_page_errors: bool = True


class BrowserInteractionAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["click", "fill", "select"]
    selector: BrowserSelector
    value: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def validate_value(self) -> "BrowserInteractionAction":
        if self.kind in {"fill", "select"} and self.value is None:
            raise ValueError(f"browser {self.kind} action requires value")
        if self.kind == "click" and self.value is not None:
            raise ValueError("browser click action cannot set value")
        return self


class BrowserInteractionVerificationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["browser_interaction"] = "browser_interaction"
    check_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    name: str = Field(min_length=1, max_length=160)
    requirement: VerificationRequirement = VerificationRequirement.REQUIRED
    path: str = Field(default="/", min_length=1, max_length=1000)
    actions: tuple[BrowserInteractionAction, ...] = Field(min_length=1, max_length=20)
    snapshot_contains: tuple[str, ...] = Field(default=(), max_length=20)
    snapshot_excludes: tuple[str, ...] = Field(default=(), max_length=20)


BrowserVerificationCheck = Annotated[
    BrowserPageVerificationCheck
    | BrowserConsoleVerificationCheck
    | BrowserInteractionVerificationCheck,
    Field(discriminator="kind"),
]
_BROWSER_CHECK_ADAPTER = TypeAdapter(BrowserVerificationCheck)


class BrowserVerificationPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "browser-verification-plan-v1"
    name: str = Field(default="browser verification", min_length=1, max_length=160)
    checks: tuple[BrowserVerificationCheck, ...] = Field(min_length=1, max_length=32)
    fail_fast_required: bool = True
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "BrowserVerificationPlan":
        ids = [item.check_id for item in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError("browser verification check IDs must be unique")
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", hashlib.sha256(_canonical(material)).hexdigest())
        return self


class BrowserVerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    check_id: str
    name: str
    kind: str
    requirement: VerificationRequirement
    status: VerificationCheckStatus
    failure_code: str | None = None
    evidence: dict[str, object] = Field(default_factory=dict)

    @property
    def blocking_failure(self) -> bool:
        return self.requirement == VerificationRequirement.REQUIRED and self.status in {
            VerificationCheckStatus.FAILED,
            VerificationCheckStatus.ERROR,
        }


class BrowserVerificationRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "browser-verification-run-v1"
    run_id: str
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    verdict: VerificationVerdict
    results: tuple[BrowserVerificationResult, ...]
    required_failures: tuple[str, ...] = ()
    advisory_failures: tuple[str, ...] = ()
    failure_signature: str | None = None
    code_verification_fresh_after: bool
    run_fingerprint: str = Field(min_length=64, max_length=64)


class BrowserVerificationPlatform:
    def __init__(
        self,
        session: ApplicationBrowserSession,
        plan: BrowserVerificationPlan,
    ) -> None:
        self.session = session
        self.plan = plan
        self.latest_run: BrowserVerificationRun | None = None

    def execute(
        self,
        *,
        code_freshness_check: Callable[[], bool],
    ) -> BrowserVerificationRun:
        results: list[BrowserVerificationResult] = []
        blocked = False
        for check in self.plan.checks:
            if blocked:
                results.append(
                    BrowserVerificationResult(
                        check_id=check.check_id,
                        name=check.name,
                        kind=check.kind,
                        requirement=check.requirement,
                        status=VerificationCheckStatus.SKIPPED,
                        failure_code="fail_fast",
                    )
                )
                continue
            result = self._execute_check(check)
            results.append(result)
            if self.plan.fail_fast_required and result.blocking_failure:
                blocked = True

        code_fresh = code_freshness_check()
        if not code_fresh:
            results.append(
                BrowserVerificationResult(
                    check_id="__code_verification_freshness__",
                    name="code verification remains fresh after browser execution",
                    kind="code_verification_freshness",
                    requirement=VerificationRequirement.REQUIRED,
                    status=VerificationCheckStatus.ERROR,
                    failure_code="code_verification_stale_after_browser",
                )
            )

        required = tuple(item.check_id for item in results if item.blocking_failure)
        advisory = tuple(
            item.check_id
            for item in results
            if item.requirement == VerificationRequirement.ADVISORY
            and item.status in {VerificationCheckStatus.FAILED, VerificationCheckStatus.ERROR}
        )
        failure_signature = None
        if required:
            material = [
                (item.check_id, item.failure_code, item.status.value)
                for item in results
                if item.check_id in required
            ]
            failure_signature = hashlib.sha256(_canonical(material)).hexdigest()[:24]
        verdict = VerificationVerdict.FAIL if required else VerificationVerdict.PASS
        material = {
            "plan": self.plan.fingerprint,
            "results": [item.model_dump(mode="json") for item in results],
            "code_fresh": code_fresh,
        }
        run = BrowserVerificationRun(
            run_id=f"browser_verification_{uuid.uuid4().hex[:20]}",
            plan_fingerprint=self.plan.fingerprint,
            verdict=verdict,
            results=tuple(results),
            required_failures=required,
            advisory_failures=advisory,
            failure_signature=failure_signature,
            code_verification_fresh_after=code_fresh,
            run_fingerprint=hashlib.sha256(_canonical(material)).hexdigest(),
        )
        self.latest_run = run
        return run

    def _execute_check(self, check: BrowserVerificationCheck) -> BrowserVerificationResult:
        try:
            observation = self.session.open(check.path)
            if isinstance(check, BrowserInteractionVerificationCheck):
                for action in check.actions:
                    if action.kind == "click":
                        observation = self.session.click(action.selector)
                    elif action.kind == "fill":
                        assert action.value is not None
                        observation = self.session.fill(action.selector, action.value)
                    else:
                        assert action.value is not None
                        observation = self.session.select(action.selector, action.value)
                return self._snapshot_result(
                    check,
                    observation.aria_snapshot,
                    observation.title,
                    check.snapshot_contains,
                    check.snapshot_excludes,
                )
            if isinstance(check, BrowserConsoleVerificationCheck):
                forbidden = {item.casefold() for item in check.forbidden_console_levels}
                console_hits = [
                    item.model_dump(mode="json")
                    for item in observation.console_messages
                    if item.level.casefold() in forbidden
                ]
                page_errors = list(observation.page_errors) if check.require_no_page_errors else []
                passed = not console_hits and not page_errors
                return BrowserVerificationResult(
                    check_id=check.check_id,
                    name=check.name,
                    kind=check.kind,
                    requirement=check.requirement,
                    status=(
                        VerificationCheckStatus.PASSED
                        if passed
                        else VerificationCheckStatus.FAILED
                    ),
                    failure_code=(None if passed else "browser_console_or_page_error"),
                    evidence={
                        "url": observation.url,
                        "forbidden_console_messages": console_hits[:30],
                        "page_errors": page_errors[:30],
                    },
                )
            assert isinstance(check, BrowserPageVerificationCheck)
            return self._snapshot_result(
                check,
                observation.aria_snapshot,
                observation.title,
                check.snapshot_contains,
                check.snapshot_excludes,
            )
        except Exception as exc:
            evidence: dict[str, object] = {
                "error": f"{type(exc).__name__}: {exc}"[:4000],
            }
            try:
                screenshot = self.session.screenshot(
                    f"verification/{check.check_id}-failure.png", full_page=True
                )
                evidence["screenshot_path"] = screenshot.path
            except Exception as screenshot_exc:
                evidence["screenshot_error"] = (
                    f"{type(screenshot_exc).__name__}: {screenshot_exc}"[:1000]
                )
            return BrowserVerificationResult(
                check_id=check.check_id,
                name=check.name,
                kind=check.kind,
                requirement=check.requirement,
                status=VerificationCheckStatus.ERROR,
                failure_code="browser_check_error",
                evidence=evidence,
            )

    @staticmethod
    def _snapshot_result(
        check: BrowserPageVerificationCheck | BrowserInteractionVerificationCheck,
        snapshot: str,
        title: str,
        required_fragments: tuple[str, ...],
        forbidden_fragments: tuple[str, ...],
    ) -> BrowserVerificationResult:
        missing = [item for item in required_fragments if item not in snapshot]
        forbidden = [item for item in forbidden_fragments if item in snapshot]
        title_missing = (
            isinstance(check, BrowserPageVerificationCheck)
            and check.title_contains is not None
            and check.title_contains not in title
        )
        passed = not missing and not forbidden and not title_missing
        return BrowserVerificationResult(
            check_id=check.check_id,
            name=check.name,
            kind=check.kind,
            requirement=check.requirement,
            status=(
                VerificationCheckStatus.PASSED
                if passed
                else VerificationCheckStatus.FAILED
            ),
            failure_code=(None if passed else "browser_expectation_failed"),
            evidence={
                "title": title,
                "title_contains": (
                    check.title_contains
                    if isinstance(check, BrowserPageVerificationCheck)
                    else None
                ),
                "missing_snapshot_fragments": missing,
                "forbidden_snapshot_fragments": forbidden,
                "snapshot_excerpt": snapshot[:12000],
            },
        )

    def context_projection(self) -> dict[str, object]:
        latest = self.latest_run
        return {
            "plan": {
                "name": self.plan.name,
                "fingerprint": self.plan.fingerprint,
                "checks": [
                    {
                        "check_id": item.check_id,
                        "kind": item.kind,
                        "name": item.name,
                        "requirement": item.requirement.value,
                        "path": item.path,
                    }
                    for item in self.plan.checks
                ],
            },
            "latest_run": None if latest is None else latest.model_dump(mode="json"),
        }


def load_browser_verification_plan(path: str) -> BrowserVerificationPlan:
    try:
        text = open(path, "r", encoding="utf-8").read()
    except OSError as exc:
        raise ValueError(f"cannot read browser verification plan {path}: {exc}") from exc
    try:
        return BrowserVerificationPlan.model_validate_json(text)
    except Exception as exc:
        raise ValueError(f"invalid browser verification plan {path}: {exc}") from exc


def parse_browser_verification_check(payload: dict[str, object]) -> BrowserVerificationCheck:
    return _BROWSER_CHECK_ADAPTER.validate_python(payload)
