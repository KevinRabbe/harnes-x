from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness_x.config import HarnessConfig
from harness_x.core import ComputeBudget, FixedClock, SystemVersion, TaskId, TraceId
from harness_x.improvement import (
    CandidateCreator,
    CandidateRiskLevel,
    CandidateStatus,
    ChangeOperation,
    ChangePatch,
    ExperimentDisposition,
    ExperimentRunResult,
    ExperimentVariant,
    ImprovementCandidateRegistry,
    ImprovementChangeType,
    ImprovementExperimentSandbox,
    ImprovementHypothesis,
    ImprovementProposal,
    ImprovementResourceBudget,
    MetricPrediction,
    RollbackPlan,
    SandboxExperimentError,
    SandboxSnapshot,
    snapshot_from_config,
)
from harness_x.orchestrator import TaskOrchestrator
from harness_x.telemetry import TraceRecorder, TraceStore


class DeterministicThresholdRunner:
    """Fast trusted benchmark over an actual HarnessConfig snapshot field."""

    def __init__(
        self,
        *,
        candidate_invariant_failure: bool = False,
        baseline_failure: bool = False,
        candidate_extra_tools: int = 0,
        raise_on_candidate: bool = False,
    ) -> None:
        self.candidate_invariant_failure = candidate_invariant_failure
        self.baseline_failure = baseline_failure
        self.candidate_extra_tools = candidate_extra_tools
        self.raise_on_candidate = raise_on_candidate

    @property
    def name(self) -> str:
        return "threshold-probe"

    @property
    def version(self) -> str:
        return "threshold-probe-v1"

    def run(
        self,
        snapshot: SandboxSnapshot,
        *,
        seed: int,
        run_directory: Path,
        budget: ImprovementResourceBudget,
    ) -> ExperimentRunResult:
        if self.raise_on_candidate and snapshot.variant == ExperimentVariant.CANDIDATE:
            raise RuntimeError("intentional sandbox runner failure")

        threshold = float(
            snapshot.state["gates"]["maintenance"]["working_pressure_trigger"]
        )
        pressures = (0.84, 0.86, 0.88, 0.92)
        maintenance_cycles = sum(value >= threshold for value in pressures)
        candidate_failure = (
            self.candidate_invariant_failure
            and snapshot.variant == ExperimentVariant.CANDIDATE
        )
        baseline_failure = self.baseline_failure and snapshot.variant == ExperimentVariant.BASELINE
        passed = not candidate_failure and not baseline_failure
        invariants = {
            "trace_replay": not candidate_failure and not baseline_failure,
            "design_invariants": not candidate_failure and not baseline_failure,
            "baseline_isolation": True,
        }
        payload = {
            "seed": seed,
            "variant": snapshot.variant.value,
            "snapshot": snapshot.fingerprint,
            "threshold": threshold,
            "maintenance_cycles": maintenance_cycles,
        }
        (run_directory / "probe-trace.json").write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
        )
        tool_actions = 1
        if snapshot.variant == ExperimentVariant.CANDIDATE:
            tool_actions += self.candidate_extra_tools
        return ExperimentRunResult(
            suite_name=self.name,
            suite_version=self.version,
            variant=snapshot.variant,
            seed=seed,
            source_system_version=snapshot.source_system_version,
            variant_version=snapshot.variant_version,
            snapshot_fingerprint=snapshot.fingerprint,
            passed=passed,
            metrics={
                "maintenance_cycles": float(maintenance_cycles),
                "safety_score": 1.0 if passed else 0.0,
            },
            invariants=invariants,
            reasoning_steps=1,
            tool_actions=tool_actions,
            wall_time_seconds=0.01,
        )


def _config() -> HarnessConfig:
    return HarnessConfig(system_version=SystemVersion(value="m15-test-v1"))


