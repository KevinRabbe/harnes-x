"""Evidence-gated live promotion for bounded configuration improvements."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from harness_x.benchmarks import run_scripted_autonomy_benchmark
from harness_x.config import HarnessConfig, ImprovementPromotionConfig
from harness_x.core.ids import SystemVersion

from .experiment import ExperimentDisposition, SandboxExperimentReport
from .models import CandidateRiskLevel, CandidateStatus, ImprovementCandidate, canonical_json
from .registry import ImprovementCandidateRegistry
from .sandbox import apply_candidate_to_snapshot, snapshot_from_config


_STRICT_FROZEN = ConfigDict(frozen=True, extra="forbid")


class PromotionError(RuntimeError):
    pass


class PromotionStatus(StrEnum):
    DENIED = "denied"
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"


class ConfigArtifact(BaseModel):
    model_config = _STRICT_FROZEN

    schema_version: str = "active-config-artifact-v1"
    system_version: SystemVersion
    config_sha256: str = Field(min_length=64, max_length=64)
    config: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_digest(self) -> "ConfigArtifact":
        digest = hashlib.sha256(canonical_json(self.config).encode("utf-8")).hexdigest()
        if digest != self.config_sha256:
            raise ValueError("config artifact digest mismatch")
        return self


class ActiveConfigPointer(BaseModel):
    model_config = _STRICT_FROZEN

    schema_version: str = "active-config-pointer-v1"
    system_version: SystemVersion
    config_sha256: str = Field(min_length=64, max_length=64)
    artifact_path: str = Field(min_length=1)


class PromotionQualification(BaseModel):
    model_config = _STRICT_FROZEN

    allowed: bool
    reasons: tuple[str, ...]
    policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_reasons(self) -> "PromotionQualification":
        if self.allowed and self.reasons:
            raise ValueError("allowed promotion qualification cannot have denial reasons")
        if not self.allowed and not self.reasons:
            raise ValueError("denied promotion qualification requires reasons")
        return self


class PromotionVerificationResult(BaseModel):
    model_config = _STRICT_FROZEN

    verifier_name: str = Field(min_length=1)
    verifier_version: str = Field(min_length=1)
    system_version: SystemVersion
    passed: bool
    checks: dict[str, bool]
    notes: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            canonical_json(self.model_dump(mode="json")).encode("utf-8")
        ).hexdigest()


class PromotionRecord(BaseModel):
    model_config = _STRICT_FROZEN

    schema_version: str = "improvement-promotion-record-v1"
    promotion_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    proposal_fingerprint: str = Field(min_length=64, max_length=64)
    experiment_report_fingerprint: str = Field(min_length=64, max_length=64)
    baseline_version: SystemVersion
    promoted_version: SystemVersion | None = None
    baseline_config_sha256: str = Field(min_length=64, max_length=64)
    promoted_config_sha256: str | None = None
    qualification: PromotionQualification
    verification: PromotionVerificationResult | None = None
    rollback_artifact_path: str | None = None
    rollback_artifact_sha256: str | None = None
    status: PromotionStatus
    reason: str = Field(min_length=1)


class PromotionVerifier(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def verify(
        self,
        config: HarnessConfig,
        *,
        required_tests: tuple[str, ...],
        output_directory: Path,
    ) -> PromotionVerificationResult: ...


def _report_fingerprint(report: SandboxExperimentReport) -> str:
    return hashlib.sha256(
        canonical_json(report.model_dump(mode="json")).encode("utf-8")
    ).hexdigest()


def _risk_rank(value: str | CandidateRiskLevel) -> int:
    mapping = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    key = value.value if isinstance(value, CandidateRiskLevel) else value.strip().casefold()
    return mapping.get(key, 99)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


class VersionedConfigStore:
    """Immutable config artifacts plus one atomically replaced active pointer."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.versions = self.root / "versions"
        self.pointer_path = self.root / "active-config.json"
        self.promotions = self.root / "promotions"
        self.versions.mkdir(parents=True, exist_ok=True)
        self.promotions.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def artifact_for(config: HarnessConfig) -> ConfigArtifact:
        payload = config.model_dump(mode="json")
        digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        return ConfigArtifact(
            system_version=config.system_version,
            config_sha256=digest,
            config=payload,
        )

    def register(self, config: HarnessConfig) -> ConfigArtifact:
        artifact = self.artifact_for(config)
        path = self.versions / f"{artifact.config_sha256}.json"
        encoded = artifact.model_dump_json(indent=2) + "\n"
        if path.exists():
            existing = ConfigArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            if existing != artifact:
                raise PromotionError("immutable config artifact hash collision")
        else:
            path.write_text(encoded, encoding="utf-8")
        return artifact

    def initialize(self, config: HarnessConfig) -> ConfigArtifact:
        artifact = self.register(config)
        if self.pointer_path.exists():
            active = self.active_artifact()
            if active != artifact:
                raise PromotionError("config store already initialized to a different active artifact")
            return active
        self.activate(artifact)
        return artifact

    def artifact_path(self, artifact: ConfigArtifact) -> Path:
        return self.versions / f"{artifact.config_sha256}.json"

    def activate(self, artifact: ConfigArtifact) -> None:
        path = self.artifact_path(artifact)
        if not path.is_file():
            raise PromotionError("cannot activate an unregistered config artifact")
        pointer = ActiveConfigPointer(
            system_version=artifact.system_version,
            config_sha256=artifact.config_sha256,
            artifact_path=path.relative_to(self.root).as_posix(),
        )
        _atomic_write(self.pointer_path, pointer.model_dump_json(indent=2) + "\n")

    def active_pointer(self) -> ActiveConfigPointer:
        if not self.pointer_path.is_file():
            raise PromotionError("active config pointer does not exist")
        return ActiveConfigPointer.model_validate_json(
            self.pointer_path.read_text(encoding="utf-8")
        )

    def active_artifact(self) -> ConfigArtifact:
        pointer = self.active_pointer()
        path = self.root / pointer.artifact_path
        if not path.is_file():
            raise PromotionError("active config artifact is missing")
        artifact = ConfigArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        if artifact.config_sha256 != pointer.config_sha256:
            raise PromotionError("active pointer/config digest mismatch")
        if artifact.system_version != pointer.system_version:
            raise PromotionError("active pointer/config version mismatch")
        return artifact

    def active_config(self) -> HarnessConfig:
        return HarnessConfig.model_validate(self.active_artifact().config)


