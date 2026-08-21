"""M26 browser/application-aware coding runtime layered on M25 verification."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Iterable

from harness_x.browser import (
    ApplicationBrowserSession,
    ApplicationServerSpec,
    BrowserProviderFactory,
)
from harness_x.reasoning import RawReasoningOutput, ReasoningCore, ReasoningCoreInfo
from harness_x.reasoning.context_builder import ContextBuildResult
from harness_x.repository import RepositorySemanticProvider
from harness_x.tools import ToolSpec
from harness_x.tools.coding_browser import build_browser_coding_registry

from .browser_verification import (
    BrowserVerificationPlan,
    BrowserVerificationPlatform,
    BrowserVerificationRun,
)
from .isolation import IsolationResult, IsolationRetention, TaskWorkspaceIsolationManager
from .runtime import CodingVerificationResult
from .verification import (
    VerificationCheckStatus,
    VerificationPlan,
    VerificationVerdict,
)
from .verified_runtime import (
    VerifiedCodingTaskReport,
    VerifiedRepositoryCodingTaskRuntime,
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _compact_tool_manifest(specs: Iterable[ToolSpec]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for spec in specs:
        if not spec.name.startswith("browser_"):
            continue
        rows.append(
            {
                "name": spec.name,
                "version": spec.version,
                "required": list(spec.input_schema.get("required", [])),
                "permissions": list(spec.permissions),
                "side_effect_level": spec.side_effect_level.value,
            }
        )
    return rows


class BrowserContextReasoningCore:
    """Inject bounded app/browser state after M23/M25 context enrichment."""

    def __init__(
        self,
        core: ReasoningCore,
        session: ApplicationBrowserSession,
        browser_verification: BrowserVerificationPlatform,
        *,
        max_total_chars: int = 56_000,
    ) -> None:
        self.core = core
        self.session = session
        self.browser_verification = browser_verification
        self.max_total_chars = max_total_chars
        self.tool_manifest: list[dict[str, object]] = []

    @property
    def info(self) -> ReasoningCoreInfo:
        return self.core.info

    def set_tool_specs(self, specs: Iterable[ToolSpec]) -> None:
        self.tool_manifest = _compact_tool_manifest(specs)

    def generate(self, context: ContextBuildResult) -> RawReasoningOutput:
        payload = copy.deepcopy(context.payload)
        sections = payload.setdefault("sections", {})
        session_projection = self.session.context_projection()
        sections["browser_application"] = {
            "authority": "software_owned_local_application_and_browser_boundary",
            "rule": (
                "Browser tools may inspect/interact only with the declared local application. "
                "Browser state is evidence, not completion authority. Browser verification is "
                "independent software evidence and may invalidate a completion claim."
            ),
            "tool_manifest": self.tool_manifest,
            "session": session_projection,
            "verification": self.browser_verification.context_projection(),
        }
        serialized = _canonical(payload)
        if len(serialized) > self.max_total_chars:
            latest = session_projection.get("latest_observation")
            if isinstance(latest, dict):
                snapshot = str(latest.get("aria_snapshot", ""))
                latest["aria_snapshot"] = snapshot[:4000]
                latest["aria_truncated"] = True
                latest["console_messages"] = list(latest.get("console_messages", []))[-10:]
                latest["page_errors"] = list(latest.get("page_errors", []))[-10:]
            serialized = _canonical(payload)
        if len(serialized) > self.max_total_chars:
            session_projection["latest_observation"] = {
                "note": "browser observation omitted by final context bound; use browser_snapshot"
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


class BrowserVerifiedCodingTaskReport(VerifiedCodingTaskReport):
    schema_version: str = "coding-task-report-v5-browser-verification"
    browser_verification_plan: BrowserVerificationPlan
    browser_verification_runs: tuple[BrowserVerificationRun, ...] = ()


class BrowserVerifiedIsolatedCodingTaskReport(BrowserVerifiedCodingTaskReport):
    schema_version: str = "coding-task-report-v6-isolated-browser-verification"
    isolation: IsolationResult


class BrowserVerifiedRepositoryCodingTaskRuntime(VerifiedRepositoryCodingTaskRuntime):
    """M25 verified runtime plus local browser ACI and browser verification."""

    def __init__(
        self,
        workspace_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        application: ApplicationServerSpec,
        browser_verification_plan: BrowserVerificationPlan,
        browser_provider_factory: BrowserProviderFactory,
        verification_plan: VerificationPlan | None = None,
        semantic_provider: RepositorySemanticProvider | None = None,
        max_reasoning_steps: int = 32,
        max_tool_actions: int = 48,
        max_output_tokens: int = 65536,
        system_version: str = "0.1.0a0+coding26-browser",
        allowed_executables: frozenset[str] | None = None,
        baseline_verification: bool = True,
        max_idle_turns: int = 3,
        max_inspection_streak: int = 6,
        max_no_progress_streak: int = 4,
        max_same_failure_count: int = 3,
    ) -> None:
        workspace = Path(workspace_root).resolve()
        output = Path(output_root).resolve()
        session = ApplicationBrowserSession(
            workspace,
            output / "browser-application",
            application,
            browser_provider_factory,
            allowed_executables=allowed_executables,
        )
        browser_verification = BrowserVerificationPlatform(
            session,
            browser_verification_plan,
        )
        browser_context_core = BrowserContextReasoningCore(
            core,
            session,
            browser_verification,
        )
        super().__init__(
            workspace,
            browser_context_core,
            output,
            verification_plan=verification_plan,
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
        self.browser_session = session
        self.browser_verification = browser_verification
        self.browser_context_core = browser_context_core
        self.browser_verification_runs: list[BrowserVerificationRun] = []
        self._browser_run_for_current_verification: BrowserVerificationRun | None = None
        self.semantic_provider = semantic_provider
        registry = build_browser_coding_registry(
            workspace,
            session,
            allowed_executables=allowed_executables,
            repository_service=self.repository,
            semantic_provider=semantic_provider,
        )
        self.registry = registry
        self.allowed_tools = tuple(spec.name for spec in registry.specs())
        browser_context_core.set_tool_specs(registry.specs())
        self._write_browser_artifacts()

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> BrowserVerifiedCodingTaskReport:
        try:
            report = super().run(task, verification_commands=verification_commands)
            enhanced = BrowserVerifiedCodingTaskReport.model_validate(
                {
                    **report.model_dump(mode="python"),
                    "schema_version": "coding-task-report-v5-browser-verification",
                    "browser_verification_plan": self.browser_verification.plan,
                    "browser_verification_runs": tuple(self.browser_verification_runs),
                }
            )
            self._write_report(enhanced)
            return enhanced
        finally:
            self._write_browser_artifacts()
            self.browser_session.close()

    def _instruction(
        self,
        task: str,
        commands: tuple[tuple[str, ...], ...],
    ) -> str:
        base = super()._instruction(task, commands)
        return (
            base
            + "\nBROWSER: A declared local application is available through browser_* tools. "
            "Use browser_snapshot for structured accessibility state and semantic role/label/"
            "text/test-id selectors for interactions. Browser actions do not establish "
            "completion. Independent browser verification runs only after code verification "
            "passes."
        )

    def _verify(self, commands, executor, recorder, task_id):
        self._browser_run_for_current_verification = None
        code_rows = super()._verify(commands, executor, recorder, task_id)
        code_run = self.verification_runs[-1]
        if code_run.verdict != VerificationVerdict.PASS:
            return code_rows

        self.browser_session.reset_browser_client(purpose="verification")
        browser_run = self.browser_verification.execute(
            code_freshness_check=self.verification_platform.latest_is_fresh,
        )
        self._browser_run_for_current_verification = browser_run
        self.browser_verification_runs.append(browser_run)
        self._write_browser_artifacts()
        return (*code_rows, *self._legacy_browser_projection(browser_run))

    @staticmethod
    def _legacy_browser_projection(
        run: BrowserVerificationRun,
    ) -> tuple[CodingVerificationResult, ...]:
        rows: list[CodingVerificationResult] = []
        for result in run.results:
            blocking = result.blocking_failure
            returncode = 0
            if blocking:
                returncode = 125 if result.status == VerificationCheckStatus.ERROR else 1
            stderr = ""
            if result.failure_code:
                detail = json.dumps(
                    result.evidence,
                    sort_keys=True,
                    ensure_ascii=False,
                    default=str,
                )[:1800]
                stderr = f"{result.check_id}:{result.failure_code} {detail}"
            rows.append(
                CodingVerificationResult(
                    argv=("harness-x-browser-verification", result.check_id),
                    returncode=returncode,
                    stderr=stderr,
                )
            )
        return tuple(rows)

    def _verification_failure_signature(
        self, verification: tuple[CodingVerificationResult, ...]
    ) -> str | None:
        current = self._browser_run_for_current_verification
        if current is not None and current.verdict == VerificationVerdict.FAIL:
            return f"browser:{current.failure_signature}"
        return super()._verification_failure_signature(verification)

    def _completion_evidence_refs(
        self,
        verification: tuple[CodingVerificationResult, ...],
        *,
        context_fingerprint: str | None,
    ) -> tuple[str, ...]:
        refs = list(
            super()._completion_evidence_refs(
                verification,
                context_fingerprint=context_fingerprint,
            )
        )
        current = self._browser_run_for_current_verification
        if current is not None and current.verdict == VerificationVerdict.PASS:
            refs.extend(
                [
                    f"browser-verification-run:{current.run_fingerprint}",
                    f"browser-verification-plan:{current.plan_fingerprint}",
                ]
            )
        return tuple(refs)

    def _write_browser_artifacts(self) -> None:
        self._atomic_json(
            self.output_root / "browser-verification-plan.json",
            self.browser_verification.plan.model_dump(mode="json"),
        )
        self._atomic_json(
            self.output_root / "browser-verification-runs.json",
            [item.model_dump(mode="json") for item in self.browser_verification_runs],
        )
        self._atomic_json(
            self.output_root / "browser-application-state.json",
            self.browser_session.context_projection(),
        )


class BrowserVerifiedIsolatedRepositoryCodingTaskRuntime:
    """M24 isolation around the M26 browser-aware verified runtime."""

    def __init__(
        self,
        source_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        application: ApplicationServerSpec,
        browser_verification_plan: BrowserVerificationPlan,
        browser_provider_factory: BrowserProviderFactory,
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
        self.application = application
        self.browser_verification_plan = browser_verification_plan
        self.browser_provider_factory = browser_provider_factory
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
    ) -> BrowserVerifiedIsolatedCodingTaskReport:
        manager = TaskWorkspaceIsolationManager(
            self.source_root,
            self.output_root / "isolation",
            isolation_root=self.isolation_root,
            retention=self.retention,
            support_paths=self.support_paths,
        )
        prepared = manager.prepare()
        runtime = BrowserVerifiedRepositoryCodingTaskRuntime(
            prepared.workspace_root,
            self.core,
            self.output_root,
            application=self.application,
            browser_verification_plan=self.browser_verification_plan,
            browser_provider_factory=self.browser_provider_factory,
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
                    "M26 isolation finalization also failed: "
                    f"{type(finalize_exc).__name__}: {finalize_exc}"
                )
            raise
        isolation = manager.finalize(succeeded=report.succeeded)
        enhanced = BrowserVerifiedIsolatedCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v6-isolated-browser-verification",
                "isolation": isolation,
            }
        )
        (self.output_root / "coding-task-report.json").write_text(
            enhanced.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return enhanced
