from __future__ import annotations

from pathlib import Path

from harness_x.config import HarnessConfig
from harness_x.core import SystemVersion
from harness_x.improvement import (
    CandidateStatus,
    MaintenancePressurePromotionVerifier,
    PromotionStatus,
    PromotionVerificationResult,
    VersionedConfigStore,
    run_first_closed_improvement_loop,
)
from harness_x.telemetry import TraceReplayer, TraceStore


class FailingPromotionVerifier:
    name = "failing-promotion-verifier"
    version = "v1"

    def verify(
        self,
        config: HarnessConfig,
        *,
        required_tests: tuple[str, ...],
        output_directory: Path,
    ) -> PromotionVerificationResult:
        output_directory.mkdir(parents=True, exist_ok=True)
        checks = {item: False for item in required_tests}
        result = PromotionVerificationResult(
            verifier_name=self.name,
            verifier_version=self.version,
            system_version=config.system_version,
            passed=False,
            checks=checks,
            notes=("intentional Milestone 16 rollback fixture",),
        )
        (output_directory / "promotion-verification.json").write_text(
            result.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return result


def _config(*, auto: bool = False) -> HarnessConfig:
    config = HarnessConfig(system_version=SystemVersion(value="m16-test-v1"))
    return config.model_copy(
        update={
            "improvement": config.improvement.model_copy(
                update={
                    "promotion": config.improvement.promotion.model_copy(
                        update={"allow_auto_promotion": auto}
                    )
                }
            )
        }
    )


def test_first_closed_loop_promotes_bounded_threshold_and_future_analysis_uses_it(
    tmp_path: Path,
) -> None:
    output = tmp_path / "closed-loop"
    report = run_first_closed_improvement_loop(
        _config(),
        output,
        operator_approved=True,
        promotion_verifier=MaintenancePressurePromotionVerifier(),
    )

    assert report.passed is True
    assert report.initial_analysis.maintenance_cycles == 3
    assert report.initial_analysis.problem_detected is True
    assert report.initial_analysis.proposed_threshold == 0.90
    assert report.initial_analysis.proposal is not None
    assert report.initial_analysis.proposal.evidence_refs == report.initial_analysis.evidence_refs
    assert report.sandbox_report.experiment_valid is True
    assert report.sandbox_report.disposition == "promotion_recommended"
    metric = next(
        item
        for item in report.sandbox_report.metric_comparisons
        if item.metric == "maintenance_cycles"
    )
    assert metric.baseline_mean == 3.0
    assert metric.candidate_mean == 1.0
    assert metric.delta == -2.0
    assert metric.target_met is True

    assert report.promotion_record.status == PromotionStatus.ACTIVE
    assert report.candidate_status == CandidateStatus.PROMOTED
    assert report.promoted_version is not None
    assert report.promoted_version != report.baseline_version
    assert report.rollback_artifact_verified is True
    assert report.post_promotion_analysis is not None
    assert report.post_promotion_analysis.maintenance_cycles == 1
    assert report.post_promotion_analysis.problem_detected is False
    assert report.post_promotion_analysis.proposal is None
    assert report.maintenance_cycle_delta == -2
    assert report.next_analysis_used_promoted_version is True
    assert report.same_issue_reproposal_suppressed is True
    assert report.next_improvement_readiness_score == 1.0

    store = VersionedConfigStore(output / "active-system")
    active = store.active_config()
    assert active.system_version == report.promoted_version
    assert active.gates.maintenance.working_pressure_trigger == 0.90
    assert len(tuple((output / "active-system" / "versions").glob("*.json"))) == 2

    lifecycle = TraceStore(output / "improvement-candidate-trace.jsonl").events()
    replay = TraceReplayer().replay(lifecycle)
    assert replay.candidates[report.candidate_id] == "promoted"


def test_live_promotion_requires_operator_approval_by_default(tmp_path: Path) -> None:
    output = tmp_path / "approval-required"
    report = run_first_closed_improvement_loop(
        _config(),
        output,
        operator_approved=False,
        promotion_verifier=MaintenancePressurePromotionVerifier(),
    )

    assert report.passed is False
    assert report.promotion_record.status == PromotionStatus.DENIED
    assert "operator_approval_required" in report.promotion_record.qualification.reasons
    store = VersionedConfigStore(output / "active-system")
    assert store.active_config().system_version == SystemVersion(value="m16-test-v1")
    assert store.active_config().gates.maintenance.working_pressure_trigger == 0.85


def test_configured_low_risk_auto_promotion_needs_no_operator_flag(tmp_path: Path) -> None:
    report = run_first_closed_improvement_loop(
        _config(auto=True),
        tmp_path / "auto",
        operator_approved=False,
        promotion_verifier=MaintenancePressurePromotionVerifier(),
    )
    assert report.passed is True
    assert report.promotion_record.status == PromotionStatus.ACTIVE


def test_failed_post_promotion_verification_automatically_restores_exact_baseline(
    tmp_path: Path,
) -> None:
    output = tmp_path / "verify-failure"
    report = run_first_closed_improvement_loop(
        _config(),
        output,
        operator_approved=True,
        promotion_verifier=FailingPromotionVerifier(),
    )

    assert report.passed is False
    assert report.promotion_record.status == PromotionStatus.ROLLED_BACK
    assert report.candidate_status == CandidateStatus.INVALIDATED
    store = VersionedConfigStore(output / "active-system")
    active = store.active_config()
    assert active.system_version == SystemVersion(value="m16-test-v1")
    assert active.gates.maintenance.working_pressure_trigger == 0.85
    assert report.promotion_record.rollback_artifact_path is not None
    assert Path(report.promotion_record.rollback_artifact_path).is_file()

    lifecycle = TraceStore(output / "improvement-candidate-trace.jsonl").events()
    replay = TraceReplayer().replay(lifecycle)
    assert replay.candidates[report.candidate_id] == "invalidated"


def test_active_config_store_detects_pointer_artifact_tampering(tmp_path: Path) -> None:
    store = VersionedConfigStore(tmp_path / "store")
    artifact = store.initialize(_config())
    path = store.path_for(artifact)
    path.write_text(path.read_text(encoding="utf-8").replace("0.85", "0.84", 1), encoding="utf-8")

    try:
        store.active_config()
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("tampered config artifact was accepted")
