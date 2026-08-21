"""M30 failure-driven procedure revision layered on M29 reliability.

Revision candidates are advisory until independently validated. Candidate trials are enabled
only by the isolated runtime wrappers. Successful trials also support a hidden technical M28
replacement entry; only after both validation and normal M28 activation can software promote
that replacement into future project-memory retrieval.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Iterable

from harness_x.browser import ApplicationServerSpec, BrowserProviderFactory
from harness_x.reasoning import RawReasoningOutput, ReasoningCore
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
from .procedure_reliability_runtime import (
    ProcedureReliabilityBrowserCodingTaskReport,
    ProcedureReliabilityCodingTaskReport,
    ProcedureReliabilityContextReasoningCore,
    ReliabilityAwareProjectMemoryStore,
    _ProcedureReliabilityRuntimeHooks,
)
from .procedure_revision import (
    ProcedureRevisionCandidate,
    ProcedureRevisionPolicy,
    ProcedureRevisionState,
    ProcedureRevisionStore,
    ProcedureRevisionUpdateProposal,
)
from .project_memory import (
    ProjectMemoryEntry,
    ProjectMemoryEntryKind,
    ProjectMemoryEntryState,
    ProjectMemoryRecallRow,
    ProjectMemoryStore,
    ProjectMemoryTaskEpisode,
    ProposedProjectProcedure,
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


class RevisionAwareProjectMemoryStore(ReliabilityAwareProjectMemoryStore):
    """M28/M29 read facade plus M30 lineage visibility rules."""

    def __init__(
        self,
        memory_store: ProjectMemoryStore,
        reliability_store: ProcedureReliabilityStore,
        revision_store: ProcedureRevisionStore,
    ) -> None:
        super().__init__(memory_store, reliability_store)
        self.revision_store = revision_store

    def _visible(self, entry: ProjectMemoryEntry) -> bool:
        if entry.kind == ProjectMemoryEntryKind.PROCEDURE:
            if not self.reliability_store.is_eligible(entry.entry_id):
                return False
            if entry.entry_id in self.revision_store.promoted_parent_ids():
                return False
            if entry.key.startswith("hx-revision/"):
                return entry.entry_id in self.revision_store.promoted_replacement_ids()
        return True

    def active_entries(self) -> tuple[ProjectMemoryEntry, ...]:
        return tuple(item for item in self.memory_store.active_entries() if self._visible(item))

    def recall(
        self,
        *,
        query: str,
        kinds: tuple[str, ...] = (),
        include_candidates: bool = False,
        limit: int = 12,
    ) -> tuple[ProjectMemoryRecallRow, ...]:
        rows = self.memory_store.recall(
            query=query,
            kinds=kinds,
            include_candidates=include_candidates,
            limit=50,
        )
        result: list[ProjectMemoryRecallRow] = []
        entries = {item.entry_id: item for item in self.memory_store.state.entries}
        for row in rows:
            entry = entries.get(row.entry_id)
            if entry is None or not self._visible(entry):
                continue
            result.append(row)
            if len(result) >= limit:
                break
        return tuple(result)

    def context_projection(self, query: str, *, limit: int = 12) -> dict[str, object]:
        projection = copy.deepcopy(self.memory_store.context_projection(query, limit=limit))
        projection["selected_active_memory"] = [
            item.model_dump(mode="json") for item in self.recall(query=query, limit=limit)
        ]
        projection["revision_filter"] = (
            "M30 hides unpromoted technical revision entries and hides a parent after a "
            "software-promoted replacement exists. M29 reliability eligibility still applies."
        )
        return projection


class ProcedureRevisionContextReasoningCore(ProcedureReliabilityContextReasoningCore):
    """M30 model context/protocol around the M29 reliability-aware core."""

    def __init__(
        self,
        core: ReasoningCore,
        memory_store: ProjectMemoryStore,
        reliability_store: ProcedureReliabilityStore,
        revision_store: ProcedureRevisionStore,
        *,
        allow_revision_trials: bool,
        max_total_chars: int = 76_000,
    ) -> None:
        self.revision_store = revision_store
        self.allow_revision_trials = allow_revision_trials
        self._pending_revision_proposals: dict[str, object] = {}
        self._used_revision_candidate_ids: set[str] = set()
        self._last_admitted_revision_candidate_ids: tuple[str, ...] = ()
        self._last_validated_revision_candidate_ids: tuple[str, ...] = ()
        self._last_promoted_revision_candidate_ids: tuple[str, ...] = ()
        # M29 receives a lower bound, leaving deterministic headroom for the M30 projection.
        super().__init__(
            core,
            memory_store,
            reliability_store,
            max_total_chars=max_total_chars - 6_000,
        )
        self.store = RevisionAwareProjectMemoryStore(
            memory_store,
            reliability_store,
            revision_store,
        )
        self.revision_final_max_total_chars = max_total_chars
        self._reconcile_ready_candidates()

    @property
    def last_admitted_revision_candidate_ids(self) -> tuple[str, ...]:
        return self._last_admitted_revision_candidate_ids

    @property
    def last_validated_revision_candidate_ids(self) -> tuple[str, ...]:
        return self._last_validated_revision_candidate_ids

    @property
    def last_promoted_revision_candidate_ids(self) -> tuple[str, ...]:
        return self._last_promoted_revision_candidate_ids

    def _consume_project_proposals(self, output: RawReasoningOutput) -> RawReasoningOutput:
        remaining = []
        observations = list(output.observations)
        consumed = False
        for proposal in output.proposals:
            payload = proposal.payload
            if payload.get("kind") != "procedure_revision_update":
                remaining.append(proposal)
                continue
            if consumed:
                observations.append(
                    "procedure_revision_update_rejected: only one update is accepted per reasoning turn"
                )
                continue
            consumed = True
            try:
                update = ProcedureRevisionUpdateProposal.model_validate(payload)
                for item in update.candidates:
                    parent = self._procedure(item.parent_procedure_id)
                    if parent.state != ProjectMemoryEntryState.ACTIVE or parent.conflicts_with:
                        raise ValueError("revision parent is not M28 active/conflict-free")
                    material = _canonical(item.model_dump(mode="json"))
                    key = hashlib.sha256(material.encode("utf-8")).hexdigest()
                    self._pending_revision_proposals[key] = item
                if update.used_revision_candidate_ids and not self.allow_revision_trials:
                    raise ValueError("procedure revision trials require an isolated task workspace")
                for candidate_id in update.used_revision_candidate_ids:
                    candidate = self.revision_store.candidate(candidate_id)
                    if candidate.state != ProcedureRevisionState.CANDIDATE:
                        raise ValueError("revision trial candidate is not open for validation")
                    reliability = self.reliability_store.record_for(candidate.parent_procedure_id)
                    if reliability is None or reliability.status != ProcedureReliabilityStatus.SUSPENDED:
                        raise ValueError("revision trial parent is no longer suspended")
                    self._used_revision_candidate_ids.add(candidate_id)
            except Exception as exc:
                observations.append(
                    "procedure_revision_update_rejected: "
                    f"{type(exc).__name__}: {str(exc)[:1200]}"
                )
                continue
            observations.append(
                "procedure_revision_update_staged: "
                f"pending_candidates={len(self._pending_revision_proposals)} "
                f"trial_candidates={len(self._used_revision_candidate_ids)}"
            )
        base_output = output.model_copy(
            update={"proposals": tuple(remaining), "observations": tuple(observations)}
        )
        return super()._consume_project_proposals(base_output)

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
        episode, admitted_memory_ids = super().finalize_task(
            task=task,
            succeeded=succeeded,
            source_ref=source_ref,
            long_horizon_session_id=long_horizon_session_id,
            long_horizon_state_fingerprint=long_horizon_state_fingerprint,
            workspace_fingerprint=workspace_fingerprint,
            changed_files=changed_files,
            verification_refs=verification_refs,
            failure_mode=failure_mode,
        )
        memory = self.raw_project_memory_store
        validated_ids: list[str] = []
        promoted_ids: list[str] = []

        # Trial IDs existed and were suspension-valid during the reasoning turn. The final
        # software verdict now owns their validation outcome.
        for candidate_id in sorted(self._used_revision_candidate_ids):
            candidate_before = self.revision_store.candidate(candidate_id)
            updated = self.revision_store.record_validation(
                candidate_id=candidate_id,
                episode=episode,
                success=succeeded,
                failure_mode=failure_mode,
            )
            validated_ids.append(candidate_id)
            parent_reliability = self.reliability_store.record_for(
                candidate_before.parent_procedure_id
            )
            if parent_reliability is None:
                continue
            if parent_reliability.status != ProcedureReliabilityStatus.SUSPENDED:
                self.revision_store.supersede_open_for_parent(
                    candidate_before.parent_procedure_id,
                    reason="parent_revalidated_before_revision_promotion",
                )
                continue
            if succeeded:
                replacement = self._support_replacement(updated, episode)
                current = self.revision_store.candidate(candidate_id)
                if (
                    current.state == ProcedureRevisionState.READY
                    and replacement.state == ProjectMemoryEntryState.ACTIVE
                    and not replacement.conflicts_with
                ):
                    promoted = self.revision_store.promote(
                        candidate_id=candidate_id,
                        replacement=replacement,
                        parent_reliability=parent_reliability,
                    )
                    promoted_ids.append(promoted.candidate_id)

        admitted_revision_ids: list[str] = []
        # A candidate may be proposed by the task whose verified failure actually causes the
        # parent suspension. Candidate admission is safe because it grants trial status only.
        for proposal in self._pending_revision_proposals.values():
            try:
                parent = self._procedure(proposal.parent_procedure_id)
                reliability = self.reliability_store.record_for(parent.entry_id)
                if reliability is None or reliability.status != ProcedureReliabilityStatus.SUSPENDED:
                    continue
                if parent.entry_id in self.revision_store.promoted_parent_ids():
                    continue
                candidate = self.revision_store.propose(
                    proposal=proposal,
                    parent=parent,
                    origin_episode=episode,
                    reliability=reliability,
                )
                admitted_revision_ids.append(candidate.candidate_id)
            except (KeyError, ValueError, RuntimeError):
                continue

        # If ordinary M29 revalidation recovered a parent, any unpromoted repair experiments for
        # that parent become obsolete rather than later replacing a recovered procedure.
        for candidate in tuple(self.revision_store.open_candidates()):
            reliability = self.reliability_store.record_for(candidate.parent_procedure_id)
            if reliability is None or reliability.status == ProcedureReliabilityStatus.ELIGIBLE:
                self.revision_store.supersede_open_for_parent(
                    candidate.parent_procedure_id,
                    reason="parent_is_not_suspended",
                )

        self._pending_revision_proposals.clear()
        self._used_revision_candidate_ids.clear()
        self._last_admitted_revision_candidate_ids = tuple(sorted(set(admitted_revision_ids)))
        self._last_validated_revision_candidate_ids = tuple(sorted(set(validated_ids)))
        self._last_promoted_revision_candidate_ids = tuple(sorted(set(promoted_ids)))
        return episode, admitted_memory_ids

    def _support_replacement(
        self,
        candidate: ProcedureRevisionCandidate,
        episode: ProjectMemoryTaskEpisode,
    ) -> ProjectMemoryEntry:
        proposal = ProposedProjectProcedure(
            key=candidate.replacement_memory_key,
            statement=candidate.statement,
            steps=candidate.steps,
            task_categories=candidate.task_categories,
        )
        return self.raw_project_memory_store.support_candidates(episode, (proposal,))[0]

    def _reconcile_ready_candidates(self) -> None:
        entries = tuple(self.raw_project_memory_store.state.entries)
        for candidate in tuple(self.revision_store.open_candidates()):
            reliability = self.reliability_store.record_for(candidate.parent_procedure_id)
            if reliability is None or reliability.status == ProcedureReliabilityStatus.ELIGIBLE:
                self.revision_store.supersede_open_for_parent(
                    candidate.parent_procedure_id,
                    reason="parent_is_not_suspended",
                )
                continue
            if candidate.state != ProcedureRevisionState.READY:
                continue
            replacement = next(
                (
                    item
                    for item in entries
                    if item.key == candidate.replacement_memory_key
                    and item.kind == ProjectMemoryEntryKind.PROCEDURE
                    and item.state == ProjectMemoryEntryState.ACTIVE
                    and not item.conflicts_with
                ),
                None,
            )
            if replacement is not None:
                self.revision_store.promote(
                    candidate_id=candidate.candidate_id,
                    replacement=replacement,
                    parent_reliability=reliability,
                )

    def _enrich(self, context):
        enriched = super()._enrich(context)
        payload = copy.deepcopy(enriched.payload)
        sections = payload.setdefault("sections", {})
        projection = copy.deepcopy(self.revision_store.projection())
        suspended_parents = []
        promoted_parents = self.revision_store.promoted_parent_ids()
        for record in self.reliability_store.state.records:
            if record.status != ProcedureReliabilityStatus.SUSPENDED:
                continue
            if record.procedure_id in promoted_parents:
                continue
            try:
                parent = self._procedure(record.procedure_id)
            except KeyError:
                continue
            suspended_parents.append(
                {
                    "procedure_id": parent.entry_id,
                    "key": parent.key,
                    "statement": parent.statement,
                    "steps": parent.steps,
                    "support_count": parent.support_count,
                    "reuse_failure_count": record.failure_count,
                    "suspension_reason": record.suspension_reason,
                }
            )
            if len(suspended_parents) >= 8:
                break
        sections["procedure_revision"] = {
            "authority": "software_owned_isolated_revision_validation_and_lineage",
            "trial_allowed": self.allow_revision_trials,
            "rule": (
                "Revision proposals are experiments, not reusable procedures. Trials count only "
                "in an isolated task workspace. Two verified successful validation episodes are "
                "required, and the replacement must independently become M28 active before "
                "software can promote it."
            ),
            "proposal_protocol": {
                "kind": "procedure_revision_update",
                "candidates": [
                    {
                        "parent_procedure_id": "M29-suspended M28 procedure ID",
                        "statement": "revised applicability/behavior",
                        "steps": ["bounded revised step"],
                        "task_categories": ["optional category"],
                        "rationale": "how verified failures motivate this revision",
                    }
                ],
                "used_revision_candidate_ids": [
                    "existing prev_* ID only when this isolated task materially trials it"
                ],
            },
            "suspended_parents": suspended_parents,
            "data": projection,
        }
        serialized = _canonical(payload)
        if len(serialized) > self.revision_final_max_total_chars:
            projection["open_candidates"] = projection.get("open_candidates", [])[:4]
            projection["promoted_lineage"] = projection.get("promoted_lineage", [])[:6]
            sections["procedure_revision"]["suspended_parents"] = suspended_parents[:4]
            serialized = _canonical(payload)
        if len(serialized) > self.revision_final_max_total_chars:
            sections["procedure_revision"] = {
                "authority": "software_owned_isolated_revision_validation_and_lineage",
                "trial_allowed": self.allow_revision_trials,
                "rule": "Use isolated verified tasks to validate open revision candidates.",
                "data": {
                    "revision": projection.get("revision"),
                    "fingerprint": projection.get("fingerprint"),
                    "validation_total": projection.get("validation_total"),
                    "candidate_counts": projection.get("candidate_counts", {}),
                    "policy": projection.get("policy", {}),
                },
            }
            serialized = _canonical(payload)
        if len(serialized) > self.revision_final_max_total_chars:
            sections.pop("procedure_revision", None)
            serialized = _canonical(payload)
        return enriched.model_copy(
            update={
                "payload": payload,
                "serialized": serialized,
                "fingerprint": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                "char_count": len(serialized),
            }
        )


class ProcedureRevisionCodingTaskReport(ProcedureReliabilityCodingTaskReport):
    schema_version: str = "coding-task-report-v19-procedure-revision"
    procedure_revision_state_path: str
    procedure_revision_validation_path: str
    procedure_revision_state_fingerprint: str
    procedure_revision_state_revision: int
    procedure_revision_validation_total: int
    procedure_revision_open_candidates: int
    procedure_revision_ready_candidates: int
    procedure_revision_promoted_candidates: int
    procedure_revision_rejected_candidates: int
    procedure_revision_admitted_candidate_ids: tuple[str, ...] = ()
    procedure_revision_validated_candidate_ids: tuple[str, ...] = ()
    procedure_revision_promoted_candidate_ids: tuple[str, ...] = ()


class ProcedureRevisionBrowserCodingTaskReport(ProcedureReliabilityBrowserCodingTaskReport):
    schema_version: str = "coding-task-report-v20-browser-procedure-revision"
    procedure_revision_state_path: str
    procedure_revision_validation_path: str
    procedure_revision_state_fingerprint: str
    procedure_revision_state_revision: int
    procedure_revision_validation_total: int
    procedure_revision_open_candidates: int
    procedure_revision_ready_candidates: int
    procedure_revision_promoted_candidates: int
    procedure_revision_rejected_candidates: int
    procedure_revision_admitted_candidate_ids: tuple[str, ...] = ()
    procedure_revision_validated_candidate_ids: tuple[str, ...] = ()
    procedure_revision_promoted_candidate_ids: tuple[str, ...] = ()


class ProcedureRevisionIsolatedCodingTaskReport(ProcedureRevisionCodingTaskReport):
    schema_version: str = "coding-task-report-v21-isolated-procedure-revision"
    isolation: IsolationResult


class ProcedureRevisionBrowserIsolatedCodingTaskReport(ProcedureRevisionBrowserCodingTaskReport):
    schema_version: str = "coding-task-report-v22-isolated-browser-procedure-revision"
    isolation: IsolationResult


class _ProcedureRevisionRuntimeHooks(_ProcedureReliabilityRuntimeHooks):
    revision_store: ProcedureRevisionStore
    project_memory_context_core: ProcedureRevisionContextReasoningCore

    def _revision_report_fields(self) -> dict[str, object]:
        state = self.revision_store.state
        counts = {
            revision_state: sum(1 for item in state.candidates if item.state == revision_state)
            for revision_state in ProcedureRevisionState
        }
        return {
            "procedure_revision_state_path": str(self.revision_store.state_path),
            "procedure_revision_validation_path": str(self.revision_store.validation_path),
            "procedure_revision_state_fingerprint": state.fingerprint,
            "procedure_revision_state_revision": state.revision,
            "procedure_revision_validation_total": state.validation_total,
            "procedure_revision_open_candidates": counts[ProcedureRevisionState.CANDIDATE],
            "procedure_revision_ready_candidates": counts[ProcedureRevisionState.READY],
            "procedure_revision_promoted_candidates": counts[ProcedureRevisionState.PROMOTED],
            "procedure_revision_rejected_candidates": counts[ProcedureRevisionState.REJECTED],
            "procedure_revision_admitted_candidate_ids": (
                self.project_memory_context_core.last_admitted_revision_candidate_ids
            ),
            "procedure_revision_validated_candidate_ids": (
                self.project_memory_context_core.last_validated_revision_candidate_ids
            ),
            "procedure_revision_promoted_candidate_ids": (
                self.project_memory_context_core.last_promoted_revision_candidate_ids
            ),
        }


def _stores(
    workspace: Path,
    project_memory_root: str | Path | None,
    project_key: str | None,
    reliability_policy: ProcedureReliabilityPolicy | None,
    revision_policy: ProcedureRevisionPolicy | None,
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
    revisions = ProcedureRevisionStore(
        memory_root,
        project_id=memory.project_id,
        policy=revision_policy,
    )
    facade = RevisionAwareProjectMemoryStore(memory, reliability, revisions)
    return memory, reliability, revisions, facade


class ProcedureRevisionVerifiedRepositoryCodingTaskRuntime(
    _ProcedureRevisionRuntimeHooks,
    LongHorizonVerifiedRepositoryCodingTaskRuntime,
):
    """M30 in-place runtime; may propose revisions but cannot validate revision trials."""

    def __init__(
        self,
        workspace_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        project_memory_root: str | Path | None = None,
        project_key: str | None = None,
        reliability_policy: ProcedureReliabilityPolicy | None = None,
        revision_policy: ProcedureRevisionPolicy | None = None,
        allow_revision_trials: bool = False,
        **runtime_kwargs,
    ) -> None:
        workspace = Path(workspace_root).resolve()
        memory, reliability, revisions, facade = _stores(
            workspace,
            project_memory_root,
            project_key,
            reliability_policy,
            revision_policy,
        )
        context_core = ProcedureRevisionContextReasoningCore(
            core,
            memory,
            reliability,
            revisions,
            allow_revision_trials=allow_revision_trials,
        )
        self.raw_project_memory_store = memory
        self.project_memory_store = facade
        self.project_memory_context_core = context_core
        self.reliability_store = reliability
        self.revision_store = revisions
        super().__init__(workspace, context_core, output_root, **runtime_kwargs)
        self._register_project_memory_recall()

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> ProcedureRevisionCodingTaskReport:
        report = LongHorizonVerifiedRepositoryCodingTaskRuntime.run(
            self,
            task,
            verification_commands=verification_commands,
        )
        episode, admitted_ids = self._finalize_project_memory(task, report)
        enhanced = ProcedureRevisionCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v19-procedure-revision",
                **self._project_report_fields(episode, admitted_ids),
                **self._reliability_report_fields(),
                **self._revision_report_fields(),
            }
        )
        self._write_report(enhanced)
        return enhanced


class ProcedureRevisionBrowserRepositoryCodingTaskRuntime(
    _ProcedureRevisionRuntimeHooks,
    LongHorizonBrowserRepositoryCodingTaskRuntime,
):
    """M30 browser in-place runtime; revision trials remain disabled by default."""

    def __init__(
        self,
        workspace_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        project_memory_root: str | Path | None = None,
        project_key: str | None = None,
        reliability_policy: ProcedureReliabilityPolicy | None = None,
        revision_policy: ProcedureRevisionPolicy | None = None,
        allow_revision_trials: bool = False,
        application: ApplicationServerSpec,
        browser_verification_plan: BrowserVerificationPlan,
        browser_provider_factory: BrowserProviderFactory,
        verification_plan: VerificationPlan | None = None,
        semantic_provider: RepositorySemanticProvider | None = None,
        **runtime_kwargs,
    ) -> None:
        workspace = Path(workspace_root).resolve()
        memory, reliability, revisions, facade = _stores(
            workspace,
            project_memory_root,
            project_key,
            reliability_policy,
            revision_policy,
        )
        context_core = ProcedureRevisionContextReasoningCore(
            core,
            memory,
            reliability,
            revisions,
            allow_revision_trials=allow_revision_trials,
        )
        self.raw_project_memory_store = memory
        self.project_memory_store = facade
        self.project_memory_context_core = context_core
        self.reliability_store = reliability
        self.revision_store = revisions
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
    ) -> ProcedureRevisionBrowserCodingTaskReport:
        report = LongHorizonBrowserRepositoryCodingTaskRuntime.run(
            self,
            task,
            verification_commands=verification_commands,
        )
        episode, admitted_ids = self._finalize_project_memory(task, report)
        enhanced = ProcedureRevisionBrowserCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v20-browser-procedure-revision",
                **self._project_report_fields(episode, admitted_ids),
                **self._reliability_report_fields(),
                **self._revision_report_fields(),
            }
        )
        self._write_report(enhanced)
        return enhanced


class ProcedureRevisionIsolatedRepositoryCodingTaskRuntime:
    """M30 isolated runtime; this is the normal revision-validation authority."""

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
        revision_policy: ProcedureRevisionPolicy | None = None,
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
        self.revision_policy = revision_policy
        self.isolation_root = isolation_root
        self.retention = IsolationRetention(retention)
        self.support_paths = tuple(support_paths)
        self.runtime_kwargs = dict(runtime_kwargs)

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> ProcedureRevisionIsolatedCodingTaskReport:
        manager = TaskWorkspaceIsolationManager(
            self.source_root,
            self.output_root / "isolation",
            isolation_root=self.isolation_root,
            retention=self.retention,
            support_paths=self.support_paths,
        )
        prepared = manager.prepare()
        runtime = ProcedureRevisionVerifiedRepositoryCodingTaskRuntime(
            prepared.workspace_root,
            self.core,
            self.output_root,
            verification_plan=self.verification_plan,
            project_memory_root=self.project_memory_root,
            project_key=self.project_key,
            reliability_policy=self.reliability_policy,
            revision_policy=self.revision_policy,
            allow_revision_trials=True,
            **self.runtime_kwargs,
        )
        try:
            report = runtime.run(task, verification_commands=verification_commands)
        except BaseException as exc:
            try:
                manager.finalize(succeeded=False)
            except Exception as finalize_exc:
                exc.add_note(
                    "M30 isolation finalization also failed: "
                    f"{type(finalize_exc).__name__}: {finalize_exc}"
                )
            raise
        isolation = manager.finalize(succeeded=report.succeeded)
        enhanced = ProcedureRevisionIsolatedCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v21-isolated-procedure-revision",
                "isolation": isolation,
            }
        )
        (self.output_root / "coding-task-report.json").write_text(
            enhanced.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return enhanced


class ProcedureRevisionBrowserIsolatedRepositoryCodingTaskRuntime:
    """Full M24+M26+M27+M28+M29+M30 isolated browser composition."""

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
        revision_policy: ProcedureRevisionPolicy | None = None,
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
        self.revision_policy = revision_policy
        self.isolation_root = isolation_root
        self.retention = IsolationRetention(retention)
        self.support_paths = tuple(support_paths)
        self.runtime_kwargs = dict(runtime_kwargs)

    def run(
        self,
        task: str,
        *,
        verification_commands: Iterable[Iterable[str]] = (),
    ) -> ProcedureRevisionBrowserIsolatedCodingTaskReport:
        manager = TaskWorkspaceIsolationManager(
            self.source_root,
            self.output_root / "isolation",
            isolation_root=self.isolation_root,
            retention=self.retention,
            support_paths=self.support_paths,
        )
        prepared = manager.prepare()
        runtime = ProcedureRevisionBrowserRepositoryCodingTaskRuntime(
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
            revision_policy=self.revision_policy,
            allow_revision_trials=True,
            **self.runtime_kwargs,
        )
        try:
            report = runtime.run(task, verification_commands=verification_commands)
        except BaseException as exc:
            try:
                manager.finalize(succeeded=False)
            except Exception as finalize_exc:
                exc.add_note(
                    "M30 browser isolation finalization also failed: "
                    f"{type(finalize_exc).__name__}: {finalize_exc}"
                )
            raise
        isolation = manager.finalize(succeeded=report.succeeded)
        enhanced = ProcedureRevisionBrowserIsolatedCodingTaskReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "schema_version": "coding-task-report-v22-isolated-browser-procedure-revision",
                "isolation": isolation,
            }
        )
        (self.output_root / "coding-task-report.json").write_text(
            enhanced.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return enhanced
