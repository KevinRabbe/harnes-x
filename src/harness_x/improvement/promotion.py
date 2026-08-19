"""Evidence-gated live promotion for bounded Harness X configuration changes."""

from __future__ import annotations

import hashlib
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
from .sandbox import _apply_candidate_snapshot, snapshot_from_config

_STRICT = ConfigDict(frozen=True, extra="forbid")


class PromotionError(RuntimeError):
    pass


class PromotionStatus(StrEnum):
    DENIED = "denied"
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"


class ConfigArtifact(BaseModel):
    model_config = _STRICT
    system_version: SystemVersion
    config_sha256: str = Field(min_length=64, max_length=64)
    config: dict[str, JsonValue]

    @model_validator(mode="after")
    def verify_hash(self) -> "ConfigArtifact":
        actual = hashlib.sha256(canonical_json(self.config).encode()).hexdigest()
        if actual != self.config_sha256:
            raise ValueError("config artifact hash mismatch")
        return self


class ActiveConfigPointer(BaseModel):
    model_config = _STRICT
    system_version: SystemVersion
    config_sha256: str = Field(min_length=64, max_length=64)
    artifact_path: str = Field(min_length=1)


class PromotionQualification(BaseModel):
    model_config = _STRICT
    allowed: bool
    reasons: tuple[str, ...]
    policy_version: str = Field(min_length=1)


class PromotionVerificationResult(BaseModel):
    model_config = _STRICT
    verifier_name: str
    verifier_version: str
    system_version: SystemVersion
    passed: bool
    checks: dict[str, bool]
    notes: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            canonical_json(self.model_dump(mode="json")).encode()
        ).hexdigest()


class PromotionRecord(BaseModel):
    model_config = _STRICT
    promotion_id: str
    candidate_id: str
    proposal_fingerprint: str = Field(min_length=64, max_length=64)
    experiment_report_fingerprint: str = Field(min_length=64, max_length=64)
    baseline_version: SystemVersion
    baseline_config_sha256: str = Field(min_length=64, max_length=64)
    promoted_version: SystemVersion | None = None
    promoted_config_sha256: str | None = None
    rollback_artifact_path: str | None = None
    rollback_artifact_sha256: str | None = None
    qualification: PromotionQualification
    verification: PromotionVerificationResult | None = None
    status: PromotionStatus
    reason: str


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


def _hash_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


