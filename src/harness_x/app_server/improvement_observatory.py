"""M76 bounded, read-only projection of existing improvement evidence.

The observatory deliberately does not instantiate any M28-M31 stores because their
constructors create missing state. Observation is a pure file read beneath one fixed
project-local ``.harness-x`` root and never acquires promotion or campaign authority.
"""

from __future__ import annotations

import hashlib
import os
from enum import StrEnum
from pathlib import Path
from typing import Callable, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harness_x import __version__
from harness_x.coding.procedure_improvement_campaign import (
    ProcedureImprovementCampaignReport,
    ProcedureImprovementCampaignStoreState,
)
from harness_x.coding.procedure_reliability import (
    ProcedureReliabilityState,
    ProcedureReliabilityStatus,
)
from harness_x.coding.procedure_revision import ProcedureRevisionStoreState
from harness_x.coding.project_memory import ProjectMemoryState
from harness_x.improvement.closed_loop import ClosedImprovementLoopReport
from harness_x.improvement.experiment import SandboxExperimentReport
from harness_x.improvement.promotion import ActiveConfigPointer, PromotionRecord


_STRICT = ConfigDict(frozen=True, extra="forbid")
_MAX_SCAN_DEPTH = 6
_MAX_SCAN_FILES = 96
_MAX_RECORD_BYTES = 512 * 1024
_MAX_TOTAL_RECORD_BYTES = 4 * 1024 * 1024
_MAX_ROLLBACK_BYTES = 2 * 1024 * 1024
_MAX_ITEMS_PER_SECTION = 48
_MAX_TEXT = 1200

_CANONICAL_PROJECT_MEMORY: dict[str, type[BaseModel]] = {
    ".harness-x/project-memory/project-memory.json": ProjectMemoryState,
    ".harness-x/project-memory/procedure-reliability.json": ProcedureReliabilityState,
    ".harness-x/project-memory/procedure-revisions.json": ProcedureRevisionStoreState,
    ".harness-x/project-memory/procedure-improvement-campaigns.json": (
        ProcedureImprovementCampaignStoreState
    ),
}
_SCAN_RECORD_TYPES: dict[str, type[BaseModel]] = {
    "experiment-report.json": SandboxExperimentReport,
    "promotion-record.json": PromotionRecord,
    "closed-improvement-loop-report.json": ClosedImprovementLoopReport,
    "procedure-improvement-campaign-report.json": ProcedureImprovementCampaignReport,
    "active-config.json": ActiveConfigPointer,
}


class ObservatorySourceStatus(StrEnum):
    OBSERVED = "observed"
    MISSING = "missing"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    SYMLINK_REJECTED = "symlink_rejected"
    SCAN_TRUNCATED = "scan_truncated"
    DUPLICATE_CONFLICT = "duplicate_conflict"


class ObservatorySource(BaseModel):
    model_config = _STRICT

    relative_path: str = Field(min_length=1, max_length=2048)
    record_kind: str = Field(min_length=1, max_length=80)
    status: ObservatorySourceStatus
    size_bytes: int | None = Field(default=None, ge=0)
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    detail: str | None = Field(default=None, max_length=_MAX_TEXT)


class ObservatoryVersion(BaseModel):
    model_config = _STRICT

    system_version: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=2048)
    source_kind: str = Field(min_length=1, max_length=80)
    config_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ObservatoryWeakness(BaseModel):
    model_config = _STRICT

    procedure_id: str = Field(min_length=1, max_length=240)
    reason: str | None = Field(default=None, max_length=_MAX_TEXT)
    usage_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    consecutive_failures: int = Field(ge=0)
    source: str = Field(min_length=1, max_length=2048)


class ObservatoryCandidate(BaseModel):
    model_config = _STRICT

    candidate_id: str = Field(min_length=1, max_length=240)
    candidate_kind: Literal["procedure_revision", "system_improvement"]
    status: str = Field(min_length=1, max_length=80)
    parent_id: str | None = Field(default=None, max_length=240)
    rationale: str | None = Field(default=None, max_length=_MAX_TEXT)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    source: str = Field(min_length=1, max_length=2048)


