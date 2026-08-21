"""M27 durable long-horizon state integrated with M25/M26 coding runtimes."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Iterable

from harness_x.browser import ApplicationServerSpec, BrowserProviderFactory
from harness_x.memory import WorkingState
from harness_x.reasoning import RawReasoningOutput, ReasoningCore, ReasoningCoreInfo
from harness_x.reasoning.context_builder import ContextBuildResult
from harness_x.repository import RepositorySemanticProvider
from harness_x.telemetry import TraceRecorder
from harness_x.tools import ToolResult
from harness_x.tools.long_horizon import task_state_recall_definition

from .browser_runtime import (
    BrowserVerifiedCodingTaskReport,
    BrowserVerifiedIsolatedCodingTaskReport,
    BrowserVerifiedRepositoryCodingTaskRuntime,
)
from .browser_verification import BrowserVerificationPlan
from .isolation import IsolationResult, IsolationRetention, TaskWorkspaceIsolationManager
from .long_horizon_state import (
    LongHorizonStateStore,
    LongHorizonStateUpdateProposal,
)
from .verification import VerificationPlan
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


def _acceptance_from_verification(
    plan: VerificationPlan | None,
    commands: tuple[tuple[str, ...], ...],
) -> tuple[str, ...]:
    if plan is not None:
        return tuple(
            f"code:{item.requirement.value}:{item.check_id}:{item.name}"
            for item in plan.checks
        )
    return tuple(f"code:required:{' '.join(command)}" for command in commands)


def _acceptance_with_browser(
    plan: VerificationPlan | None,
    commands: tuple[tuple[str, ...], ...],
    browser_plan: BrowserVerificationPlan,
) -> tuple[str, ...]:
    return (
        *_acceptance_from_verification(plan, commands),
        *(
            f"browser:{item.requirement.value}:{item.check_id}:{item.name}"
            for item in browser_plan.checks
        ),
    )


class LongHorizonContextReasoningCore:
    """Inject bounded durable task state and consume typed advisory state proposals."""

    def __init__(
        self,
        core: ReasoningCore,
        store: LongHorizonStateStore,
        *,
        max_total_chars: int = 68_000,
    ) -> None:
        self.core = core
        self.store = store
        self.max_total_chars = max_total_chars

    @property
    def info(self) -> ReasoningCoreInfo:
        return self.core.info

    def generate(self, context: ContextBuildResult) -> RawReasoningOutput:
        enriched = self._enrich(context)
        output = self.core.generate(enriched)
        return self._consume_state_proposals(output)

    def close(self) -> None:
        close = getattr(self.core, "close", None)
        if callable(close):
            close()

    def _enrich(self, context: ContextBuildResult) -> ContextBuildResult:
        payload = copy.deepcopy(context.payload)
        sections = payload.setdefault("sections", {})
        projection = copy.deepcopy(self.store.context_projection())
        sections["long_horizon_task_state"] = {
            "authority": "software_owned_durable_task_state",
            "rule": (
                "The user task and acceptance requirements are immutable authority. Strategy, "
                "obligations, and decisions are advisory durable state. Selected evidence is "
                "bounded; use task_state_recall for older evidence rather than assuming it was "
                "forgotten. A state-update proposal does not count as coding progress, so when "
                "work remains combine it with a concrete tool action."
            ),
            "state_update_protocol": {
                "proposal_summary": "short reason for updating durable task state",
                "payload": {
                    "kind": "long_horizon_state_update",
                    "strategy": {
                        "current_focus": "optional bounded focus",
                        "next_actions": ["optional next action"],
                        "risks": ["optional risk"],
                    },
                    "add_obligations": [
                        {
                            "text": "durable obligation",
                            "rationale": "why it must survive context churn",
                            "priority": 0.8,
                        }
                    ],
                    "resolve_obligation_ids": ["obl_000001"],
                    "decisions": [
                        {
                            "statement": "durable design decision",
                            "rationale": "why",
                            "evidence_refs": ["evidence id if known"],
                            "supersedes": [],
                        }
                    ],
                    "checkpoint_reason": "optional safe-point reason",
                },
                "note": (
                    "Omit fields that do not change. Never put task text or acceptance rules in "
                    "this proposal; software will reject unknown/invalid IDs."
                ),
            },
            "data": projection,
        }
        serialized = _canonical(payload)
        if len(serialized) > self.max_total_chars and isinstance(projection, dict):
            evidence = projection.get("selected_evidence")
            if isinstance(evidence, list):
                projection["selected_evidence"] = evidence[:8]
            obligations = projection.get("open_obligations")
            if isinstance(obligations, list):
                projection["open_obligations"] = obligations[:12]
            decisions = projection.get("active_decisions")
            if isinstance(decisions, list):
                projection["active_decisions"] = decisions[:8]
            serialized = _canonical(payload)
        if len(serialized) > self.max_total_chars and isinstance(projection, dict):
            obligations = projection.get("open_obligations")
            if isinstance(obligations, list):
                projection["open_obligations"] = [
                    {
                        "obligation_id": row.get("obligation_id"),
                        "text": str(row.get("text", ""))[:500],
                        "priority": row.get("priority"),
                        "status": row.get("status"),
                    }
                    for row in obligations
                    if isinstance(row, dict)
                ]
            decisions = projection.get("active_decisions")
            if isinstance(decisions, list):
                projection["active_decisions"] = [
                    {
                        "decision_id": row.get("decision_id"),
                        "statement": str(row.get("statement", ""))[:500],
                        "status": row.get("status"),
                    }
                    for row in decisions
                    if isinstance(row, dict)
                ]
            projection["selected_evidence"] = []
            serialized = _canonical(payload)
        if len(serialized) > self.max_total_chars:
            sections["long_horizon_task_state"] = {
                "authority": "software_owned_durable_task_state",
                "rule": "Use task_state_recall for older evidence; immutable task authority is unchanged.",
                "data": {
                    "configured": projection.get("configured", False),
                    "session_id": projection.get("session_id"),
                    "revision": projection.get("revision"),
                    "fingerprint": projection.get("fingerprint"),
                    "counts": projection.get("counts", {}),
                    "recall_rule": projection.get("recall_rule"),
                },
            }
            serialized = _canonical(payload)
        fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return context.model_copy(
            update={
                "payload": payload,
                "serialized": serialized,
                "fingerprint": fingerprint,
                "char_count": len(serialized),
            }
        )

    def _consume_state_proposals(self, output: RawReasoningOutput) -> RawReasoningOutput:
        remaining = []
        observations = list(output.observations)
        consumed = False
        for proposal in output.proposals:
            payload = proposal.payload
            if payload.get("kind") != "long_horizon_state_update":
                remaining.append(proposal)
                continue
            if consumed:
                observations.append(
                    "long_horizon_state_update_rejected: only one state update is accepted per reasoning turn"
                )
                continue
            consumed = True
            try:
                update = LongHorizonStateUpdateProposal.model_validate(payload)
                state = self.store.apply_model_update(update)
            except Exception as exc:
                observations.append(
                    "long_horizon_state_update_rejected: "
                    f"{type(exc).__name__}: {str(exc)[:1200]}"
                )
                continue
            observations.append(
                "long_horizon_state_update_applied: "
                f"revision={state.revision} fingerprint={state.fingerprint}"
            )
        return output.model_copy(
            update={
                "proposals": tuple(remaining),
                "observations": tuple(observations),
            }
        )


class LongHorizonCodingTaskReport(VerifiedCodingTaskReport):
    schema_version: str = "coding-task-report-v7-long-horizon-state"
    long_horizon_session_id: str
    long_horizon_state_path: str
    long_horizon_evidence_path: str
    long_horizon_state_fingerprint: str
    long_horizon_state_revision: int
    long_horizon_checkpoint_count: int
    long_horizon_resumed: bool


class LongHorizonBrowserCodingTaskReport(BrowserVerifiedCodingTaskReport):
    schema_version: str = "coding-task-report-v8-browser-long-horizon-state"
    long_horizon_session_id: str
    long_horizon_state_path: str
    long_horizon_evidence_path: str
    long_horizon_state_fingerprint: str
    long_horizon_state_revision: int
    long_horizon_checkpoint_count: int
    long_horizon_resumed: bool


class LongHorizonIsolatedCodingTaskReport(LongHorizonCodingTaskReport):
    schema_version: str = "coding-task-report-v9-isolated-long-horizon-state"
    isolation: IsolationResult


class LongHorizonBrowserIsolatedCodingTaskReport(LongHorizonBrowserCodingTaskReport):
    schema_version: str = "coding-task-report-v10-isolated-browser-long-horizon-state"
    isolation: IsolationResult


class _LongHorizonRuntimeHooks:
    long_horizon_store: LongHorizonStateStore

    def _remember_tool_result(
        self,
        working: WorkingState,
        recorder: TraceRecorder,
        result: ToolResult,
    ) -> None:
        super()._remember_tool_result(working, recorder, result)
        if self.long_horizon_store.initialized and result.tool_name != "task_state_recall":
            self.long_horizon_store.record_tool_result(result)

    def _remember_verification_snapshot(
        self,
        working: WorkingState,
        recorder: TraceRecorder,
        *,
        kind: str,
        verification,
        priority: float,
    ) -> None:
        super()._remember_verification_snapshot(
            working,
            recorder,
            kind=kind,
            verification=verification,
            priority=priority,
        )
        if not self.long_horizon_store.initialized:
            return
        passed = all(item.returncode == 0 for item in verification)
        metadata: dict[str, object] = {
            "commands": [item.model_dump(mode="json") for item in verification],
        }
        runs = getattr(self, "verification_runs", None)
        if runs:
            latest = runs[-1]
            metadata["typed_code_verification"] = {
                "run_fingerprint": latest.run_fingerprint,
                "plan_fingerprint": latest.plan_fingerprint,
                "workspace_fingerprint": latest.workspace_fingerprint_after,
                "required_failures": list(latest.required_failures),
                "advisory_failures": list(latest.advisory_failures),
                "failure_signature": latest.failure_signature,
            }
        browser_runs = getattr(self, "browser_verification_runs", None)
        if browser_runs:
            latest_browser = browser_runs[-1]
            metadata["typed_browser_verification"] = {
                "run_fingerprint": latest_browser.run_fingerprint,
                "plan_fingerprint": latest_browser.plan_fingerprint,
                "required_failures": list(latest_browser.required_failures),
                "advisory_failures": list(latest_browser.advisory_failures),
                "failure_signature": latest_browser.failure_signature,
                "code_verification_fresh_after": latest_browser.code_verification_fresh_after,
            }
        self.long_horizon_store.record_evidence(
            kind=f"verification:{kind}",
            summary=(
                f"software-owned verification {kind}: "
                + ("passed" if passed else "failed")
            ),
            source_ref=f"verification:{recorder.trace_id}:{kind}",
            importance=0.98,
            success=passed,
            metadata=metadata,
        )
        # Verification boundaries are exact safe points for process restart/resume.
        self.long_horizon_store.checkpoint(f"verification_boundary:{kind}")

    def _remember_control_intervention(
        self,
        working: WorkingState,
        recorder: TraceRecorder,
        *,
        intervention,
        phase,
        reasoning_step: int,
    ) -> None:
        super()._remember_control_intervention(
            working,
            recorder,
            intervention=intervention,
            phase=phase,
            reasoning_step=reasoning_step,
        )
        if self.long_horizon_store.initialized:
            self.long_horizon_store.record_control_intervention(
                phase=phase.value,
                reason=intervention.reason,
                intervention_kind=intervention.kind.value,
                reasoning_step=reasoning_step,
            )

    def _register_long_horizon_recall(self) -> None:
        self.registry.register(task_state_recall_definition(self.long_horizon_store))
        self.allowed_tools = tuple(spec.name for spec in self.registry.specs())

    def _long_horizon_report_fields(self) -> dict[str, object]:
        state = self.long_horizon_store.state
        if state is None:
            raise RuntimeError("long-horizon state was not initialized")
        return {
            "long_horizon_session_id": state.session_id,
            "long_horizon_state_path": str(self.long_horizon_store.state_path),
            "long_horizon_evidence_path": str(self.long_horizon_store.evidence_path),
            "long_horizon_state_fingerprint": state.fingerprint,
            "long_horizon_state_revision": state.revision,
            "long_horizon_checkpoint_count": state.checkpoint_count,
            "long_horizon_resumed": state.resumed,
        }


class LongHorizonVerifiedRepositoryCodingTaskRuntime(
    _LongHorizonRuntimeHooks,
    VerifiedRepositoryCodingTaskRuntime,
):
    """M25/M23 coding runtime with M27 durable task state and recall."""

    def __init__(
        self,
        workspace_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        verification_plan: VerificationPlan | None = None,
        resume_state_path: str | Path | None = None,
        require_resume_workspace_match: bool = True,
        **runtime_kwargs,
    ) -> None:
        output = Path(output_root).resolve()
        store = LongHorizonStateStore(
            output / "long-horizon",
            workspace_root,
            resume_state_path=resume_state_path,
            require_resume_workspace_match=require_resume_workspace_match,
        )
        context_core = LongHorizonContextReasoningCore(core, store)
        self.long_horizon_store = store
        self.long_horizon_context_core = context_core
        super().__init__(
            workspace_root,
            context_core,
            output,
            verification_plan=verification_plan,
            system_version="0.1.0a0+coding27-long-horizon",
            **runtime_kwargs,
        )
        self._register_long_horizon_recall()

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> LongHorizonCodingTaskReport:
        commands = tuple(tuple(item for item in command) for command in verification_commands)
        self.long_horizon_store.initialize(
            task=task,
            acceptance_requirements=_acceptance_from_verification(
                self.verification_platform.plan,
                commands,
            ),
        )
        self.long_horizon_store.checkpoint(
            "run_resume" if self.long_horizon_store.state and self.long_horizon_store.state.resumed else "run_start"
        )
        try:
            report = super().run(task, verification_commands=commands)
        except BaseException as exc:
            try:
                self.long_horizon_store.checkpoint("runtime_exception")
            except Exception as checkpoint_exc:
                exc.add_note(
                    "M27 checkpoint after runtime exception also failed: "
                    f"{type(checkpoint_exc).__name__}: {checkpoint_exc}"
                )
            raise
        self.long_horizon_store.checkpoint(
            "run_complete" if report.succeeded else "run_failed"
        )
        enhanced = LongHorizonCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v7-long-horizon-state",
                **self._long_horizon_report_fields(),
            }
        )
        self._write_report(enhanced)
        return enhanced


class LongHorizonBrowserRepositoryCodingTaskRuntime(
    _LongHorizonRuntimeHooks,
    BrowserVerifiedRepositoryCodingTaskRuntime,
):
    """M26 browser-aware runtime plus the M27 durable task-state layer."""

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
        resume_state_path: str | Path | None = None,
        require_resume_workspace_match: bool = True,
        semantic_provider: RepositorySemanticProvider | None = None,
        **runtime_kwargs,
    ) -> None:
        output = Path(output_root).resolve()
        store = LongHorizonStateStore(
            output / "long-horizon",
            workspace_root,
            resume_state_path=resume_state_path,
            require_resume_workspace_match=require_resume_workspace_match,
        )
        context_core = LongHorizonContextReasoningCore(core, store)
        self.long_horizon_store = store
        self.long_horizon_context_core = context_core
        super().__init__(
            workspace_root,
            context_core,
            output,
            application=application,
            browser_verification_plan=browser_verification_plan,
            browser_provider_factory=browser_provider_factory,
            verification_plan=verification_plan,
            semantic_provider=semantic_provider,
            system_version="0.1.0a0+coding27-browser-long-horizon",
            **runtime_kwargs,
        )
        self._register_long_horizon_recall()

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> LongHorizonBrowserCodingTaskReport:
        commands = tuple(tuple(item for item in command) for command in verification_commands)
        self.long_horizon_store.initialize(
            task=task,
            acceptance_requirements=_acceptance_with_browser(
                self.verification_platform.plan,
                commands,
                self.browser_verification.plan,
            ),
        )
        self.long_horizon_store.checkpoint(
            "run_resume" if self.long_horizon_store.state and self.long_horizon_store.state.resumed else "run_start"
        )
        try:
            report = super().run(task, verification_commands=commands)
        except BaseException as exc:
            try:
                self.long_horizon_store.checkpoint("runtime_exception")
            except Exception as checkpoint_exc:
                exc.add_note(
                    "M27 checkpoint after browser runtime exception also failed: "
                    f"{type(checkpoint_exc).__name__}: {checkpoint_exc}"
                )
            raise
        self.long_horizon_store.checkpoint(
            "run_complete" if report.succeeded else "run_failed"
        )
        enhanced = LongHorizonBrowserCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v8-browser-long-horizon-state",
                **self._long_horizon_report_fields(),
            }
        )
        self._write_report(enhanced)
        return enhanced


class LongHorizonIsolatedRepositoryCodingTaskRuntime:
    """M24 isolation around the M27 non-browser runtime.

    Resume intentionally targets an already retained workspace through the in-place M27
    runtime. Creating a fresh isolated clone cannot reproduce unexported checkpoint edits.
    """

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
    ) -> LongHorizonIsolatedCodingTaskReport:
        manager = TaskWorkspaceIsolationManager(
            self.source_root,
            self.output_root / "isolation",
            isolation_root=self.isolation_root,
            retention=self.retention,
            support_paths=self.support_paths,
        )
        prepared = manager.prepare()
        runtime = LongHorizonVerifiedRepositoryCodingTaskRuntime(
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
                    "M27 isolation finalization also failed: "
                    f"{type(finalize_exc).__name__}: {finalize_exc}"
                )
            raise
        isolation = manager.finalize(succeeded=report.succeeded)
        enhanced = LongHorizonIsolatedCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v9-isolated-long-horizon-state",
                "isolation": isolation,
            }
        )
        (self.output_root / "coding-task-report.json").write_text(
            enhanced.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return enhanced


class LongHorizonBrowserIsolatedRepositoryCodingTaskRuntime:
    """M24 isolation around M26 browser feedback plus M27 durable task state."""

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
    ) -> LongHorizonBrowserIsolatedCodingTaskReport:
        manager = TaskWorkspaceIsolationManager(
            self.source_root,
            self.output_root / "isolation",
            isolation_root=self.isolation_root,
            retention=self.retention,
            support_paths=self.support_paths,
        )
        prepared = manager.prepare()
        runtime = LongHorizonBrowserRepositoryCodingTaskRuntime(
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
                    "M27 browser isolation finalization also failed: "
                    f"{type(finalize_exc).__name__}: {finalize_exc}"
                )
            raise
        isolation = manager.finalize(succeeded=report.succeeded)
        enhanced = LongHorizonBrowserIsolatedCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v10-isolated-browser-long-horizon-state",
                "isolation": isolation,
            }
        )
        (self.output_root / "coding-task-report.json").write_text(
            enhanced.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return enhanced