class VersionedConfigStore:
    """Immutable config artifacts selected by one atomically replaced pointer."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.versions = self.root / "versions"
        self.promotions = self.root / "promotions"
        self.pointer_path = self.root / "active-config.json"
        self.versions.mkdir(parents=True, exist_ok=True)
        self.promotions.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def build_artifact(config: HarnessConfig) -> ConfigArtifact:
        payload = config.model_dump(mode="json")
        return ConfigArtifact(
            system_version=config.system_version,
            config_sha256=_hash_json(payload),
            config=payload,
        )

    def register(self, config: HarnessConfig) -> ConfigArtifact:
        artifact = self.build_artifact(config)
        path = self.versions / f"{artifact.config_sha256}.json"
        if path.exists():
            existing = ConfigArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            if existing != artifact:
                raise PromotionError("immutable config artifact collision")
        else:
            path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return artifact

    def initialize(self, config: HarnessConfig) -> ConfigArtifact:
        artifact = self.register(config)
        if self.pointer_path.exists():
            if self.active_artifact() != artifact:
                raise PromotionError("store already points at a different active config")
        else:
            self.activate(artifact)
        return artifact

    def path_for(self, artifact: ConfigArtifact) -> Path:
        return self.versions / f"{artifact.config_sha256}.json"

    def activate(self, artifact: ConfigArtifact) -> None:
        path = self.path_for(artifact)
        if not path.is_file():
            raise PromotionError("cannot activate unregistered config artifact")
        pointer = ActiveConfigPointer(
            system_version=artifact.system_version,
            config_sha256=artifact.config_sha256,
            artifact_path=path.relative_to(self.root).as_posix(),
        )
        _atomic_write(self.pointer_path, pointer.model_dump_json(indent=2) + "\n")

    def active_pointer(self) -> ActiveConfigPointer:
        if not self.pointer_path.is_file():
            raise PromotionError("active config pointer is missing")
        return ActiveConfigPointer.model_validate_json(
            self.pointer_path.read_text(encoding="utf-8")
        )

    def active_artifact(self) -> ConfigArtifact:
        pointer = self.active_pointer()
        artifact = ConfigArtifact.model_validate_json(
            (self.root / pointer.artifact_path).read_text(encoding="utf-8")
        )
        if artifact.config_sha256 != pointer.config_sha256:
            raise PromotionError("active pointer/config digest mismatch")
        if artifact.system_version != pointer.system_version:
            raise PromotionError("active pointer/config version mismatch")
        return artifact

    def active_config(self) -> HarnessConfig:
        return HarnessConfig.model_validate(self.active_artifact().config)


class InitialPromotionPolicy:
    """Deterministic/configured authority check immediately before live activation."""

    _risk = {"low": 0, "medium": 1, "high": 2, "critical": 3}

    def evaluate(
        self,
        candidate: ImprovementCandidate,
        report: SandboxExperimentReport,
        active: HarnessConfig,
        settings: ImprovementPromotionConfig,
        *,
        operator_approved: bool,
    ) -> PromotionQualification:
        reasons: list[str] = []
        if candidate.status != CandidateStatus.SANDBOX_ELIGIBLE:
            reasons.append("candidate_not_sandbox_eligible")
        if report.candidate_id != candidate.candidate_id:
            reasons.append("candidate_id_mismatch")
        if report.proposal_fingerprint != candidate.proposal_fingerprint:
            reasons.append("proposal_fingerprint_mismatch")
        if report.baseline_version != candidate.proposal.baseline_version:
            reasons.append("report_baseline_mismatch")
        if active.system_version != candidate.proposal.baseline_version:
            reasons.append("active_baseline_is_stale")
        if snapshot_from_config(active).fingerprint != report.baseline_snapshot_fingerprint:
            reasons.append("active_snapshot_mismatch")
        if not report.experiment_valid:
            reasons.append("experiment_invalid")
        if report.disposition != ExperimentDisposition.PROMOTION_RECOMMENDED:
            reasons.append("promotion_not_recommended")
        if candidate.proposal.change_type.value not in settings.allowed_change_types:
            reasons.append("change_type_not_live_promotable")
        if self._risk.get(candidate.proposal.risk_level.value, 99) > self._risk.get(
            settings.max_risk_level, 99
        ):
            reasons.append("risk_exceeds_live_policy")
        if len(report.seeds) < settings.min_paired_runs:
            reasons.append("insufficient_paired_runs")
        if settings.require_zero_regressions and report.regressions:
            reasons.append("regressions_present")
        if settings.require_zero_new_failure_modes and report.new_failure_modes:
            reasons.append("new_failure_modes_present")
        if settings.require_zero_budget_violations and report.budget_violations:
            reasons.append("budget_violations_present")
        if settings.require_baseline_untouched and not report.baseline_untouched:
            reasons.append("baseline_not_untouched")
        if settings.require_teardown_verified and not report.teardown_verified:
            reasons.append("teardown_not_verified")
        if not settings.allow_auto_promotion and not operator_approved:
            reasons.append("operator_approval_required")
        return PromotionQualification(
            allowed=not reasons,
            reasons=tuple(dict.fromkeys(reasons)),
            policy_version=settings.policy_version,
        )


class ScriptedAutonomyPromotionVerifier:
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
            passed=report.passed and all(checks[item] for item in required_tests),
            checks=checks,
        )
        (output_directory / "promotion-verification.json").write_text(
            result.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return result


class PromotionAuthority:
    """Only this object can move the active config pointer in Milestone 16."""

    def __init__(
        self,
        store: VersionedConfigStore,
        registry: ImprovementCandidateRegistry,
        verifier: PromotionVerifier,
    ) -> None:
        self.store = store
        self.registry = registry
        self.verifier = verifier
        self.policy = InitialPromotionPolicy()

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
        active = self.store.active_config()
        baseline_artifact = self.store.active_artifact()
        qualification = self.policy.evaluate(
            candidate,
            report,
            active,
            active.improvement.promotion,
            operator_approved=operator_approved,
        )
        report_hash = _hash_json(report.model_dump(mode="json"))
        promotion_id = f"promotion_{uuid4().hex}"

        if not qualification.allowed:
            result = PromotionRecord(
                promotion_id=promotion_id,
                candidate_id=str(candidate.candidate_id),
                proposal_fingerprint=candidate.proposal_fingerprint,
                experiment_report_fingerprint=report_hash,
                baseline_version=active.system_version,
                baseline_config_sha256=baseline_artifact.config_sha256,
                qualification=qualification,
                status=PromotionStatus.DENIED,
                reason=";".join(qualification.reasons),
            )
            (root / "promotion-record.json").write_text(
                result.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
            return result

        sandbox_candidate = _apply_candidate_snapshot(snapshot_from_config(active), candidate)
        short = str(candidate.candidate_id).removeprefix("candidate_")[:12]
        promoted_version = SystemVersion(
            value=f"{active.system_version.value}+improvement.{short}.1"
        )
        state = dict(sandbox_candidate.state)
        state["system_version"] = promoted_version.model_dump(mode="json")
        promoted_config = HarnessConfig.model_validate(state)
        promoted_artifact = self.store.register(promoted_config)

        promotion_dir = self.store.promotions / promotion_id
        promotion_dir.mkdir(parents=True, exist_ok=False)
        rollback_path = promotion_dir / "rollback-baseline-artifact.json"
        shutil.copy2(self.store.path_for(baseline_artifact), rollback_path)
        rollback_hash = hashlib.sha256(rollback_path.read_bytes()).hexdigest()

        self.store.activate(promoted_artifact)
        try:
            verification = self.verifier.verify(
                self.store.active_config(),
                required_tests=candidate.proposal.required_tests,
                output_directory=root / "post-promotion-verification",
            )
        except Exception:
            self.store.activate(baseline_artifact)
            raise

        activation_ok = self.store.active_artifact() == promoted_artifact
        if not verification.passed or not activation_ok:
            self.store.activate(baseline_artifact)
            evidence = (
                f"experiment:{report_hash}",
                f"verification:{verification.fingerprint}",
                f"rollback:{rollback_hash}",
            )
            self.registry.invalidate(
                candidate.candidate_id,
                reason="post_promotion_verification_failed_and_rolled_back",
                evidence_refs=evidence,
            )
            status = PromotionStatus.ROLLED_BACK
            reason = "post_promotion_verification_failed"
        else:
            evidence = (
                f"experiment:{report_hash}",
                f"verification:{verification.fingerprint}",
                f"rollback:{rollback_hash}",
            )
            self.registry.record_promotion(
                candidate.candidate_id,
                promoted_version=promoted_version,
                evidence_refs=evidence,
            )
            status = PromotionStatus.ACTIVE
            reason = "sandbox_evidence_and_post_promotion_verification_passed"

        result = PromotionRecord(
            promotion_id=promotion_id,
            candidate_id=str(candidate.candidate_id),
            proposal_fingerprint=candidate.proposal_fingerprint,
            experiment_report_fingerprint=report_hash,
            baseline_version=active.system_version,
            baseline_config_sha256=baseline_artifact.config_sha256,
            promoted_version=promoted_version,
            promoted_config_sha256=promoted_artifact.config_sha256,
            rollback_artifact_path=str(rollback_path),
            rollback_artifact_sha256=rollback_hash,
            qualification=qualification,
            verification=verification,
            status=status,
            reason=reason,
        )
        (root / "promotion-record.json").write_text(
            result.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return result

    def rollback(
        self,
        record: PromotionRecord,
        output_directory: str | Path,
        *,
        reason: str,
    ) -> PromotionRecord:
        if record.status != PromotionStatus.ACTIVE or record.promoted_version is None:
            raise PromotionError("only an active promotion can be rolled back")
        if self.store.active_config().system_version != record.promoted_version:
            raise PromotionError("active system no longer matches promotion")
        if not record.rollback_artifact_path or not record.rollback_artifact_sha256:
            raise PromotionError("rollback artifact is missing from promotion record")
        rollback_path = Path(record.rollback_artifact_path)
        if hashlib.sha256(rollback_path.read_bytes()).hexdigest() != record.rollback_artifact_sha256:
            raise PromotionError("rollback artifact hash mismatch")
        baseline = ConfigArtifact.model_validate_json(rollback_path.read_text(encoding="utf-8"))
        if baseline.config_sha256 != record.baseline_config_sha256:
            raise PromotionError("rollback artifact does not match baseline")
        registered = self.store.register(HarnessConfig.model_validate(baseline.config))
        self.store.activate(registered)
        verification = self.verifier.verify(
            self.store.active_config(),
            required_tests=("trace_replay",),
            output_directory=Path(output_directory),
        )
        if not verification.passed:
            raise PromotionError("rollback verification failed")
        current = next(
            item for item in self.registry.all() if str(item.candidate_id) == record.candidate_id
        )
        self.registry.invalidate(
            current.candidate_id,
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
