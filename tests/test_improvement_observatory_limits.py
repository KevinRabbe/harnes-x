from __future__ import annotations

from pathlib import Path

from harness_x.app_server.improvement_observatory import (
    ObservatorySourceStatus,
    build_improvement_observatory,
)
from harness_x.core.ids import CandidateId, SystemVersion
from harness_x.improvement.experiment import (
    ExperimentDisposition,
    ExperimentRunResult,
    ExperimentVariant,
    MetricComparison,
    ResourceComparison,
    SandboxExperimentReport,
)
from harness_x.improvement.promotion import (
    ActiveConfigPointer,
    PromotionQualification,
    PromotionRecord,
    PromotionStatus,
)


def _write(path: Path, model) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")


def test_experiment_projection_surfaces_regressions_failure_modes_and_budget_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    baseline_version = SystemVersion(value="0.1.0-alpha.0")
    candidate_version = SystemVersion(value="0.1.0-alpha.0+sandbox.fixture")
    baseline_run = ExperimentRunResult(
        suite_name="fixture-suite",
        suite_version="v1",
        variant=ExperimentVariant.BASELINE,
        seed=15000,
        source_system_version=baseline_version,
        variant_version=baseline_version,
        snapshot_fingerprint="a" * 64,
        passed=True,
        metrics={"score": 1.0},
        invariants={"guard": True},
    )
    candidate_run = ExperimentRunResult(
        suite_name="fixture-suite",
        suite_version="v1",
        variant=ExperimentVariant.CANDIDATE,
        seed=15000,
        source_system_version=baseline_version,
        variant_version=candidate_version,
        snapshot_fingerprint="b" * 64,
        passed=False,
        metrics={"score": 0.5},
        invariants={"guard": False},
        tool_actions=3,
    )
    report = SandboxExperimentReport(
        candidate_id=CandidateId(value="candidate_fixture"),
        proposal_fingerprint="c" * 64,
        baseline_version=baseline_version,
        baseline_snapshot_fingerprint="a" * 64,
        candidate_snapshot_fingerprint="b" * 64,
        suite_name="fixture-suite",
        suite_version="v1",
        seeds=(15000,),
        baseline_runs=(baseline_run,),
        candidate_runs=(candidate_run,),
        metric_comparisons=(
            MetricComparison(
                metric="score",
                baseline_mean=1.0,
                candidate_mean=0.5,
                delta=-0.5,
                baseline_variance=0.0,
                candidate_variance=0.0,
                expected_delta=0.2,
                minimum_acceptable_delta=0.1,
                target_met=False,
            ),
        ),
        resource_comparison=ResourceComparison(
            baseline_reasoning_steps=0,
            candidate_reasoning_steps=0,
            reasoning_step_delta=0,
            baseline_tool_actions=0,
            candidate_tool_actions=3,
            tool_action_delta=3,
            baseline_wall_time_seconds=0.0,
            candidate_wall_time_seconds=0.0,
            wall_time_delta_seconds=0.0,
        ),
        new_failure_modes=("guard",),
        regressions=("target_not_met:score", "new_failure_mode:guard"),
        budget_violations=("candidate_tool_action_budget_exceeded",),
        baseline_untouched=True,
        teardown_verified=True,
        experiment_valid=True,
        disposition=ExperimentDisposition.REJECTION_RECOMMENDED,
        reasons=("target_not_met:score",),
        evidence_directory="not-publicly-projected",
    )
    _write(workspace / ".harness-x" / "experiments" / "fixture" / "experiment-report.json", report)

    projection = build_improvement_observatory(project_id="project_fixture", workspace_root=workspace)
    assert len(projection.experiments) == 1
    observed = projection.experiments[0]
    assert observed.candidate_id == "candidate_fixture"
    assert observed.disposition == "rejection_recommended"
    assert observed.regressions == ("target_not_met:score", "new_failure_mode:guard")
    assert observed.new_failure_modes == ("guard",)
    assert observed.budget_violations == ("candidate_tool_action_budget_exceeded",)
    assert "not-publicly-projected" not in projection.model_dump_json()


def test_conflicting_duplicate_promotion_identity_is_fail_visible(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    version = SystemVersion(value="0.1.0-alpha.0")

    def denied(reason: str, fingerprint: str) -> PromotionRecord:
        return PromotionRecord(
            promotion_id="promotion_duplicate",
            candidate_id="candidate_fixture",
            proposal_fingerprint=fingerprint * 64,
            experiment_report_fingerprint="e" * 64,
            baseline_version=version,
            baseline_config_sha256="b" * 64,
            qualification=PromotionQualification(
                allowed=False,
                reasons=(reason,),
                policy_version="fixture-policy-v1",
            ),
            status=PromotionStatus.DENIED,
            reason=reason,
        )

    _write(workspace / ".harness-x" / "one" / "promotion-record.json", denied("first_denial", "1"))
    _write(workspace / ".harness-x" / "two" / "promotion-record.json", denied("second_denial", "2"))
    projection = build_improvement_observatory(project_id="project_fixture", workspace_root=workspace)

    assert len(projection.promotions) == 1
    conflicts = [
        item
        for item in projection.sources
        if item.status == ObservatorySourceStatus.DUPLICATE_CONFLICT
    ]
    assert len(conflicts) == 1
    assert conflicts[0].detail == "promotion:promotion_duplicate"


def test_scan_is_bounded_by_allowlisted_record_count(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".harness-x"
    for index in range(97):
        pointer = ActiveConfigPointer(
            system_version=SystemVersion(value=f"0.1.0-alpha.0+observed.{index}"),
            config_sha256=f"{index:064x}"[-64:],
            artifact_path=f"versions/{index}.json",
        )
        _write(root / f"run-{index:03d}" / "active-config.json", pointer)

    projection = build_improvement_observatory(project_id="project_fixture", workspace_root=workspace)
    assert projection.scan_truncated is True
    observed_pointers = [
        item
        for item in projection.sources
        if item.record_kind == "ActiveConfigPointer"
        and item.status == ObservatorySourceStatus.OBSERVED
    ]
    assert len(observed_pointers) == 96
    assert any(
        item.status == ObservatorySourceStatus.SCAN_TRUNCATED
        and item.detail == "record-count limit reached"
        for item in projection.sources
    )
    assert len(projection.versions) == 48
