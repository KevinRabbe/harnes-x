"""M29 procedure reliability layered on M28 project memory.

M28 answers whether a project procedure has repeated verified support. M29 keeps a
separate verified reuse-outcome history and filters automatic reuse when that history
shows a previously supported procedure has degraded. Historical M28 support remains
intact and can later provide fresh evidence for revalidation.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Iterable

from harness_x.browser import ApplicationServerSpec, BrowserProviderFactory
from harness_x.reasoning import ReasoningCore
from harness_x.repository import RepositorySemanticProvider

from .browser_verification import BrowserVerificationPlan
from .isolation import IsolationResult, IsolationRetention, TaskWorkspaceIsolationManager
from .long_horizon_runtime import (
    LongHorizonBrowserRepositoryCodingTaskRuntime,
    LongHorizonVerifiedRepositoryCodingTaskRuntime,
)
from .procedure_reliability import (
    ProcedureReliabilityPolicy,
    ProcedureReliabilityStatus,
    ProcedureReliabilityStore,
)
from .project_memory import (
    ProjectMemoryEntry,
    ProjectMemoryEntryKind,
    ProjectMemoryEntryState,
    ProjectMemoryRecallRow,
    ProjectMemoryStore,
    ProjectMemoryTaskEpisode,
)
from .project_memory_runtime import (
    ProjectMemoryBrowserCodingTaskReport,
    ProjectMemoryBrowserIsolatedCodingTaskReport,
    ProjectMemoryCodingTaskReport,
    ProjectMemoryContextReasoningCore,
    ProjectMemoryIsolatedCodingTaskReport,
    _ProjectMemoryRuntimeHooks,
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


class ReliabilityAwareProjectMemoryStore:
    """Read facade that applies M29 eligibility without rewriting M28 state."""

    def __init__(
        self,
        memory_store: ProjectMemoryStore,
        reliability_store: ProcedureReliabilityStore,
    ) -> None:
        self.memory_store = memory_store
        self.reliability_store = reliability_store

    def __getattr__(self, name: str):
        return getattr(self.memory_store, name)

    def _reusable(self, entry: ProjectMemoryEntry) -> bool:
        if entry.kind != ProjectMemoryEntryKind.PROCEDURE:
            return True
        return self.reliability_store.is_eligible(entry.entry_id)

    def active_entries(self) -> tuple[ProjectMemoryEntry, ...]:
        return tuple(
            item for item in self.memory_store.active_entries() if self._reusable(item)
        )

    def recall(
        self,
        *,
        query: str,
        kinds: tuple[str, ...] = (),
        include_candidates: bool = False,
        limit: int = 12,
    ) -> tuple[ProjectMemoryRecallRow, ...]:
        # Ask M28 for a wider bounded candidate set first so suspended procedures do not
        # consume the requested result limit before the reliability filter is applied.
        rows = self.memory_store.recall(
            query=query,
            kinds=kinds,
            include_candidates=include_candidates,
            limit=50,
        )
        result: list[ProjectMemoryRecallRow] = []
        for row in rows:
            if (
                row.kind == ProjectMemoryEntryKind.PROCEDURE
                and not self.reliability_store.is_eligible(row.entry_id)
            ):
                continue
            result.append(row)
            if len(result) >= limit:
                break
        return tuple(result)

    def context_projection(self, query: str, *, limit: int = 12) -> dict[str, object]:
        projection = copy.deepcopy(self.memory_store.context_projection(query, limit=limit))
        projection["selected_active_memory"] = [
            item.model_dump(mode="json")
            for item in self.recall(query=query, limit=limit)
        ]
        projection["reliability_filter"] = (
            "M29 may suppress an M28-active procedure when verified reuse outcomes mark it "
            "suspended. Historical M28 support is retained."
        )
        return projection


class ProcedureReliabilityContextReasoningCore(ProjectMemoryContextReasoningCore):
    """M28 context/proposal protocol plus software-owned M29 reliability gating."""

    def __init__(
        self,
        core: ReasoningCore,
        memory_store: ProjectMemoryStore,
        reliability_store: ProcedureReliabilityStore,
        *,
        max_total_chars: int = 76_000,
    ) -> None:
        self.raw_project_memory_store = memory_store
        self.reliability_store = reliability_store
        facade = ReliabilityAwareProjectMemoryStore(memory_store, reliability_store)
        # Reserve context room for the small reliability sidecar projection.
        super().__init__(core, facade, max_total_chars=max_total_chars - 4_000)
        self.final_max_total_chars = max_total_chars

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
        memory = self.raw_project_memory_store
        episode = memory.record_episode(
            task=task,
            succeeded=succeeded,
            source_ref=source_ref,
            long_horizon_session_id=long_horizon_session_id,
            long_horizon_state_fingerprint=long_horizon_state_fingerprint,
            workspace_fingerprint=workspace_fingerprint,
            changed_files=changed_files,
            verification_refs=verification_refs,
        )

        # Reuse was validated as eligible when the model declared it during the task. Record
        # both M28 historical usage and M29 verified reliability evidence before new support can
        # alter lifecycle state at closeout.
        for entry_id in sorted(self._used_procedure_ids):
            procedure = self._procedure(entry_id)
            memory.record_procedure_usage(
                entry_id,
                success=succeeded,
                failure_mode=failure_mode,
            )
            self.reliability_store.record_usage(
                procedure=procedure,
                episode=episode,
                success=succeeded,
                failure_mode=failure_mode,
            )

        admitted_ids: tuple[str, ...] = ()
        if succeeded and self._pending_candidates:
            admitted = memory.support_candidates(
                episode,
                tuple(self._pending_candidates.values()),
            )
            admitted_ids = tuple(item.entry_id for item in admitted)
            for item in admitted:
                if (
                    item.kind == ProjectMemoryEntryKind.PROCEDURE
                    and item.state == ProjectMemoryEntryState.ACTIVE
                    and not item.conflicts_with
                ):
                    self.reliability_store.observe_verified_support(
                        procedure=item,
                        episode=episode,
                    )

        self._pending_candidates.clear()
        self._used_procedure_ids.clear()
        return episode, admitted_ids

    def _procedure(self, entry_id: str) -> ProjectMemoryEntry:
        for item in self.raw_project_memory_store.state.entries:
            if item.entry_id == entry_id:
                if item.kind != ProjectMemoryEntryKind.PROCEDURE:
                    raise ValueError(f"project memory entry {entry_id} is not a procedure")
                return item
        raise KeyError(f"unknown project procedure {entry_id}")

    def _enrich(self, context):
        enriched = super()._enrich(context)
        payload = copy.deepcopy(enriched.payload)
        sections = payload.setdefault("sections", {})
        projection = copy.deepcopy(self.reliability_store.projection())
        sections["procedure_reliability"] = {
            "authority": "software_owned_verified_reuse_reliability_gate",
            "rule": (
                "A project procedure can remain historically ACTIVE in M28 while M29 suppresses "
                "automatic reuse after degraded verified outcomes. Suspended procedures are not "
                "valid used_procedure_ids. Fresh verified support can revalidate them."
            ),
            "data": projection,
        }
        serialized = _canonical(payload)
        if len(serialized) > self.final_max_total_chars:
            projection["suspended"] = projection.get("suspended", [])[:6]
            serialized = _canonical(payload)
        if len(serialized) > self.final_max_total_chars:
            sections["procedure_reliability"] = {
                "authority": "software_owned_verified_reuse_reliability_gate",
                "rule": "M29 filters unreliable procedures from M28 automatic reuse.",
                "data": {
                    "revision": projection.get("revision"),
                    "fingerprint": projection.get("fingerprint"),
                    "usage_total": projection.get("usage_total"),
                    "eligible_record_count": projection.get("eligible_record_count"),
                    "suspended_count": projection.get("suspended_count"),
                    "policy": projection.get("policy"),
                },
            }
            serialized = _canonical(payload)
        if len(serialized) > self.final_max_total_chars:
            sections.pop("procedure_reliability", None)
            serialized = _canonical(payload)
        return enriched.model_copy(
            update={
                "payload": payload,
                "serialized": serialized,
                "fingerprint": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                "char_count": len(serialized),
            }
        )


class ProcedureReliabilityCodingTaskReport(ProjectMemoryCodingTaskReport):
    schema_version: str = "coding-task-report-v15-procedure-reliability"
    procedure_reliability_state_path: str
    procedure_reliability_usage_path: str
    procedure_reliability_state_fingerprint: str
    procedure_reliability_state_revision: int
    procedure_reliability_usage_total: int
    procedure_reliability_eligible_records: int
    procedure_reliability_suspended_count: int
    procedure_reliability_suspended_ids: tuple[str, ...] = ()


class ProcedureReliabilityBrowserCodingTaskReport(ProjectMemoryBrowserCodingTaskReport):
    schema_version: str = "coding-task-report-v16-browser-procedure-reliability"
    procedure_reliability_state_path: str
    procedure_reliability_usage_path: str
    procedure_reliability_state_fingerprint: str
    procedure_reliability_state_revision: int
    procedure_reliability_usage_total: int
    procedure_reliability_eligible_records: int
    procedure_reliability_suspended_count: int
    procedure_reliability_suspended_ids: tuple[str, ...] = ()


class ProcedureReliabilityIsolatedCodingTaskReport(ProcedureReliabilityCodingTaskReport):
    schema_version: str = "coding-task-report-v17-isolated-procedure-reliability"
    isolation: IsolationResult


class ProcedureReliabilityBrowserIsolatedCodingTaskReport(
    ProcedureReliabilityBrowserCodingTaskReport
):
    schema_version: str = "coding-task-report-v18-isolated-browser-procedure-reliability"
    isolation: IsolationResult


class _ProcedureReliabilityRuntimeHooks(_ProjectMemoryRuntimeHooks):
    reliability_store: ProcedureReliabilityStore

    def _reliability_report_fields(self) -> dict[str, object]:
        state = self.reliability_store.state
        suspended = tuple(
            item.procedure_id
            for item in state.records
            if item.status == ProcedureReliabilityStatus.SUSPENDED
        )
        return {
            "procedure_reliability_state_path": str(self.reliability_store.state_path),
            "procedure_reliability_usage_path": str(self.reliability_store.usage_path),
            "procedure_reliability_state_fingerprint": state.fingerprint,
            "procedure_reliability_state_revision": state.revision,
            "procedure_reliability_usage_total": state.usage_total,
            "procedure_reliability_eligible_records": sum(
                1
                for item in state.records
                if item.status == ProcedureReliabilityStatus.ELIGIBLE
            ),
            "procedure_reliability_suspended_count": len(suspended),
            "procedure_reliability_suspended_ids": suspended,
        }


def _stores(
    workspace: Path,
    project_memory_root: str | Path | None,
    project_key: str | None,
    reliability_policy: ProcedureReliabilityPolicy | None,
):
    memory_root = (
        Path(project_memory_root).resolve()
        if project_memory_root is not None
        else workspace / ".harness-x" / "project-memory"
    )
    memory = ProjectMemoryStore(memory_root, project_key=project_key or str(workspace))
    reliability = ProcedureReliabilityStore(
        memory_root,
        project_id=memory.project_id,
        policy=reliability_policy,
    )
    facade = ReliabilityAwareProjectMemoryStore(memory, reliability)
    return memory, reliability, facade


class ProcedureReliabilityVerifiedRepositoryCodingTaskRuntime(
    _ProcedureReliabilityRuntimeHooks,
    LongHorizonVerifiedRepositoryCodingTaskRuntime,
):
    """Default M29 in-place coding runtime."""

    def __init__(
        self,
        workspace_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        project_memory_root: str | Path | None = None,
        project_key: str | None = None,
        reliability_policy: ProcedureReliabilityPolicy | None = None,
        **runtime_kwargs,
    ) -> None:
        workspace = Path(workspace_root).resolve()
        memory, reliability, facade = _stores(
            workspace,
            project_memory_root,
            project_key,
            reliability_policy,
        )
        context_core = ProcedureReliabilityContextReasoningCore(core, memory, reliability)
        self.raw_project_memory_store = memory
        self.project_memory_store = facade
        self.project_memory_context_core = context_core
        self.reliability_store = reliability
        super().__init__(workspace, context_core, output_root, **runtime_kwargs)
        self._register_project_memory_recall()

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> ProcedureReliabilityCodingTaskReport:
        report = LongHorizonVerifiedRepositoryCodingTaskRuntime.run(
            self,
            task,
            verification_commands=verification_commands,
        )
        episode, admitted_ids = self._finalize_project_memory(task, report)
        enhanced = ProcedureReliabilityCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v15-procedure-reliability",
                **self._project_report_fields(episode, admitted_ids),
                **self._reliability_report_fields(),
            }
        )
        self._write_report(enhanced)
        return enhanced


class ProcedureReliabilityBrowserRepositoryCodingTaskRuntime(
    _ProcedureReliabilityRuntimeHooks,
    LongHorizonBrowserRepositoryCodingTaskRuntime,
):
    """M26 browser + M27 task state + M28 memory + M29 reliability."""

    def __init__(
        self,
        workspace_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        project_memory_root: str | Path | None = None,
        project_key: str | None = None,
        reliability_policy: ProcedureReliabilityPolicy | None = None,
        application: ApplicationServerSpec,
        browser_verification_plan: BrowserVerificationPlan,
        browser_provider_factory: BrowserProviderFactory,
        verification_plan: VerificationPlan | None = None,
        semantic_provider: RepositorySemanticProvider | None = None,
        **runtime_kwargs,
    ) -> None:
        workspace = Path(workspace_root).resolve()
        memory, reliability, facade = _stores(
            workspace,
            project_memory_root,
            project_key,
            reliability_policy,
        )
        context_core = ProcedureReliabilityContextReasoningCore(core, memory, reliability)
        self.raw_project_memory_store = memory
        self.project_memory_store = facade
        self.project_memory_context_core = context_core
        self.reliability_store = reliability
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
    ) -> ProcedureReliabilityBrowserCodingTaskReport:
        report = LongHorizonBrowserRepositoryCodingTaskRuntime.run(
            self,
            task,
            verification_commands=verification_commands,
        )
        episode, admitted_ids = self._finalize_project_memory(task, report)
        enhanced = ProcedureReliabilityBrowserCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v16-browser-procedure-reliability",
                **self._project_report_fields(episode, admitted_ids),
                **self._reliability_report_fields(),
            }
        )
        self._write_report(enhanced)
        return enhanced


class ProcedureReliabilityIsolatedRepositoryCodingTaskRuntime:
    """M29 isolated task runtime with source-scoped persistent reliability state."""

    def __init__(
        self,
        source_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        verification_plan: VerificationPlan | None = None,
        project_memory_root: str | Path | None = None,
        project_key: str | None = None,
        reliability_policy: ProcedureReliabilityPolicy | None = None,
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
        self.reliability_policy = reliability_policy
        self.isolation_root = isolation_root
        self.retention = IsolationRetention(retention)
        self.support_paths = tuple(support_paths)
        self.runtime_kwargs = dict(runtime_kwargs)

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> ProcedureReliabilityIsolatedCodingTaskReport:
        manager = TaskWorkspaceIsolationManager(
            self.source_root,
            self.output_root / "isolation",
            isolation_root=self.isolation_root,
            retention=self.retention,
            support_paths=self.support_paths,
        )
        prepared = manager.prepare()
        runtime = ProcedureReliabilityVerifiedRepositoryCodingTaskRuntime(
            prepared.workspace_root,
            self.core,
            self.output_root,
            verification_plan=self.verification_plan,
            project_memory_root=self.project_memory_root,
            project_key=self.project_key,
            reliability_policy=self.reliability_policy,
            **self.runtime_kwargs,
        )
        try:
            report = runtime.run(task, verification_commands=verification_commands)
        except BaseException as exc:
            try:
                manager.finalize(succeeded=False)
            except Exception as finalize_exc:
                exc.add_note(
                    "M29 isolation finalization also failed: "
                    f"{type(finalize_exc).__name__}: {finalize_exc}"
                )
            raise
        isolation = manager.finalize(succeeded=report.succeeded)
        enhanced = ProcedureReliabilityIsolatedCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v17-isolated-procedure-reliability",
                "isolation": isolation,
            }
        )
        (self.output_root / "coding-task-report.json").write_text(
            enhanced.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return enhanced


class ProcedureReliabilityBrowserIsolatedRepositoryCodingTaskRuntime:
    """Full M24+M26+M27+M28+M29 isolated browser composition."""

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
        reliability_policy: ProcedureReliabilityPolicy | None = None,
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
        self.reliability_policy = reliability_policy
        self.isolation_root = isolation_root
        self.retention = IsolationRetention(retention)
        self.support_paths = tuple(support_paths)
        self.runtime_kwargs = dict(runtime_kwargs)

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> ProcedureReliabilityBrowserIsolatedCodingTaskReport:
        manager = TaskWorkspaceIsolationManager(
            self.source_root,
            self.output_root / "isolation",
            isolation_root=self.isolation_root,
            retention=self.retention,
            support_paths=self.support_paths,
        )
        prepared = manager.prepare()
        runtime = ProcedureReliabilityBrowserRepositoryCodingTaskRuntime(
            prepared.workspace_root,
            self.core,
            self.output_root,
            application=self.application,
            browser_verification_plan=self.browser_verification_plan,
            browser_provider_factory=self.browser_provider_factory,
            verification_plan=self.verification_plan,
            project_memory_root=self.project_memory_root,
            project_key=self.project_key,
            reliability_policy=self.reliability_policy,
            **self.runtime_kwargs,
        )
        try:
            report = runtime.run(task, verification_commands=verification_commands)
        except BaseException as exc:
            try:
                manager.finalize(succeeded=False)
            except Exception as finalize_exc:
                exc.add_note(
                    "M29 browser isolation finalization also failed: "
                    f"{type(finalize_exc).__name__}: {finalize_exc}"
                )
            raise
        isolation = manager.finalize(succeeded=report.succeeded)
        enhanced = ProcedureReliabilityBrowserIsolatedCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v18-isolated-browser-procedure-reliability",
                "isolation": isolation,
            }
        )
        (self.output_root / "coding-task-report.json").write_text(
            enhanced.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return enhanced
