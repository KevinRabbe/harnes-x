"""M28 project-scoped memory layered on M27 durable task state."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Iterable

from harness_x.browser import ApplicationServerSpec, BrowserProviderFactory
from harness_x.reasoning import RawReasoningOutput, ReasoningCore, ReasoningCoreInfo
from harness_x.reasoning.context_builder import ContextBuildResult
from harness_x.repository import RepositorySemanticProvider
from harness_x.tools.project_memory import project_memory_recall_definition

from .browser_verification import BrowserVerificationPlan
from .isolation import IsolationResult, IsolationRetention, TaskWorkspaceIsolationManager
from .long_horizon_runtime import (
    LongHorizonBrowserCodingTaskReport,
    LongHorizonBrowserRepositoryCodingTaskRuntime,
    LongHorizonCodingTaskReport,
    LongHorizonVerifiedRepositoryCodingTaskRuntime,
)
from .project_memory import (
    ProjectMemoryEntryState,
    ProjectMemoryStore,
    ProjectMemoryTaskEpisode,
    ProjectMemoryUpdateProposal,
)
from .verification import VerificationPlan


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


class ProjectMemoryContextReasoningCore:
    """Inject bounded active project memory and stage advisory memory proposals."""

    def __init__(
        self,
        core: ReasoningCore,
        store: ProjectMemoryStore,
        *,
        max_total_chars: int = 76_000,
    ) -> None:
        self.core = core
        self.store = store
        self.max_total_chars = max_total_chars
        self._pending_candidates: dict[str, object] = {}
        self._used_procedure_ids: set[str] = set()

    @property
    def info(self) -> ReasoningCoreInfo:
        return self.core.info

    @property
    def pending_candidate_count(self) -> int:
        return len(self._pending_candidates)

    @property
    def used_procedure_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._used_procedure_ids))

    def generate(self, context: ContextBuildResult) -> RawReasoningOutput:
        enriched = self._enrich(context)
        output = self.core.generate(enriched)
        return self._consume_project_proposals(output)

    def close(self) -> None:
        close = getattr(self.core, "close", None)
        if callable(close):
            close()

    def finalize_task(
        self,
        *,
        task: str,
        succeeded: bool,
        source_ref: str,
        long_horizon_session_id: str | None,
        long_horizon_state_fingerprint: str | None,
        workspace_fingerprint: str | None,
        changed_files: tuple[str, ...],
        verification_refs: tuple[str, ...],
        failure_mode: str | None,
    ) -> tuple[ProjectMemoryTaskEpisode, tuple[str, ...]]:
        episode = self.store.record_episode(
            task=task,
            succeeded=succeeded,
            source_ref=source_ref,
            long_horizon_session_id=long_horizon_session_id,
            long_horizon_state_fingerprint=long_horizon_state_fingerprint,
            workspace_fingerprint=workspace_fingerprint,
            changed_files=changed_files,
            verification_refs=verification_refs,
        )
        # Usage was validated while the procedure was active during the task. Record that
        # historical fact before admitting newly learned candidates, which may conflict with
        # and suspend the procedure at closeout.
        for entry_id in sorted(self._used_procedure_ids):
            self.store.record_procedure_usage(
                entry_id,
                success=succeeded,
                failure_mode=failure_mode,
            )
        admitted_ids: tuple[str, ...] = ()
        if succeeded and self._pending_candidates:
            admitted = self.store.support_candidates(
                episode,
                tuple(self._pending_candidates.values()),
            )
            admitted_ids = tuple(item.entry_id for item in admitted)
        self._pending_candidates.clear()
        self._used_procedure_ids.clear()
        return episode, admitted_ids

    def _enrich(self, context: ContextBuildResult) -> ContextBuildResult:
        payload = copy.deepcopy(context.payload)
        sections = payload.setdefault("sections", {})
        query = str(payload.get("instruction", ""))
        projection = copy.deepcopy(self.store.context_projection(query))
        sections["project_memory"] = {
            "authority": "software_owned_evidence_gated_project_memory",
            "rule": (
                "Only ACTIVE, conflict-free entries are reusable guidance. A model proposal is "
                "staged during this task and is admitted only if software-owned task verification "
                "succeeds. Activation requires two distinct verified successful task episodes with "
                "identical content. Conflicting variants are suspended."
            ),
            "update_protocol": {
                "proposal_payload": {
                    "kind": "project_memory_update",
                    "candidates": [
                        {
                            "kind": "procedure",
                            "key": "stable-project-key",
                            "statement": "when and why this procedure applies",
                            "steps": ["bounded reusable step"],
                            "task_categories": ["optional category"],
                        },
                        {
                            "kind": "fact",
                            "key": "stable-project-key",
                            "statement": "candidate repository convention or architectural fact",
                            "task_categories": ["optional category"],
                        },
                    ],
                    "used_procedure_ids": ["pmem_id only when an ACTIVE procedure was actually used"],
                },
                "note": (
                    "Project-memory proposals do not count as coding progress. Combine them with "
                    "a concrete coding action when work remains. Do not claim a procedure was used "
                    "unless it materially informed the task."
                ),
            },
            "data": projection,
        }
        serialized = _canonical(payload)
        if len(serialized) > self.max_total_chars:
            selected = projection.get("selected_active_memory")
            if isinstance(selected, list):
                projection["selected_active_memory"] = selected[:6]
            serialized = _canonical(payload)
        if len(serialized) > self.max_total_chars:
            selected = projection.get("selected_active_memory")
            if isinstance(selected, list):
                projection["selected_active_memory"] = [
                    {
                        "entry_id": row.get("entry_id"),
                        "kind": row.get("kind"),
                        "key": row.get("key"),
                        "statement": str(row.get("statement", ""))[:500],
                        "steps": [str(item)[:300] for item in row.get("steps", [])[:8]],
                        "support_count": row.get("support_count"),
                    }
                    for row in selected
                    if isinstance(row, dict)
                ]
            serialized = _canonical(payload)
        if len(serialized) > self.max_total_chars:
            sections["project_memory"] = {
                "authority": "software_owned_evidence_gated_project_memory",
                "rule": "Use project_memory_recall for reusable project knowledge.",
                "data": {
                    "project_id": projection.get("project_id"),
                    "revision": projection.get("revision"),
                    "fingerprint": projection.get("fingerprint"),
                    "episode_count": projection.get("episode_count"),
                    "entry_counts": projection.get("entry_counts", {}),
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

    def _consume_project_proposals(self, output: RawReasoningOutput) -> RawReasoningOutput:
        remaining = []
        observations = list(output.observations)
        consumed = False
        for proposal in output.proposals:
            payload = proposal.payload
            if payload.get("kind") != "project_memory_update":
                remaining.append(proposal)
                continue
            if consumed:
                observations.append(
                    "project_memory_update_rejected: only one update is accepted per reasoning turn"
                )
                continue
            consumed = True
            try:
                update = ProjectMemoryUpdateProposal.model_validate(payload)
                active_ids = {item.entry_id for item in self.store.active_entries() if item.kind.value == "procedure"}
                unknown = sorted(set(update.used_procedure_ids) - active_ids)
                if unknown:
                    raise ValueError(
                        "used procedure IDs are not active/conflict-free project procedures: "
                        + ", ".join(unknown)
                    )
                for candidate in update.candidates:
                    material = _canonical(candidate.model_dump(mode="json"))
                    key = hashlib.sha256(material.encode("utf-8")).hexdigest()
                    self._pending_candidates[key] = candidate
                self._used_procedure_ids.update(update.used_procedure_ids)
            except Exception as exc:
                observations.append(
                    "project_memory_update_rejected: "
                    f"{type(exc).__name__}: {str(exc)[:1200]}"
                )
                continue
            observations.append(
                "project_memory_update_staged: "
                f"pending_candidates={len(self._pending_candidates)} "
                f"used_procedures={len(self._used_procedure_ids)}"
            )
        return output.model_copy(
            update={
                "proposals": tuple(remaining),
                "observations": tuple(observations),
            }
        )


class ProjectMemoryCodingTaskReport(LongHorizonCodingTaskReport):
    schema_version: str = "coding-task-report-v11-project-memory"
    project_memory_project_id: str
    project_memory_root: str
    project_memory_state_path: str
    project_memory_state_fingerprint: str
    project_memory_state_revision: int
    project_memory_episode_id: str
    project_memory_active_entries: int
    project_memory_candidate_entries: int
    project_memory_conflicted_entries: int
    project_memory_admitted_entry_ids: tuple[str, ...] = ()


class ProjectMemoryBrowserCodingTaskReport(LongHorizonBrowserCodingTaskReport):
    schema_version: str = "coding-task-report-v12-browser-project-memory"
    project_memory_project_id: str
    project_memory_root: str
    project_memory_state_path: str
    project_memory_state_fingerprint: str
    project_memory_state_revision: int
    project_memory_episode_id: str
    project_memory_active_entries: int
    project_memory_candidate_entries: int
    project_memory_conflicted_entries: int
    project_memory_admitted_entry_ids: tuple[str, ...] = ()


class ProjectMemoryIsolatedCodingTaskReport(ProjectMemoryCodingTaskReport):
    schema_version: str = "coding-task-report-v13-isolated-project-memory"
    isolation: IsolationResult


class ProjectMemoryBrowserIsolatedCodingTaskReport(ProjectMemoryBrowserCodingTaskReport):
    schema_version: str = "coding-task-report-v14-isolated-browser-project-memory"
    isolation: IsolationResult


class _ProjectMemoryRuntimeHooks:
    project_memory_store: ProjectMemoryStore
    project_memory_context_core: ProjectMemoryContextReasoningCore

    def _register_project_memory_recall(self) -> None:
        self.registry.register(project_memory_recall_definition(self.project_memory_store))
        self.allowed_tools = tuple(spec.name for spec in self.registry.specs())

    def _finalize_project_memory(self, task: str, report) -> tuple[ProjectMemoryTaskEpisode, tuple[str, ...]]:
        state = self.long_horizon_store.state
        checkpoint = state.latest_checkpoint if state is not None else None
        progress = report.coding_progress if isinstance(report.coding_progress, dict) else {}
        raw_changed = progress.get("changed_files", ())
        changed_files = tuple(str(item) for item in raw_changed) if isinstance(raw_changed, (list, tuple)) else ()
        verification_refs: list[str] = []
        for run in getattr(report, "verification_runs", ()):
            verification_refs.append(f"code-verification:{run.run_fingerprint}")
        for run in getattr(report, "browser_verification_runs", ()):
            verification_refs.append(f"browser-verification:{run.run_fingerprint}")
        if state is not None:
            verification_refs.append(f"long-horizon-state:{state.fingerprint}")
        episode, admitted_ids = self.project_memory_context_core.finalize_task(
            task=task,
            succeeded=report.succeeded,
            source_ref=f"coding-report:{report.task_id}:{report.trace_id}",
            long_horizon_session_id=(state.session_id if state is not None else None),
            long_horizon_state_fingerprint=(state.fingerprint if state is not None else None),
            workspace_fingerprint=(checkpoint.workspace_fingerprint if checkpoint is not None else None),
            changed_files=changed_files,
            verification_refs=tuple(verification_refs),
            failure_mode=report.failure_reason,
        )
        return episode, admitted_ids

    def _project_report_fields(
        self,
        episode: ProjectMemoryTaskEpisode,
        admitted_ids: tuple[str, ...],
    ) -> dict[str, object]:
        state = self.project_memory_store.state
        return {
            "project_memory_project_id": state.project_id,
            "project_memory_root": str(self.project_memory_store.root),
            "project_memory_state_path": str(self.project_memory_store.state_path),
            "project_memory_state_fingerprint": state.fingerprint,
            "project_memory_state_revision": state.revision,
            "project_memory_episode_id": episode.episode_id,
            "project_memory_active_entries": sum(
                1 for item in state.entries if item.state == ProjectMemoryEntryState.ACTIVE and not item.conflicts_with
            ),
            "project_memory_candidate_entries": sum(
                1 for item in state.entries if item.state == ProjectMemoryEntryState.CANDIDATE
            ),
            "project_memory_conflicted_entries": sum(
                1 for item in state.entries if item.state == ProjectMemoryEntryState.CONFLICTED
            ),
            "project_memory_admitted_entry_ids": admitted_ids,
        }


class ProjectMemoryVerifiedRepositoryCodingTaskRuntime(
    _ProjectMemoryRuntimeHooks,
    LongHorizonVerifiedRepositoryCodingTaskRuntime,
):
    """M27 coding runtime plus cross-task M28 project memory."""

    def __init__(
        self,
        workspace_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        project_memory_root: str | Path | None = None,
        project_key: str | None = None,
        **runtime_kwargs,
    ) -> None:
        workspace = Path(workspace_root).resolve()
        memory_root = (
            Path(project_memory_root).resolve()
            if project_memory_root is not None
            else workspace / ".harness-x" / "project-memory"
        )
        store = ProjectMemoryStore(memory_root, project_key=project_key or str(workspace))
        context_core = ProjectMemoryContextReasoningCore(core, store)
        self.project_memory_store = store
        self.project_memory_context_core = context_core
        super().__init__(workspace, context_core, output_root, **runtime_kwargs)
        self._register_project_memory_recall()

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> ProjectMemoryCodingTaskReport:
        report = super().run(task, verification_commands=verification_commands)
        episode, admitted_ids = self._finalize_project_memory(task, report)
        enhanced = ProjectMemoryCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v11-project-memory",
                **self._project_report_fields(episode, admitted_ids),
            }
        )
        self._write_report(enhanced)
        return enhanced


class ProjectMemoryBrowserRepositoryCodingTaskRuntime(
    _ProjectMemoryRuntimeHooks,
    LongHorizonBrowserRepositoryCodingTaskRuntime,
):
    """M26 browser + M27 task state + M28 project memory."""

    def __init__(
        self,
        workspace_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        project_memory_root: str | Path | None = None,
        project_key: str | None = None,
        application: ApplicationServerSpec,
        browser_verification_plan: BrowserVerificationPlan,
        browser_provider_factory: BrowserProviderFactory,
        verification_plan: VerificationPlan | None = None,
        semantic_provider: RepositorySemanticProvider | None = None,
        **runtime_kwargs,
    ) -> None:
        workspace = Path(workspace_root).resolve()
        memory_root = (
            Path(project_memory_root).resolve()
            if project_memory_root is not None
            else workspace / ".harness-x" / "project-memory"
        )
        store = ProjectMemoryStore(memory_root, project_key=project_key or str(workspace))
        context_core = ProjectMemoryContextReasoningCore(core, store)
        self.project_memory_store = store
        self.project_memory_context_core = context_core
        super().__init__(
            workspace,
            context_core,
            output_root,
            application=application,
            browser_verification_plan=browser_verification_plan,
            browser_provider_factory=browser_provider_factory,
            verification_plan=verification_plan,
            semantic_provider=semantic_provider,
            **runtime_kwargs,
        )
        self._register_project_memory_recall()

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> ProjectMemoryBrowserCodingTaskReport:
        report = super().run(task, verification_commands=verification_commands)
        episode, admitted_ids = self._finalize_project_memory(task, report)
        enhanced = ProjectMemoryBrowserCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v12-browser-project-memory",
                **self._project_report_fields(episode, admitted_ids),
            }
        )
        self._write_report(enhanced)
        return enhanced


class ProjectMemoryIsolatedRepositoryCodingTaskRuntime:
    """M24 isolated task workspace with source-scoped persistent M28 memory."""

    def __init__(
        self,
        source_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        verification_plan: VerificationPlan | None = None,
        project_memory_root: str | Path | None = None,
        project_key: str | None = None,
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
        self.project_memory_root = (
            Path(project_memory_root).resolve()
            if project_memory_root is not None
            else self.source_root / ".harness-x" / "project-memory"
        )
        self.project_key = project_key or str(self.source_root)
        self.isolation_root = isolation_root
        self.retention = IsolationRetention(retention)
        self.support_paths = tuple(support_paths)
        self.runtime_kwargs = dict(runtime_kwargs)

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> ProjectMemoryIsolatedCodingTaskReport:
        manager = TaskWorkspaceIsolationManager(
            self.source_root,
            self.output_root / "isolation",
            isolation_root=self.isolation_root,
            retention=self.retention,
            support_paths=self.support_paths,
        )
        prepared = manager.prepare()
        runtime = ProjectMemoryVerifiedRepositoryCodingTaskRuntime(
            prepared.workspace_root,
            self.core,
            self.output_root,
            verification_plan=self.verification_plan,
            project_memory_root=self.project_memory_root,
            project_key=self.project_key,
            **self.runtime_kwargs,
        )
        try:
            report = runtime.run(task, verification_commands=verification_commands)
        except BaseException as exc:
            try:
                manager.finalize(succeeded=False)
            except Exception as finalize_exc:
                exc.add_note(
                    "M28 isolation finalization also failed: "
                    f"{type(finalize_exc).__name__}: {finalize_exc}"
                )
            raise
        isolation = manager.finalize(succeeded=report.succeeded)
        enhanced = ProjectMemoryIsolatedCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v13-isolated-project-memory",
                "isolation": isolation,
            }
        )
        (self.output_root / "coding-task-report.json").write_text(
            enhanced.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return enhanced


class ProjectMemoryBrowserIsolatedRepositoryCodingTaskRuntime:
    """M24 + M26 + M27 + M28 composition with source-scoped project memory."""

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
        project_memory_root: str | Path | None = None,
        project_key: str | None = None,
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
        self.project_memory_root = (
            Path(project_memory_root).resolve()
            if project_memory_root is not None
            else self.source_root / ".harness-x" / "project-memory"
        )
        self.project_key = project_key or str(self.source_root)
        self.isolation_root = isolation_root
        self.retention = IsolationRetention(retention)
        self.support_paths = tuple(support_paths)
        self.runtime_kwargs = dict(runtime_kwargs)

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> ProjectMemoryBrowserIsolatedCodingTaskReport:
        manager = TaskWorkspaceIsolationManager(
            self.source_root,
            self.output_root / "isolation",
            isolation_root=self.isolation_root,
            retention=self.retention,
            support_paths=self.support_paths,
        )
        prepared = manager.prepare()
        runtime = ProjectMemoryBrowserRepositoryCodingTaskRuntime(
            prepared.workspace_root,
            self.core,
            self.output_root,
            application=self.application,
            browser_verification_plan=self.browser_verification_plan,
            browser_provider_factory=self.browser_provider_factory,
            verification_plan=self.verification_plan,
            project_memory_root=self.project_memory_root,
            project_key=self.project_key,
            **self.runtime_kwargs,
        )
        try:
            report = runtime.run(task, verification_commands=verification_commands)
        except BaseException as exc:
            try:
                manager.finalize(succeeded=False)
            except Exception as finalize_exc:
                exc.add_note(
                    "M28 browser isolation finalization also failed: "
                    f"{type(finalize_exc).__name__}: {finalize_exc}"
                )
            raise
        isolation = manager.finalize(succeeded=report.succeeded)
        enhanced = ProjectMemoryBrowserIsolatedCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v14-isolated-browser-project-memory",
                "isolation": isolation,
            }
        )
        (self.output_root / "coding-task-report.json").write_text(
            enhanced.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return enhanced