class ObservatoryExperiment(BaseModel):
    model_config = _STRICT

    candidate_id: str = Field(min_length=1, max_length=240)
    baseline_version: str = Field(min_length=1, max_length=160)
    suite_name: str = Field(min_length=1, max_length=240)
    suite_version: str = Field(min_length=1, max_length=240)
    experiment_valid: bool
    disposition: str = Field(min_length=1, max_length=80)
    regressions: tuple[str, ...] = Field(default=(), max_length=32)
    new_failure_modes: tuple[str, ...] = Field(default=(), max_length=32)
    budget_violations: tuple[str, ...] = Field(default=(), max_length=32)
    source: str = Field(min_length=1, max_length=2048)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ObservatoryRollbackEvidence(BaseModel):
    model_config = _STRICT

    recorded: bool
    recorded_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    independently_verified: bool | None = None
    verification_detail: str = Field(min_length=1, max_length=_MAX_TEXT)


class ObservatoryPromotion(BaseModel):
    model_config = _STRICT

    promotion_id: str = Field(min_length=1, max_length=240)
    candidate_id: str = Field(min_length=1, max_length=240)
    baseline_version: str = Field(min_length=1, max_length=160)
    promoted_version: str | None = Field(default=None, max_length=160)
    status: str = Field(min_length=1, max_length=80)
    qualification_allowed: bool
    verification_passed: bool | None = None
    reason: str = Field(min_length=1, max_length=_MAX_TEXT)
    rollback: ObservatoryRollbackEvidence
    source: str = Field(min_length=1, max_length=2048)


class ObservatoryCampaign(BaseModel):
    model_config = _STRICT

    campaign_id: str = Field(min_length=1, max_length=240)
    parent_procedure_id: str = Field(min_length=1, max_length=240)
    status: str = Field(min_length=1, max_length=80)
    proposal_attempts: int = Field(ge=0)
    trial_attempts: int = Field(ge=0)
    max_candidate_proposals: int = Field(ge=1)
    max_trial_tasks: int = Field(ge=1)
    pending_step_kind: str | None = Field(default=None, max_length=80)
    promoted_candidate_id: str | None = Field(default=None, max_length=240)
    terminal_reason: str | None = Field(default=None, max_length=_MAX_TEXT)
    source: str = Field(min_length=1, max_length=2048)


class ImprovementObservatoryProjection(BaseModel):
    model_config = _STRICT

    schema_version: Literal["improvement-observatory-v1"] = "improvement-observatory-v1"
    project_id: str = Field(min_length=1, max_length=240)
    software_version: str = Field(min_length=1, max_length=160)
    read_only: Literal[True] = True
    promotion_authority: Literal[False] = False
    observatory_root_present: bool
    scan_truncated: bool
    versions: tuple[ObservatoryVersion, ...] = Field(default=(), max_length=_MAX_ITEMS_PER_SECTION)
    weaknesses: tuple[ObservatoryWeakness, ...] = Field(default=(), max_length=_MAX_ITEMS_PER_SECTION)
    candidates: tuple[ObservatoryCandidate, ...] = Field(default=(), max_length=_MAX_ITEMS_PER_SECTION)
    experiments: tuple[ObservatoryExperiment, ...] = Field(default=(), max_length=_MAX_ITEMS_PER_SECTION)
    promotions: tuple[ObservatoryPromotion, ...] = Field(default=(), max_length=_MAX_ITEMS_PER_SECTION)
    campaigns: tuple[ObservatoryCampaign, ...] = Field(default=(), max_length=_MAX_ITEMS_PER_SECTION)
    sources: tuple[ObservatorySource, ...] = Field(default=(), max_length=256)


T = TypeVar("T", bound=BaseModel)


def _short(value: object, *, limit: int = _MAX_TEXT) -> str:
    text = str(value).strip()
    return text[:limit]