def _registry(tmp_path: Path, config: HarnessConfig) -> ImprovementCandidateRegistry:
    recorder = TraceRecorder(
        TraceStore(tmp_path / "candidate-trace.jsonl"),
        TraceId(value="trace_m15_candidate"),
        TaskId(value="task_m15_candidate"),
        config.system_version,
        FixedClock(datetime(2026, 8, 19, 22, 0, tzinfo=timezone.utc)),
    )
    TaskOrchestrator.create(
        recorder,
        budget=ComputeBudget(
            max_reasoning_steps=100,
            max_tool_actions=100,
            max_output_tokens=4096,
        ),
    )
    return ImprovementCandidateRegistry(recorder)


def _proposal(
    config: HarnessConfig,
    *,
    after: float = 0.90,
    metric: str = "maintenance_cycles",
    expected_delta: float = -2.0,
    minimum_delta: float | None = -1.0,
    budget: ImprovementResourceBudget | None = None,
) -> ImprovementProposal:
    return ImprovementProposal(
        created_by=CandidateCreator.SYSTEM,
        creator_id="milestone-15-test",
        baseline_version=config.system_version,
        change_type=ImprovementChangeType.CONFIG_THRESHOLD,
        scope=("gates.maintenance",),
        patches=(
            ChangePatch(
                path="gates.maintenance.working_pressure_trigger",
                operation=ChangeOperation.SET,
                before=config.gates.maintenance.working_pressure_trigger,
                after=after,
            ),
        ),
        hypothesis=ImprovementHypothesis(
            statement="Raising maintenance pressure threshold reduces unnecessary maintenance.",
            mechanism="The maintenance gate should fire on fewer moderate-pressure states.",
            falsification_condition="Maintenance cycles do not decrease under matched states.",
        ),
        predicted_metrics=(
            MetricPrediction(
                metric=metric,
                expected_delta=expected_delta,
                minimum_acceptable_delta=minimum_delta,
                rationale="Matched threshold cases should expose the direction directly.",
            ),
        ),
        required_tests=("trace_replay", "design_invariants"),
        resource_budget=budget
        or ImprovementResourceBudget(
            benchmark_runs=3,
            max_wall_time_seconds=10,
            max_reasoning_steps=10,
            max_tool_actions=10,
        ),
        risk_level=CandidateRiskLevel.LOW,
        rollback=RollbackPlan(
            strategy="Restore the exact baseline snapshot.",
            restore_baseline_version=config.system_version,
            verification_tests=("trace_replay", "design_invariants"),
            automatic=True,
        ),
    )


def _qualified(
    tmp_path: Path,
    config: HarnessConfig,
    proposal: ImprovementProposal,
):
    registry = _registry(tmp_path, config)
    created = registry.create(proposal)
    qualified = registry.qualify(created.candidate_id)
    assert qualified.status == CandidateStatus.SANDBOX_ELIGIBLE
    return qualified


def test_matched_sandbox_recommends_promotion_and_preserves_baseline(tmp_path: Path) -> None:
    config = _config()
    original_dump = config.model_dump(mode="json")
    candidate = _qualified(tmp_path, config, _proposal(config))
    baseline = snapshot_from_config(config)
    output = tmp_path / "experiment"

    report = ImprovementExperimentSandbox(
        DeterministicThresholdRunner(), base_seed=9100
    ).run(candidate, baseline, output)

    assert report.experiment_valid is True
    assert report.disposition == ExperimentDisposition.PROMOTION_RECOMMENDED
    assert report.seeds == (9100, 9101, 9102)
    assert report.baseline_untouched is True
    assert report.teardown_verified is True
    assert not (output / "working").exists()
    assert baseline.fingerprint == report.baseline_snapshot_fingerprint
    assert report.candidate_snapshot_fingerprint != report.baseline_snapshot_fingerprint
    assert config.model_dump(mode="json") == original_dump

    metric = next(item for item in report.metric_comparisons if item.metric == "maintenance_cycles")
    assert metric.baseline_mean == 3.0
    assert metric.candidate_mean == 1.0
    assert metric.delta == -2.0
    assert metric.target_met is True
    assert metric.baseline_variance == 0.0
    assert metric.candidate_variance == 0.0

    for variant in ("baseline", "candidate"):
        for index in range(1, 4):
            evidence = output / "evidence" / variant / f"run-{index:02d}"
            assert (evidence / "probe-trace.json").is_file()
            assert (evidence / "run-result.json").is_file()
    persisted = json.loads((output / "experiment-report.json").read_text(encoding="utf-8"))
    assert persisted["disposition"] == "promotion_recommended"


