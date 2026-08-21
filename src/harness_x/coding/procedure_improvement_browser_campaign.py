"""Browser/application specialization of the M31 procedure-improvement campaign."""

from __future__ import annotations

from pathlib import Path

from harness_x.browser import ApplicationServerSpec, BrowserProviderFactory
from harness_x.reasoning import ReasoningCore

from .browser_verification import BrowserVerificationPlan
from .isolation import IsolationRetention, TaskWorkspaceIsolationManager
from .procedure_improvement_campaign import (
    ProcedureImprovementCampaignBudget,
    ProcedureImprovementCampaignRunner,
)
from .procedure_revision_runtime import ProcedureRevisionBrowserRepositoryCodingTaskRuntime
from .verification import VerificationPlan


class ProcedureImprovementBrowserCampaignRunner(ProcedureImprovementCampaignRunner):
    """M31 campaign whose isolated experiment tasks also require browser verification."""

    def __init__(
        self,
        source_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        application: ApplicationServerSpec,
        browser_verification_plan: BrowserVerificationPlan,
        browser_provider_factory: BrowserProviderFactory,
        verification_plan: VerificationPlan,
        project_memory_root: str | Path | None = None,
        project_key: str | None = None,
        budget: ProcedureImprovementCampaignBudget | None = None,
        isolation_root: str | Path | None = None,
        retention: IsolationRetention = IsolationRetention.ALWAYS,
        support_paths=(),
        **runtime_kwargs,
    ) -> None:
        self.application = application
        self.browser_verification_plan = browser_verification_plan
        self.browser_provider_factory = browser_provider_factory
        super().__init__(
            source_root,
            core,
            output_root,
            verification_plan=verification_plan,
            project_memory_root=project_memory_root,
            project_key=project_key,
            budget=budget,
            isolation_root=isolation_root,
            retention=retention,
            support_paths=support_paths,
            **runtime_kwargs,
        )

    def _run_isolated_m30(
        self,
        task: str,
        step_root: Path,
        *,
        allow_revision_trials: bool,
    ):
        manager = TaskWorkspaceIsolationManager(
            self.source_root,
            step_root / "isolation",
            isolation_root=self.isolation_root,
            retention=self.retention,
            support_paths=self.support_paths,
        )
        prepared = manager.prepare()
        runtime = ProcedureRevisionBrowserRepositoryCodingTaskRuntime(
            prepared.workspace_root,
            self.core,
            step_root,
            application=self.application,
            browser_verification_plan=self.browser_verification_plan,
            browser_provider_factory=self.browser_provider_factory,
            verification_plan=self.verification_plan,
            project_memory_root=self.project_memory_root,
            project_key=self.project_key,
            allow_revision_trials=allow_revision_trials,
            **self.runtime_kwargs,
        )
        try:
            report = runtime.run(task)
        except BaseException as exc:
            try:
                manager.finalize(succeeded=False)
            except Exception as finalize_exc:
                exc.add_note(
                    "M31 browser campaign isolation finalization also failed: "
                    f"{type(finalize_exc).__name__}: {finalize_exc}"
                )
            raise
        manager.finalize(succeeded=report.succeeded)
        return report