def _bounded_strings(values: tuple[str, ...], *, limit: int = 32) -> tuple[str, ...]:
    return tuple(_short(item) for item in values[:limit])


def _safe_relative(workspace: Path, path: Path) -> str:
    return path.relative_to(workspace).as_posix()


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _contains_symlink(root: Path, path: Path) -> bool:
    if path == root:
        return path.is_symlink()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    if current.is_symlink():
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _record_kind(model_type: type[BaseModel]) -> str:
    return model_type.__name__


class _ObservationBuilder:
    def __init__(self, project_id: str, workspace_root: str | Path) -> None:
        self.project_id = project_id
        self.workspace = Path(workspace_root).resolve()
        self.root = self.workspace / ".harness-x"
        self.sources: list[ObservatorySource] = []
        self.versions: list[ObservatoryVersion] = []
        self.weaknesses: list[ObservatoryWeakness] = []
        self.candidates: dict[tuple[str, str], ObservatoryCandidate] = {}
        self.experiments: list[ObservatoryExperiment] = []
        self.promotions: dict[str, ObservatoryPromotion] = {}
        self.campaigns: dict[str, ObservatoryCampaign] = {}
        self.total_record_bytes = 0
        self.scan_files = 0
        self.scan_truncated = False

    def build(self) -> ImprovementObservatoryProjection:
        if not self.workspace.is_dir() or not self.root.is_dir() or self.root.is_symlink():
            return ImprovementObservatoryProjection(
                project_id=self.project_id,
                software_version=__version__,
                observatory_root_present=False,
                scan_truncated=False,
                sources=tuple(self.sources),
            )

        for relative, model_type in _CANONICAL_PROJECT_MEMORY.items():
            path = self.workspace / relative
            if not path.exists():
                self.sources.append(
                    ObservatorySource(
                        relative_path=relative,
                        record_kind=_record_kind(model_type),
                        status=ObservatorySourceStatus.MISSING,
                    )
                )
                continue
            parsed = self._parse_file(path, model_type)
            if parsed is not None:
                self._project(parsed, path, self.sources[-1].source_sha256 or "")

        canonical = {str((self.workspace / item).resolve()) for item in _CANONICAL_PROJECT_MEMORY}
        for directory, directories, filenames in os.walk(self.root, topdown=True, followlinks=False):
            base = Path(directory)
            depth = len(base.relative_to(self.root).parts)
            directories[:] = [
                name
                for name in directories
                if depth < _MAX_SCAN_DEPTH and not (base / name).is_symlink()
            ]
            if depth > _MAX_SCAN_DEPTH:
                directories[:] = []
                continue
            for filename in sorted(filenames):
                model_type = _SCAN_RECORD_TYPES.get(filename)
                if model_type is None:
                    continue
                path = base / filename
                if str(path.resolve()) in canonical:
                    continue
                if self.scan_files >= _MAX_SCAN_FILES:
                    self._mark_scan_truncated("record-count limit reached")
                    break
                if self.total_record_bytes >= _MAX_TOTAL_RECORD_BYTES:
                    self._mark_scan_truncated("aggregate byte limit reached")
                    break
                self.scan_files += 1
                parsed = self._parse_file(path, model_type)
                if parsed is not None:
                    self._project(parsed, path, self.sources[-1].source_sha256 or "")
            if self.scan_truncated:
                break

        return ImprovementObservatoryProjection(
            project_id=self.project_id,
            software_version=__version__,
            observatory_root_present=True,
            scan_truncated=self.scan_truncated,
            versions=tuple(self.versions[:_MAX_ITEMS_PER_SECTION]),
            weaknesses=tuple(self.weaknesses[:_MAX_ITEMS_PER_SECTION]),
            candidates=tuple(self.candidates.values())[:_MAX_ITEMS_PER_SECTION],
            experiments=tuple(self.experiments[:_MAX_ITEMS_PER_SECTION]),
            promotions=tuple(self.promotions.values())[:_MAX_ITEMS_PER_SECTION],
            campaigns=tuple(self.campaigns.values())[:_MAX_ITEMS_PER_SECTION],
            sources=tuple(self.sources[:256]),
        )

    def _mark_scan_truncated(self, detail: str) -> None:
        if self.scan_truncated:
            return
        self.scan_truncated = True
        self.sources.append(
            ObservatorySource(
                relative_path=".harness-x",
                record_kind="observatory_scan",
                status=ObservatorySourceStatus.SCAN_TRUNCATED,
                detail=detail,
            )
        )

    def _parse_file(self, path: Path, model_type: type[T]) -> T | None:
        relative = _safe_relative(self.workspace, path)
        if _contains_symlink(self.root, path):
            self.sources.append(
                ObservatorySource(
                    relative_path=relative,
                    record_kind=_record_kind(model_type),
                    status=ObservatorySourceStatus.SYMLINK_REJECTED,
                    detail="observatory does not follow symlinked evidence",
                )
            )
            return None
        try:
            resolved = path.resolve(strict=True)
            if not _inside(self.root.resolve(strict=True), resolved) or not resolved.is_file():
                raise ValueError("source does not remain an ordinary file inside observatory root")
            size = resolved.stat().st_size
        except (OSError, ValueError) as exc:
            self.sources.append(
                ObservatorySource(
                    relative_path=relative,
                    record_kind=_record_kind(model_type),
                    status=ObservatorySourceStatus.MALFORMED,
                    detail=_short(exc),
                )
            )
            return None
        if size > _MAX_RECORD_BYTES or self.total_record_bytes + size > _MAX_TOTAL_RECORD_BYTES:
            self.sources.append(
                ObservatorySource(
                    relative_path=relative,
                    record_kind=_record_kind(model_type),
                    status=ObservatorySourceStatus.OVERSIZED,
                    size_bytes=size,
                    detail="record exceeds observatory read budget",
                )
            )
            if self.total_record_bytes + size > _MAX_TOTAL_RECORD_BYTES:
                self._mark_scan_truncated("aggregate byte limit reached")
            return None
        try:
            payload = resolved.read_bytes()
            if len(payload) != size:
                raise ValueError("source changed while being observed")
            parsed = model_type.model_validate_json(payload)
        except (OSError, ValueError, ValidationError) as exc:
            self.sources.append(
                ObservatorySource(
                    relative_path=relative,
                    record_kind=_record_kind(model_type),
                    status=ObservatorySourceStatus.MALFORMED,
                    size_bytes=size,
                    detail=_short(exc),
                )
            )
            return None
        digest = hashlib.sha256(payload).hexdigest()
        self.total_record_bytes += size
        self.sources.append(
            ObservatorySource(
                relative_path=relative,
                record_kind=_record_kind(model_type),
                status=ObservatorySourceStatus.OBSERVED,
                size_bytes=size,
                source_sha256=digest,
            )
        )
        return parsed

    def _project(self, record: BaseModel, path: Path, source_sha256: str) -> None:
        source = _safe_relative(self.workspace, path)
        if isinstance(record, ProjectMemoryState):
            return
        if isinstance(record, ProcedureReliabilityState):
            for item in record.records:
                if item.status != ProcedureReliabilityStatus.SUSPENDED:
                    continue
                self.weaknesses.append(
                    ObservatoryWeakness(
                        procedure_id=item.procedure_id,
                        reason=_short(item.suspension_reason) if item.suspension_reason else None,
                        usage_count=item.usage_count,
                        success_count=item.success_count,
                        failure_count=item.failure_count,
                        consecutive_failures=item.consecutive_failures,
                        source=source,
                    )
                )
            return
        if isinstance(record, ProcedureRevisionStoreState):
            for item in record.candidates:
                self._put_candidate(
                    ObservatoryCandidate(
                        candidate_id=item.candidate_id,
                        candidate_kind="procedure_revision",
                        status=item.state.value,
                        parent_id=item.parent_procedure_id,
                        rationale=_short(item.rationale),
                        success_count=item.success_count,
                        failure_count=item.failure_count,
                        source=source,
                    )
                )
            return
        if isinstance(record, ProcedureImprovementCampaignStoreState):
            for item in record.campaigns:
                self._put_campaign(self._campaign_summary(item, source))
            return
        if isinstance(record, ProcedureImprovementCampaignReport):
            self._put_campaign(self._campaign_summary(record.campaign, source))
            return
        if isinstance(record, SandboxExperimentReport):
            self.versions.append(
                ObservatoryVersion(
                    system_version=str(record.baseline_version),
                    source=source,
                    source_kind="sandbox_experiment",
                )
            )
            self.experiments.append(
                ObservatoryExperiment(
                    candidate_id=str(record.candidate_id),
                    baseline_version=str(record.baseline_version),
                    suite_name=_short(record.suite_name, limit=240),
                    suite_version=_short(record.suite_version, limit=240),
                    experiment_valid=record.experiment_valid,
                    disposition=record.disposition.value,
                    regressions=_bounded_strings(record.regressions),
                    new_failure_modes=_bounded_strings(record.new_failure_modes),
                    budget_violations=_bounded_strings(record.budget_violations),
                    source=source,
                    source_sha256=source_sha256,
                )
            )
            return
        if isinstance(record, PromotionRecord):
            self.versions.append(
                ObservatoryVersion(
                    system_version=str(record.baseline_version),
                    source=source,
                    source_kind="promotion_baseline",
                    config_sha256=record.baseline_config_sha256,
                )
            )
            if record.promoted_version is not None:
                self.versions.append(
                    ObservatoryVersion(
                        system_version=str(record.promoted_version),
                        source=source,
                        source_kind="promotion_target",
                        config_sha256=record.promoted_config_sha256,
                    )
                )
            summary = ObservatoryPromotion(
                promotion_id=record.promotion_id,
                candidate_id=record.candidate_id,
                baseline_version=str(record.baseline_version),
                promoted_version=(
                    str(record.promoted_version) if record.promoted_version is not None else None
                ),
                status=record.status.value,
                qualification_allowed=record.qualification.allowed,
                verification_passed=(record.verification.passed if record.verification else None),
                reason=_short(record.reason),
                rollback=self._rollback_evidence(record),
                source=source,
            )
            self._put_promotion(summary)
            return
        if isinstance(record, ActiveConfigPointer):
            self.versions.append(
                ObservatoryVersion(
                    system_version=str(record.system_version),
                    source=source,
                    source_kind="active_config_pointer",
                    config_sha256=record.config_sha256,
                )
            )
            return
        if isinstance(record, ClosedImprovementLoopReport):
            self.versions.append(
                ObservatoryVersion(
                    system_version=str(record.baseline_version),
                    source=source,
                    source_kind="closed_loop_baseline",
                )
            )
            if record.promoted_version is not None:
                self.versions.append(
                    ObservatoryVersion(
                        system_version=str(record.promoted_version),
                        source=source,
                        source_kind="closed_loop_promoted",
                    )
                )
            self._put_candidate(
                ObservatoryCandidate(
                    candidate_id=record.candidate_id,
                    candidate_kind="system_improvement",
                    status=record.candidate_status.value,
                    rationale=(
                        _short(record.initial_analysis.proposal.hypothesis.statement)
                        if record.initial_analysis.proposal is not None
                        else None
                    ),
                    source=source,
                )
            )
            return

    def _campaign_summary(self, item, source: str) -> ObservatoryCampaign:
        return ObservatoryCampaign(
            campaign_id=item.campaign_id,
            parent_procedure_id=item.parent_procedure_id,
            status=item.status.value,
            proposal_attempts=item.proposal_attempts,
            trial_attempts=item.trial_attempts,
            max_candidate_proposals=item.budget.max_candidate_proposals,
            max_trial_tasks=item.budget.max_trial_tasks,
            pending_step_kind=(item.pending_step.kind.value if item.pending_step else None),
            promoted_candidate_id=item.promoted_candidate_id,
            terminal_reason=_short(item.terminal_reason) if item.terminal_reason else None,
            source=source,
        )

    def _put_candidate(self, item: ObservatoryCandidate) -> None:
        key = (item.candidate_kind, item.candidate_id)
        prior = self.candidates.get(key)
        if prior is None:
            self.candidates[key] = item
            return
        if prior.model_dump(exclude={"source"}) == item.model_dump(exclude={"source"}):
            return
        self._conflict(item.source, f"candidate:{item.candidate_kind}:{item.candidate_id}")

    def _put_promotion(self, item: ObservatoryPromotion) -> None:
        prior = self.promotions.get(item.promotion_id)
        if prior is None:
            self.promotions[item.promotion_id] = item
            return
        if prior.model_dump(exclude={"source"}) == item.model_dump(exclude={"source"}):
            return
        self._conflict(item.source, f"promotion:{item.promotion_id}")

    def _put_campaign(self, item: ObservatoryCampaign) -> None:
        prior = self.campaigns.get(item.campaign_id)
        if prior is None:
            self.campaigns[item.campaign_id] = item
            return
        if prior.model_dump(exclude={"source"}) == item.model_dump(exclude={"source"}):
            return
        self._conflict(item.source, f"campaign:{item.campaign_id}")

    def _conflict(self, source: str, identity: str) -> None:
        self.sources.append(
            ObservatorySource(
                relative_path=source,
                record_kind="identity_conflict",
                status=ObservatorySourceStatus.DUPLICATE_CONFLICT,
                detail=_short(identity),
            )
        )

    def _rollback_evidence(self, record: PromotionRecord) -> ObservatoryRollbackEvidence:
        if not record.rollback_artifact_path or not record.rollback_artifact_sha256:
            return ObservatoryRollbackEvidence(
                recorded=False,
                verification_detail="promotion record contains no rollback artifact identity",
            )
        raw = Path(record.rollback_artifact_path)
        candidate = raw if raw.is_absolute() else self.workspace / raw
        try:
            if _contains_symlink(self.root, candidate):
                raise ValueError("rollback artifact path contains a symlink")
            resolved_root = self.root.resolve(strict=True)
            resolved = candidate.resolve(strict=True)
            if not _inside(resolved_root, resolved) or not resolved.is_file():
                raise ValueError("rollback artifact is outside the fixed observatory root")
            size = resolved.stat().st_size
            if size > _MAX_ROLLBACK_BYTES:
                raise ValueError("rollback artifact exceeds independent verification byte limit")
            payload = resolved.read_bytes()
            if len(payload) != size:
                raise ValueError("rollback artifact changed during verification")
            verified = hashlib.sha256(payload).hexdigest() == record.rollback_artifact_sha256
            return ObservatoryRollbackEvidence(
                recorded=True,
                recorded_sha256=record.rollback_artifact_sha256,
                independently_verified=verified,
                verification_detail=(
                    "bounded in-root rollback bytes match recorded SHA-256"
                    if verified
                    else "bounded in-root rollback bytes do not match recorded SHA-256"
                ),
            )
        except (OSError, ValueError) as exc:
            return ObservatoryRollbackEvidence(
                recorded=True,
                recorded_sha256=record.rollback_artifact_sha256,
                independently_verified=None,
                verification_detail=_short(exc),
            )


def build_improvement_observatory(
    *,
    project_id: str,
    workspace_root: str | Path,
) -> ImprovementObservatoryProjection:
    """Project existing improvement artifacts without mutating or repairing any source."""

    return _ObservationBuilder(project_id, workspace_root).build()


__all__ = [
    "ImprovementObservatoryProjection",
    "ObservatoryCampaign",
    "ObservatoryCandidate",
    "ObservatoryExperiment",
    "ObservatoryPromotion",
    "ObservatoryRollbackEvidence",
    "ObservatorySource",
    "ObservatorySourceStatus",
    "ObservatoryVersion",
    "ObservatoryWeakness",
    "build_improvement_observatory",
]