class InitialPromotionPolicy:
    """Deterministic promotion policy configured by HarnessConfig."""

    def evaluate(
        self,
        candidate: ImprovementCandidate,
        report: SandboxExperimentReport,
        active_config: HarnessConfig,
        settings: ImprovementPromotionConfig,
        *,
        operator_approved: bool,
    ) -> PromotionQualification:
        reasons: list[str] = []
        if candidate.status != CandidateStatus.SANDBOX_ELIGIBLE:
            reasons.append("candidate_not_sandbox_eligible")
        if report.candidate_id != candidate.candidate_id:
            reasons.append("experiment_candidate_mismatch")
        if report.proposal_fingerprint != candidate.proposal_fingerprint:
            reasons.append("experiment_proposal_fingerprint_mismatch")
        if report.baseline_version != candidate.proposal.baseline_version:
            reasons.append("experiment_baseline_mismatch")
        if active_config.system_version != candidate.proposal.baseline_version:
            reasons.append("active_baseline_is_stale")
        if snapshot_from_config(active_config).fingerprint != report.baseline_snapshot_fingerprint:
            reasons.append("active_baseline_snapshot_mismatch")
        if not report.experiment_valid:
            reasons.append("experiment_not_valid")
        if report.disposition != ExperimentDisposition.PROMOTION_RECOMMENDED:
            reasons.append("sandbox_did_not_recommend_promotion")
        if candidate.proposal.change_type.value not in settings.allowed_change_types:
            reasons.append("change_type_not_live_promotable")
        if _risk_rank(candidate.proposal.risk_level) > _risk_rank(settings.max_risk_level):
            reasons.append("risk_level_exceeds_live_policy")
        if len(report.seeds) < settings.min_paired_runs:
            reasons.append("insufficient_paired_runs")
        if settings.require_zero_regressions and report.regressions:
            reasons.append("sandbox_regressions_present")
        if settings.require_zero_new_failure_modes and report.new_failure_modes:
            reasons.append("new_failure_modes_present")
        if settings.require_zero_budget_violations and report.budget_violations:
            reasons.append("experiment_budget_violations_present")
        if settings.require_baseline_untouched and not report.baseline_untouched:
            reasons.append("sandbox_baseline_not_untouched")
        if settings.require_teardown_verified and not report.teardown_verified:
            reasons.append("sandbox_teardown_not_verified")
        if not settings.allow_auto_promotion and not operator_approved:
            reasons.append("operator_approval_required")
        return PromotionQualification(
            allowed=not reasons,
            reasons=tuple(dict.fromkeys(reasons)),
            policy_version=settings.policy_version,
        )