def test_candidate_that_misses_declared_target_is_rejected_not_invalid(tmp_path: Path) -> None:
    config = _config()
    candidate = _qualified(
        tmp_path,
        config,
        _proposal(config, after=0.84, expected_delta=-1.0, minimum_delta=-0.5),
    )
    report = ImprovementExperimentSandbox(DeterministicThresholdRunner()).run(
        candidate, snapshot_from_config(config), tmp_path / "miss"
    )

    assert report.experiment_valid is True
    assert report.disposition == ExperimentDisposition.REJECTION_RECOMMENDED
    assert "target_not_met:maintenance_cycles" in report.reasons


def test_new_candidate_invariant_failure_is_rejection_evidence(tmp_path: Path) -> None:
    config = _config()
    candidate = _qualified(tmp_path, config, _proposal(config))
    report = ImprovementExperimentSandbox(
        DeterministicThresholdRunner(candidate_invariant_failure=True)
    ).run(candidate, snapshot_from_config(config), tmp_path / "invariant")

    assert report.experiment_valid is True
    assert report.disposition == ExperimentDisposition.REJECTION_RECOMMENDED
    assert "trace_replay" in report.new_failure_modes
    assert "new_failure_mode:trace_replay" in report.reasons


def test_broken_baseline_makes_experiment_inconclusive(tmp_path: Path) -> None:
    config = _config()
    candidate = _qualified(tmp_path, config, _proposal(config))
    report = ImprovementExperimentSandbox(
        DeterministicThresholdRunner(baseline_failure=True)
    ).run(candidate, snapshot_from_config(config), tmp_path / "baseline-failure")

    assert report.experiment_valid is False
    assert report.disposition == ExperimentDisposition.INCONCLUSIVE
    assert "baseline_benchmark_failed" in report.reasons
    assert any(reason.startswith("baseline_invariant_failed:") for reason in report.reasons)


def test_unmeasured_declared_target_is_inconclusive(tmp_path: Path) -> None:
    config = _config()
    candidate = _qualified(
        tmp_path,
        config,
        _proposal(config, metric="unmeasured_metric", expected_delta=1.0, minimum_delta=0.5),
    )
    report = ImprovementExperimentSandbox(DeterministicThresholdRunner()).run(
        candidate, snapshot_from_config(config), tmp_path / "unsupported"
    )

    assert report.experiment_valid is False
    assert report.disposition == ExperimentDisposition.INCONCLUSIVE
    assert "unsupported_target_metric:unmeasured_metric" in report.reasons


def test_candidate_only_budget_overrun_is_rejection(tmp_path: Path) -> None:
    config = _config()
    budget = ImprovementResourceBudget(
        benchmark_runs=1,
        max_wall_time_seconds=10,
        max_reasoning_steps=3,
        max_tool_actions=1,
    )
    candidate = _qualified(tmp_path, config, _proposal(config, budget=budget))
    report = ImprovementExperimentSandbox(
        DeterministicThresholdRunner(candidate_extra_tools=1)
    ).run(candidate, snapshot_from_config(config), tmp_path / "candidate-budget")

    assert report.experiment_valid is True
    assert report.disposition == ExperimentDisposition.REJECTION_RECOMMENDED
    assert "candidate_tool_action_budget_exceeded" in report.budget_violations
    assert "candidate_tool_action_budget_exceeded" in report.reasons


