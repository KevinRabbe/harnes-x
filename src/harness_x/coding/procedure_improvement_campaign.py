"""M31 bounded autonomous campaigns around M29 suspension and M30 revision trials.

M31 owns orchestration only. M29 remains the reliability authority and M30 remains the
revision-validation/promotion authority. Campaign state precommits each proposal/trial budget
unit before execution so a crash can reconcile durable M30 state without accidentally running
the same autonomous experiment twice.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness_x.reasoning import ReasoningCore

from .isolation import IsolationRetention, TaskWorkspaceIsolationManager
from .procedure_reliability import ProcedureReliabilityStatus, ProcedureReliabilityStore
from .procedure_revision import (
    ProcedureRevisionCandidate,
    ProcedureRevisionState,
    ProcedureRevisionStore,
)
from .procedure_revision_runtime import ProcedureRevisionVerifiedRepositoryCodingTaskRuntime
from .project_memory import ProjectMemoryEntry, ProjectMemoryEntryKind, ProjectMemoryEntryState, ProjectMemoryStore
from .verification import VerificationPlan


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = str(raw).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)


class ProcedureImprovementCampaignStatus(StrEnum):
    ACTIVE = "active"
    PROMOTED = "promoted"
    EXHAUSTED = "exhausted"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class ProcedureImprovementStepKind(StrEnum):
    PROPOSE = "propose"
    TRIAL = "trial"


class ProcedureImprovementCampaignBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["procedure-improvement-campaign-budget-v1"] = (
        "procedure-improvement-campaign-budget-v1"
    )
    max_candidate_proposals: int = Field(default=3, ge=1, le=12)
    max_trial_tasks: int = Field(default=6, ge=1, le=24)


class PendingProcedureImprovementStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str
    kind: ProcedureImprovementStepKind
    candidate_id: str | None = None
    revision_state_revision_before: int = Field(ge=1)
    validation_total_before: int = Field(ge=0)
    candidate_ids_before: tuple[str, ...] = ()
    candidate_success_count_before: int | None = Field(default=None, ge=0)
    candidate_failure_count_before: int | None = Field(default=None, ge=0)
    started_at: datetime


class ProcedureImprovementCampaign(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    campaign_id: str
    parent_procedure_id: str
    parent_content_fingerprint: str = Field(min_length=64, max_length=64)
    origin_reliability_revision: int = Field(ge=1)
    origin_suspension_reason: str | None = Field(default=None, max_length=800)
    status: ProcedureImprovementCampaignStatus = ProcedureImprovementCampaignStatus.ACTIVE
    budget: ProcedureImprovementCampaignBudget
    proposal_attempts: int = Field(default=0, ge=0)
    trial_attempts: int = Field(default=0, ge=0)
    candidate_ids: tuple[str, ...] = ()
    promoted_candidate_id: str | None = None
    pending_step: PendingProcedureImprovementStep | None = None
    terminal_reason: str | None = Field(default=None, max_length=1200)
    created_at: datetime
    updated_at: datetime
    created_revision: int = Field(ge=1)
    updated_revision: int = Field(ge=1)

    @property
    def terminal(self) -> bool:
        return self.status != ProcedureImprovementCampaignStatus.ACTIVE


class ProcedureImprovementCampaignStoreState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["procedure-improvement-campaign-state-v1"] = (
        "procedure-improvement-campaign-state-v1"
    )
    project_id: str
    revision: int = Field(ge=1)
    campaigns: tuple[ProcedureImprovementCampaign, ...] = ()
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ProcedureImprovementCampaignStoreState":
        ids = [item.campaign_id for item in self.campaigns]
        if len(ids) != len(set(ids)):
            raise ValueError("procedure improvement campaign IDs must be unique")
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", _sha256(_canonical(material)))
        return self


class ProcedureImprovementCampaignStore:
    """Atomic orchestration state; M29/M30 ledgers remain authoritative evidence."""

    def __init__(self, project_memory_root: str | Path, *, project_id: str) -> None:
        self.root = Path(project_memory_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "procedure-improvement-campaigns.json"
        if self.state_path.exists():
            self.state = self._load_state()
            if self.state.project_id != project_id:
                raise ValueError("procedure improvement campaign state belongs to another project")
        else:
            self.state = ProcedureImprovementCampaignStoreState(
                project_id=project_id,
                revision=1,
            )
            self._write_state()

    def open_campaign(
        self,
        *,
        parent: ProjectMemoryEntry,
        reliability_store: ProcedureReliabilityStore,
        revision_store: ProcedureRevisionStore,
        budget: ProcedureImprovementCampaignBudget | None = None,
    ) -> ProcedureImprovementCampaign:
        if parent.kind != ProjectMemoryEntryKind.PROCEDURE:
            raise ValueError("procedure improvement campaign parent must be a procedure")
        if parent.state != ProjectMemoryEntryState.ACTIVE or parent.conflicts_with:
            raise ValueError("procedure improvement campaign parent must remain M28 active")
        reliability = reliability_store.record_for(parent.entry_id)
        if reliability is None or reliability.status != ProcedureReliabilityStatus.SUSPENDED:
            raise ValueError("procedure improvement campaign requires an M29-suspended parent")
        if parent.entry_id in revision_store.promoted_parent_ids():
            raise ValueError("procedure improvement campaign parent already has a promoted revision")

        same_suspension = next(
            (
                item
                for item in self.state.campaigns
                if item.parent_procedure_id == parent.entry_id
                and item.origin_reliability_revision == reliability.updated_revision
            ),
            None,
        )
        if same_suspension is not None:
            return same_suspension
        active = next(
            (
                item
                for item in self.state.campaigns
                if item.parent_procedure_id == parent.entry_id
                and item.status == ProcedureImprovementCampaignStatus.ACTIVE
            ),
            None,
        )
        if active is not None:
            return active

        existing_candidates = tuple(
            item.candidate_id
            for item in revision_store.open_candidates()
            if item.parent_procedure_id == parent.entry_id
        )
        now = datetime.now(timezone.utc)
        next_revision = self.state.revision + 1
        campaign = ProcedureImprovementCampaign(
            campaign_id=f"pcamp_{uuid.uuid4().hex}",
            parent_procedure_id=parent.entry_id,
            parent_content_fingerprint=parent.content_fingerprint,
            origin_reliability_revision=reliability.updated_revision,
            origin_suspension_reason=reliability.suspension_reason,
            budget=budget or ProcedureImprovementCampaignBudget(),
            candidate_ids=existing_candidates,
            created_at=now,
            updated_at=now,
            created_revision=next_revision,
            updated_revision=next_revision,
        )
        self._replace([*self.state.campaigns, campaign], revision=next_revision)
        return campaign

    def campaign(self, campaign_id: str) -> ProcedureImprovementCampaign:
        return self.state.campaigns[self._index(campaign_id)]

    def begin_proposal(
        self,
        campaign_id: str,
        *,
        revision_store: ProcedureRevisionStore,
    ) -> ProcedureImprovementCampaign:
        current = self.campaign(campaign_id)
        self._require_step_start(current)
        if current.proposal_attempts >= current.budget.max_candidate_proposals:
            raise ValueError("procedure improvement proposal budget exhausted")
        parent_ids = tuple(
            item.candidate_id
            for item in revision_store.state.candidates
            if item.parent_procedure_id == current.parent_procedure_id
        )
        pending = PendingProcedureImprovementStep(
            step_id=f"pstep_{uuid.uuid4().hex}",
            kind=ProcedureImprovementStepKind.PROPOSE,
            revision_state_revision_before=revision_store.state.revision,
            validation_total_before=revision_store.state.validation_total,
            candidate_ids_before=parent_ids,
            started_at=datetime.now(timezone.utc),
        )
        return self._update_campaign(
            campaign_id,
            proposal_attempts=current.proposal_attempts + 1,
            pending_step=pending,
        )

    def begin_trial(
        self,
        campaign_id: str,
        *,
        candidate: ProcedureRevisionCandidate,
        revision_store: ProcedureRevisionStore,
    ) -> ProcedureImprovementCampaign:
        current = self.campaign(campaign_id)
        self._require_step_start(current)
        if current.trial_attempts >= current.budget.max_trial_tasks:
            raise ValueError("procedure improvement trial budget exhausted")
        if candidate.candidate_id not in current.candidate_ids:
            raise ValueError("procedure improvement campaign does not own this candidate")
        if candidate.parent_procedure_id != current.parent_procedure_id:
            raise ValueError("procedure improvement candidate belongs to another parent")
        if candidate.state != ProcedureRevisionState.CANDIDATE:
            raise ValueError("procedure improvement trial requires an open M30 candidate")
        pending = PendingProcedureImprovementStep(
            step_id=f"pstep_{uuid.uuid4().hex}",
            kind=ProcedureImprovementStepKind.TRIAL,
            candidate_id=candidate.candidate_id,
            revision_state_revision_before=revision_store.state.revision,
            validation_total_before=revision_store.state.validation_total,
            candidate_success_count_before=candidate.success_count,
            candidate_failure_count_before=candidate.failure_count,
            started_at=datetime.now(timezone.utc),
        )
        return self._update_campaign(
            campaign_id,
            trial_attempts=current.trial_attempts + 1,
            pending_step=pending,
        )

    def complete_pending(
        self,
        campaign_id: str,
        *,
        revision_store: ProcedureRevisionStore,
        admitted_candidate_ids: Iterable[str] = (),
    ) -> ProcedureImprovementCampaign:
        current = self.campaign(campaign_id)
        pending = current.pending_step
        if pending is None:
            return current
        candidate_ids = list(current.candidate_ids)
        if pending.kind == ProcedureImprovementStepKind.PROPOSE:
            before = set(pending.candidate_ids_before)
            discovered = [
                item.candidate_id
                for item in revision_store.state.candidates
                if item.parent_procedure_id == current.parent_procedure_id
                and item.candidate_id not in before
            ]
            candidate_ids = list(
                _unique((*candidate_ids, *admitted_candidate_ids, *discovered))
            )
        return self._update_campaign(
            campaign_id,
            candidate_ids=tuple(candidate_ids),
            pending_step=None,
        )

    def reconcile(
        self,
        campaign_id: str,
        *,
        reliability_store: ProcedureReliabilityStore,
        revision_store: ProcedureRevisionStore,
    ) -> ProcedureImprovementCampaign:
        current = self.campaign(campaign_id)
        if current.terminal:
            return current
        if current.pending_step is not None:
            # A pending step consumed its budget before launch. On restart, reconcile any durable
            # M30 delta and clear the pending marker rather than automatically replaying the task.
            current = self.complete_pending(campaign_id, revision_store=revision_store)
        promoted = next(
            (
                item
                for item in revision_store.promoted_candidates()
                if item.parent_procedure_id == current.parent_procedure_id
            ),
            None,
        )
        if promoted is not None:
            return self._terminate(
                campaign_id,
                ProcedureImprovementCampaignStatus.PROMOTED,
                reason=f"m30_revision_promoted:{promoted.candidate_id}",
                promoted_candidate_id=promoted.candidate_id,
            )
        reliability = reliability_store.record_for(current.parent_procedure_id)
        if reliability is None or reliability.status != ProcedureReliabilityStatus.SUSPENDED:
            return self._terminate(
                campaign_id,
                ProcedureImprovementCampaignStatus.SUPERSEDED,
                reason="parent_no_longer_m29_suspended",
            )
        return self.campaign(campaign_id)

    def exhaust(self, campaign_id: str, *, reason: str) -> ProcedureImprovementCampaign:
        current = self.campaign(campaign_id)
        if current.terminal:
            return current
        if current.pending_step is not None:
            raise ValueError("cannot exhaust a campaign with a pending step")
        return self._terminate(
            campaign_id,
            ProcedureImprovementCampaignStatus.EXHAUSTED,
            reason=reason,
        )

    def cancel(self, campaign_id: str, *, reason: str) -> ProcedureImprovementCampaign:
        current = self.campaign(campaign_id)
        if current.terminal:
            return current
        return self._terminate(
            campaign_id,
            ProcedureImprovementCampaignStatus.CANCELLED,
            reason=reason,
        )

    def projection(self) -> dict[str, object]:
        counts = {
            status.value: sum(1 for item in self.state.campaigns if item.status == status)
            for status in ProcedureImprovementCampaignStatus
        }
        active = [
            {
                "campaign_id": item.campaign_id,
                "parent_procedure_id": item.parent_procedure_id,
                "proposal_attempts": item.proposal_attempts,
                "trial_attempts": item.trial_attempts,
                "candidate_ids": item.candidate_ids,
                "pending_step": (
                    item.pending_step.model_dump(mode="json") if item.pending_step else None
                ),
                "budget": item.budget.model_dump(mode="json"),
            }
            for item in self.state.campaigns
            if item.status == ProcedureImprovementCampaignStatus.ACTIVE
        ]
        return {
            "schema_version": self.state.schema_version,
            "revision": self.state.revision,
            "fingerprint": self.state.fingerprint,
            "campaign_counts": counts,
            "active_campaigns": active[:12],
        }

    def _require_step_start(self, campaign: ProcedureImprovementCampaign) -> None:
        if campaign.status != ProcedureImprovementCampaignStatus.ACTIVE:
            raise ValueError("procedure improvement campaign is not active")
        if campaign.pending_step is not None:
            raise ValueError("procedure improvement campaign already has a pending step")

    def _terminate(
        self,
        campaign_id: str,
        status: ProcedureImprovementCampaignStatus,
        *,
        reason: str,
        promoted_candidate_id: str | None = None,
    ) -> ProcedureImprovementCampaign:
        return self._update_campaign(
            campaign_id,
            status=status,
            promoted_candidate_id=promoted_candidate_id,
            pending_step=None,
            terminal_reason=reason[:1200],
        )

    def _update_campaign(self, campaign_id: str, **changes) -> ProcedureImprovementCampaign:
        index = self._index(campaign_id)
        current = self.state.campaigns[index]
        next_revision = self.state.revision + 1
        changes.update(
            {
                "updated_at": datetime.now(timezone.utc),
                "updated_revision": next_revision,
            }
        )
        campaigns = list(self.state.campaigns)
        campaigns[index] = current.model_copy(update=changes)
        self._replace(campaigns, revision=next_revision)
        return campaigns[index]

    def _index(self, campaign_id: str) -> int:
        for index, item in enumerate(self.state.campaigns):
            if item.campaign_id == campaign_id:
                return index
        raise KeyError(f"unknown procedure improvement campaign {campaign_id}")

    def _replace(self, campaigns: list[ProcedureImprovementCampaign], *, revision: int) -> None:
        self.state = ProcedureImprovementCampaignStoreState.model_validate(
            {
                "project_id": self.state.project_id,
                "revision": revision,
                "campaigns": tuple(campaigns),
            }
        )
        self._write_state()

    def _load_state(self) -> ProcedureImprovementCampaignStoreState:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load procedure improvement campaign state: {exc}") from exc
        stored = str(raw.get("fingerprint", ""))
        state = ProcedureImprovementCampaignStoreState.model_validate(raw)
        if stored != state.fingerprint:
            raise ValueError("procedure improvement campaign state fingerprint mismatch")
        return state

    def _write_state(self) -> None:
        payload = self.state.model_dump_json(indent=2) + "\n"
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, self.state_path)


class ProcedureImprovementCampaignStepReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str
    kind: ProcedureImprovementStepKind
    candidate_id: str | None = None
    task_succeeded: bool
    admitted_candidate_ids: tuple[str, ...] = ()
    validated_candidate_ids: tuple[str, ...] = ()
    promoted_candidate_ids: tuple[str, ...] = ()
    output_root: str


class ProcedureImprovementCampaignReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["procedure-improvement-campaign-report-v1"] = (
        "procedure-improvement-campaign-report-v1"
    )
    campaign: ProcedureImprovementCampaign
    state_path: str
    state_fingerprint: str
    step_reports: tuple[ProcedureImprovementCampaignStepReport, ...] = ()


class ProcedureImprovementCampaignRunner:
    """Bounded autonomous M31 controller using isolated M30 runtimes as experiments."""

    def __init__(
        self,
        source_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        verification_plan: VerificationPlan,
        project_memory_root: str | Path | None = None,
        project_key: str | None = None,
        budget: ProcedureImprovementCampaignBudget | None = None,
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
        self.budget = budget or ProcedureImprovementCampaignBudget()
        self.isolation_root = isolation_root
        self.retention = IsolationRetention(retention)
        self.support_paths = tuple(support_paths)
        self.runtime_kwargs = dict(runtime_kwargs)

    def run(
        self,
        *,
        parent_procedure_id: str,
        validation_task: str,
    ) -> ProcedureImprovementCampaignReport:
        validation_task = validation_task.strip()
        if not validation_task:
            raise ValueError("procedure improvement campaign requires a validation task")
        memory, reliability, revisions, campaigns = self._stores()
        parent = self._procedure(memory, parent_procedure_id)
        campaign = campaigns.open_campaign(
            parent=parent,
            reliability_store=reliability,
            revision_store=revisions,
            budget=self.budget,
        )
        steps: list[ProcedureImprovementCampaignStepReport] = []

        # A terminal campaign for the same exact suspension event is intentionally not reopened.
        campaign = campaigns.reconcile(
            campaign.campaign_id,
            reliability_store=reliability,
            revision_store=revisions,
        )
        while not campaign.terminal:
            memory, reliability, revisions, campaigns = self._stores()
            campaign = campaigns.reconcile(
                campaign.campaign_id,
                reliability_store=reliability,
                revision_store=revisions,
            )
            if campaign.terminal:
                break

            candidate = self._next_trial_candidate(campaign, revisions)
            if candidate is not None and campaign.trial_attempts < campaign.budget.max_trial_tasks:
                campaign = campaigns.begin_trial(
                    campaign.campaign_id,
                    candidate=candidate,
                    revision_store=revisions,
                )
                pending = campaign.pending_step
                assert pending is not None
                step_root = self._step_root(campaign, pending)
                task = self._trial_task(parent, candidate, validation_task)
                report = self._run_isolated_m30(
                    task,
                    step_root,
                    allow_revision_trials=True,
                )
                memory, reliability, revisions, campaigns = self._stores()
                campaign = campaigns.complete_pending(
                    campaign.campaign_id,
                    revision_store=revisions,
                    admitted_candidate_ids=report.procedure_revision_admitted_candidate_ids,
                )
                steps.append(
                    ProcedureImprovementCampaignStepReport(
                        step_id=pending.step_id,
                        kind=pending.kind,
                        candidate_id=candidate.candidate_id,
                        task_succeeded=report.succeeded,
                        admitted_candidate_ids=report.procedure_revision_admitted_candidate_ids,
                        validated_candidate_ids=report.procedure_revision_validated_candidate_ids,
                        promoted_candidate_ids=report.procedure_revision_promoted_candidate_ids,
                        output_root=str(step_root),
                    )
                )
                campaign = campaigns.reconcile(
                    campaign.campaign_id,
                    reliability_store=reliability,
                    revision_store=revisions,
                )
                continue

            if candidate is None and campaign.proposal_attempts < campaign.budget.max_candidate_proposals:
                campaign = campaigns.begin_proposal(
                    campaign.campaign_id,
                    revision_store=revisions,
                )
                pending = campaign.pending_step
                assert pending is not None
                step_root = self._step_root(campaign, pending)
                task = self._proposal_task(parent, reliability, validation_task)
                report = self._run_isolated_m30(
                    task,
                    step_root,
                    allow_revision_trials=False,
                )
                memory, reliability, revisions, campaigns = self._stores()
                campaign = campaigns.complete_pending(
                    campaign.campaign_id,
                    revision_store=revisions,
                    admitted_candidate_ids=report.procedure_revision_admitted_candidate_ids,
                )
                steps.append(
                    ProcedureImprovementCampaignStepReport(
                        step_id=pending.step_id,
                        kind=pending.kind,
                        task_succeeded=report.succeeded,
                        admitted_candidate_ids=report.procedure_revision_admitted_candidate_ids,
                        validated_candidate_ids=report.procedure_revision_validated_candidate_ids,
                        promoted_candidate_ids=report.procedure_revision_promoted_candidate_ids,
                        output_root=str(step_root),
                    )
                )
                campaign = campaigns.reconcile(
                    campaign.campaign_id,
                    reliability_store=reliability,
                    revision_store=revisions,
                )
                continue

            reason = (
                "trial_budget_exhausted_with_open_candidate"
                if candidate is not None
                else "proposal_budget_exhausted_without_open_candidate"
            )
            campaign = campaigns.exhaust(campaign.campaign_id, reason=reason)

        final_state = campaigns.state
        result = ProcedureImprovementCampaignReport(
            campaign=campaign,
            state_path=str(campaigns.state_path),
            state_fingerprint=final_state.fingerprint,
            step_reports=tuple(steps),
        )
        (self.output_root / "procedure-improvement-campaign-report.json").write_text(
            result.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        return result

    def _stores(self):
        memory = ProjectMemoryStore(self.project_memory_root, project_key=self.project_key)
        reliability = ProcedureReliabilityStore(
            self.project_memory_root,
            project_id=memory.project_id,
        )
        revisions = ProcedureRevisionStore(
            self.project_memory_root,
            project_id=memory.project_id,
        )
        campaigns = ProcedureImprovementCampaignStore(
            self.project_memory_root,
            project_id=memory.project_id,
        )
        return memory, reliability, revisions, campaigns

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
        runtime = ProcedureRevisionVerifiedRepositoryCodingTaskRuntime(
            prepared.workspace_root,
            self.core,
            step_root,
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
                    "M31 campaign isolation finalization also failed: "
                    f"{type(finalize_exc).__name__}: {finalize_exc}"
                )
            raise
        manager.finalize(succeeded=report.succeeded)
        return report

    @staticmethod
    def _next_trial_candidate(
        campaign: ProcedureImprovementCampaign,
        revisions: ProcedureRevisionStore,
    ) -> ProcedureRevisionCandidate | None:
        owned = set(campaign.candidate_ids)
        candidates = [
            item
            for item in revisions.state.candidates
            if item.candidate_id in owned
            and item.parent_procedure_id == campaign.parent_procedure_id
            and item.state == ProcedureRevisionState.CANDIDATE
        ]
        candidates.sort(key=lambda item: (item.failure_count, -item.success_count, item.candidate_id))
        return candidates[0] if candidates else None

    @staticmethod
    def _procedure(memory: ProjectMemoryStore, entry_id: str) -> ProjectMemoryEntry:
        for item in memory.state.entries:
            if item.entry_id == entry_id:
                if item.kind != ProjectMemoryEntryKind.PROCEDURE:
                    raise ValueError("campaign parent is not a procedure")
                return item
        raise KeyError(f"unknown project procedure {entry_id}")

    def _step_root(
        self,
        campaign: ProcedureImprovementCampaign,
        pending: PendingProcedureImprovementStep,
    ) -> Path:
        ordinal = campaign.proposal_attempts + campaign.trial_attempts
        candidate = f"-{pending.candidate_id}" if pending.candidate_id else ""
        root = (
            self.output_root
            / "campaigns"
            / campaign.campaign_id
            / f"step-{ordinal:03d}-{pending.kind.value}{candidate}"
        )
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _proposal_task(
        parent: ProjectMemoryEntry,
        reliability: ProcedureReliabilityStore,
        validation_task: str,
    ) -> str:
        record = reliability.record_for(parent.entry_id)
        reason = record.suspension_reason if record is not None else "verified reuse degradation"
        return (
            "M31 bounded procedure-improvement proposal step. "
            f"Parent procedure {parent.entry_id!r} is M29-suspended because {reason!r}. "
            f"Parent statement: {parent.statement!r}. Parent steps: {list(parent.steps)!r}. "
            f"Validation task: {validation_task!r}. "
            "Propose at most one materially different bounded replacement using the "
            "procedure_revision_update candidates protocol. Explain the failure-driven rationale. "
            "Do not declare used_revision_candidate_ids in this proposal step. The source workspace "
            "is isolated and any coding actions are experimental only."
        )

    @staticmethod
    def _trial_task(
        parent: ProjectMemoryEntry,
        candidate: ProcedureRevisionCandidate,
        validation_task: str,
    ) -> str:
        return (
            "M31 bounded isolated procedure-revision validation step. "
            f"Validate existing M30 candidate {candidate.candidate_id!r} for suspended parent "
            f"{parent.entry_id!r}. Candidate statement: {candidate.statement!r}. "
            f"Candidate steps: {list(candidate.steps)!r}. Validation task: {validation_task!r}. "
            "Use the candidate only if it materially guides the implementation; when it does, "
            "declare exactly that candidate ID in procedure_revision_update.used_revision_candidate_ids. "
            "Complete the coding task and satisfy the software-owned verification plan. Do not propose "
            "another revision while this candidate is being trialed."
        )