class ScriptedAutonomyPromotionVerifier:
    """Post-activation verifier backed by the permanent long-horizon suite."""

    name = "scripted-autonomy-promotion-verifier"
    version = "v1"

    def verify(
        self,
        config: HarnessConfig,
        *,
        required_tests: tuple[str, ...],
        output_directory: Path,
    ) -> PromotionVerificationResult:
        output_directory.mkdir(parents=True, exist_ok=True)
        report = run_scripted_autonomy_benchmark(config, output_directory)
        checks = {
            "trace_replay": all(item.replay_valid for item in report.scenarios),
            "design_invariants": report.passed
            and all(item.illegal_transitions == 0 for item in report.scenarios),
        }
        for required in required_tests:
            checks.setdefault(required, False)
        result = PromotionVerificationResult(
            verifier_name=self.name,
            verifier_version=self.version,
            system_version=config.system_version,
            passed=report.passed and all(checks.get(item, False) for item in required_tests),
            checks=checks,
        )
        (output_directory / "promotion-verification.json").write_text(
            result.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return result


class PromotionAuthority:
    """Narrow live authority for bounded config artifacts only."""

    def __init__(
        self,
        store: VersionedConfigStore,
        registry: ImprovementCandidateRegistry,
        verifier: PromotionVerifier,
        *,
        policy: InitialPromotionPolicy | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.verifier = verifier
        self.policy = policy or InitialPromotionPolicy()

    def promote(
        self,
        candidate: ImprovementCandidate,
        report: SandboxExperimentReport,
        output_directory: str | Path,
        *,
        operator_approved: bool = False,
    ) -> PromotionRecord:
        root = Path(output_directory)
        root.mkdir(parents=True, exist_ok=True)
        active_config = self.store.active_config()
        settings = active_config.improvement.promotion
        qualification = self.policy.evaluate(
            candidate,
            report,
            active_config,
            settings,
            operator_approved=operator_approved,
        )
        report_fingerprint = _report_fingerprint(report)
        baseline_artifact = self.store.active_artifact()
        promotion_id = f"promotion_{uuid4().hex}"

        if not qualification.allowed:
            record = PromotionRecord(
                promotion_id=promotion_id,
                candidate_id=str(candidate.candidate_id),
                proposal_fingerprint=candidate.proposal_fingerprint,
                experiment_report_fingerprint=report_fingerprint,
                baseline_version=active_config.system_version,
                baseline_config_sha256=baseline_artifact.config_sha256,
                qualification=qualification,
                status=PromotionStatus.DENIED,
                reason=";".join(qualification.reasons),
            )
            (root / "promotion-record.json").write_text(
                record.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
            return record

        candidate_snapshot = apply_candidate_to_snapshot(
            snapshot_from_config(active_config), candidate
        )
        short = str(candidate.candidate_id).removeprefix("candidate_")[:12]
        promoted_version = SystemVersion(
            value=f"{active_config.system_version.value}+improvement.{short}.1"
        )
        promoted_state = dict(candidate_snapshot.state)
        promoted_state["system_version"] = promoted_version.model_dump(mode="json")
        promoted_config = HarnessConfig.model_validate(promoted_state)
        promoted_artifact = self.store.register(promoted_config)

        promotion_dir = self.store.promotions / promotion_id
        promotion_dir.mkdir(parents=True, exist_ok=False)
        rollback_path = promotion_dir / "rollback-baseline-artifact.json"
        shutil.copy2(self.store.artifact_path(baseline_artifact), rollback_path)
        rollback_hash = hashlib.sha256(rollback_path.read_bytes()).hexdigest()

        self.store.activate(promoted_artifact)
        verification_dir = root / "post-promotion-verification"
        try:
            verification = self.verifier.verify(
                self.store.active_config(),
                required_tests=candidate.proposal.required_tests,
                output_directory=verification_dir,
            )
            active_after = self.store.active_artifact()
            activation_ok = (
                active_after.config_sha256 == promoted_artifact.config_sha256
                and active_after.system_version == promoted_version
            )
        except Exception:
            self.store.activate(baseline_artifact)
            raise

        if not verification.passed or not activation_ok:
            self.store.activate(baseline_artifact)
            evidence = (
                f"experiment:{report_fingerprint}",
                f"verification:{verification.fingerprint}",
                f"rollback:{rollback_hash}",
            )
            self.registry.invalidate(
                candidate.candidate_id,
                reason="post_promotion_verification_failed_and_rolled_back",
                evidence_refs=evidence,
            )
            record = PromotionRecord(
                promotion_id=promotion_id,
                candidate_id=str(candidate.candidate_id),
                proposal_fingerprint=candidate.proposal_fingerprint,
                experiment_report_fingerprint=report_fingerprint,
                baseline_version=active_config.system_version,
                promoted_version=promoted_version,
                baseline_config_sha256=baseline_artifact.config_sha256,
                promoted_config_sha256=promoted_artifact.config_sha256,
                qualification=qualification,
                verification=verification,
                rollback_artifact_path=str(rollback_path),
                rollback_artifact_sha256=rollback_hash,
                status=PromotionStatus.ROLLED_BACK,
                reason="post_promotion_verification_failed",
            )
            (root / "promotion-record.json").write_text(
                record.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
            return record

        evidence = (
            f"experiment:{report_fingerprint}",
            f"verification:{verification.fingerprint}",
            f"rollback:{rollback_hash}",
        )
        self.registry.record_promotion(
            candidate.candidate_id,
            promoted_version=promoted_version,
            evidence_refs=evidence,
        )
        record = PromotionRecord(
            promotion_id=promotion_id,
            candidate_id=str(candidate.candidate_id),
            proposal_fingerprint=candidate.proposal_fingerprint,
            experiment_report_fingerprint=report_fingerprint,
            baseline_version=active_config.system_version,
            promoted_version=promoted_version,
            baseline_config_sha256=baseline_artifact.config_sha256,
            promoted_config_sha256=promoted_artifact.config_sha256,
            qualification=qualification,
            verification=verification,
            rollback_artifact_path=str(rollback_path),
            rollback_artifact_sha256=rollback_hash,
            status=PromotionStatus.ACTIVE,
            reason="sandbox_evidence_and_post_promotion_verification_passed",
        )
        (root / "promotion-record.json").write_text(
            record.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return record

    def rollback(
        self,
        record: PromotionRecord,
        output_directory: str | Path,
        *,
        reason: str,
    ) -> PromotionRecord:
        if record.status != PromotionStatus.ACTIVE or record.promoted_version is None:
            raise PromotionError("only an active promotion can be rolled back")
        active = self.store.active_artifact()
        if active.system_version != record.promoted_version:
            raise PromotionError("active system no longer matches promotion being rolled back")
        if not record.rollback_artifact_path or not record.rollback_artifact_sha256:
            raise PromotionError("promotion record lacks rollback artifact")
        rollback_path = Path(record.rollback_artifact_path)
        if hashlib.sha256(rollback_path.read_bytes()).hexdigest() != record.rollback_artifact_sha256:
            raise PromotionError("rollback artifact hash mismatch")
        baseline = ConfigArtifact.model_validate_json(rollback_path.read_text(encoding="utf-8"))
        if baseline.config_sha256 != record.baseline_config_sha256:
            raise PromotionError("rollback artifact does not match recorded baseline")
        registered = self.store.register(HarnessConfig.model_validate(baseline.config))
        self.store.activate(registered)
        verification = self.verifier.verify(
            self.store.active_config(),
            required_tests=("trace_replay",),
            output_directory=Path(output_directory),
        )
        if not verification.passed:
            raise PromotionError("rollback verification failed")
        self.registry.invalidate(
            candidate_id=self.registry.require(
                next(
                    item.candidate_id
                    for item in self.registry.all()
                    if str(item.candidate_id) == record.candidate_id
                )
            ).candidate_id,
            reason=reason,
            evidence_refs=(
                f"rollback:{record.rollback_artifact_sha256}",
                f"verification:{verification.fingerprint}",
            ),
        )
        return record.model_copy(
            update={
                "verification": verification,
                "status": PromotionStatus.ROLLED_BACK,
                "reason": reason,
            }
        )