def test_baseline_budget_overrun_invalidates_comparison(tmp_path: Path) -> None:
    config = _config()
    budget = ImprovementResourceBudget(
        benchmark_runs=1,
        max_wall_time_seconds=10,
        max_reasoning_steps=0,
        max_tool_actions=3,
    )
    candidate = _qualified(tmp_path, config, _proposal(config, budget=budget))
    report = ImprovementExperimentSandbox(DeterministicThresholdRunner()).run(
        candidate, snapshot_from_config(config), tmp_path / "baseline-budget"
    )

    assert report.experiment_valid is False
    assert report.disposition == ExperimentDisposition.INCONCLUSIVE
    assert "baseline_reasoning_step_budget_exceeded" in report.reasons


def test_sandbox_fails_closed_for_namespace_missing_from_snapshot(tmp_path: Path) -> None:
    config = _config()
    proposal = ImprovementProposal(
        created_by=CandidateCreator.SYSTEM,
        creator_id="milestone-15-test",
        baseline_version=config.system_version,
        change_type=ImprovementChangeType.CONTEXT_BUILDER_POLICY,
        scope=("reasoning.context",),
        patches=(
            ChangePatch(
                path="reasoning.context.max_chars",
                operation=ChangeOperation.SET,
                before=8000,
                after=6000,
            ),
        ),
        hypothesis=ImprovementHypothesis(
            statement="A smaller context budget may improve focus.",
            mechanism="Lower-priority evidence is dropped sooner.",
            falsification_condition="The measured task metric does not improve.",
        ),
        predicted_metrics=(
            MetricPrediction(
                metric="maintenance_cycles",
                expected_delta=-1.0,
                minimum_acceptable_delta=-0.5,
                rationale="test metric",
            ),
        ),
        required_tests=("trace_replay", "design_invariants"),
        resource_budget=ImprovementResourceBudget(
            benchmark_runs=1,
            max_wall_time_seconds=10,
            max_reasoning_steps=10,
            max_tool_actions=10,
        ),
        risk_level=CandidateRiskLevel.LOW,
        rollback=RollbackPlan(
            strategy="Restore baseline context policy.",
            restore_baseline_version=config.system_version,
            verification_tests=("trace_replay",),
            automatic=True,
        ),
    )
    candidate = _qualified(tmp_path, config, proposal)

    with pytest.raises(SandboxExperimentError, match="does not expose"):
        ImprovementExperimentSandbox(DeterministicThresholdRunner()).run(
            candidate, snapshot_from_config(config), tmp_path / "missing-namespace"
        )


def test_sandbox_refuses_noneligible_or_stale_candidate(tmp_path: Path) -> None:
    config = _config()
    registry = _registry(tmp_path, config)
    proposed = registry.create(_proposal(config))
    with pytest.raises(SandboxExperimentError, match="sandbox-eligible"):
        ImprovementExperimentSandbox(DeterministicThresholdRunner()).run(
            proposed, snapshot_from_config(config), tmp_path / "proposed"
        )

    qualified = registry.qualify(proposed.candidate_id)
    stale = SandboxSnapshot(
        source_system_version=SystemVersion(value="different-version"),
        variant_version=SystemVersion(value="different-version"),
        variant=ExperimentVariant.BASELINE,
        state=snapshot_from_config(config).state,
    )
    with pytest.raises(SandboxExperimentError, match="baseline versions differ"):
        ImprovementExperimentSandbox(DeterministicThresholdRunner()).run(
            qualified, stale, tmp_path / "stale"
        )


def test_runner_exception_still_tears_down_disposable_working_tree(tmp_path: Path) -> None:
    config = _config()
    candidate = _qualified(tmp_path, config, _proposal(config))
    output = tmp_path / "runner-failure"
    with pytest.raises(RuntimeError, match="intentional"):
        ImprovementExperimentSandbox(
            DeterministicThresholdRunner(raise_on_candidate=True)
        ).run(candidate, snapshot_from_config(config), output)

    assert not (output / "working").exists()
    assert (output / "baseline-snapshot.json").is_file()


def test_sandbox_has_no_live_promotion_or_commit_surface() -> None:
    assert not hasattr(ImprovementExperimentSandbox, "promote")
    assert not hasattr(ImprovementExperimentSandbox, "apply_live")
    assert not hasattr(ImprovementExperimentSandbox, "commit")
