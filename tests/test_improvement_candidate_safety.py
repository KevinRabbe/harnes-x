from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from harness_x.benchmarks.runtime import BenchmarkRuntime
from harness_x.config import load_config
from harness_x.improvement import (
    CandidateCreator,
    CandidateRiskLevel,
    ChangeOperation,
    ChangePatch,
    ImprovementChangeType,
    ImprovementHypothesis,
    ImprovementProposal,
    ImprovementResourceBudget,
    InitialImprovementPolicy,
    MetricPrediction,
    RollbackPlan,
)


def _runtime(tmp_path: Path) -> BenchmarkRuntime:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")
    return BenchmarkRuntime.create(tmp_path / "candidate_safety", config, name="candidate_safety")


def _proposal(runtime: BenchmarkRuntime) -> ImprovementProposal:
    version = runtime.recorder.system_version
    return ImprovementProposal(
        created_by=CandidateCreator.SYSTEM,
        creator_id="candidate-safety-test",
        baseline_version=version,
        change_type=ImprovementChangeType.CONFIG_THRESHOLD,
        scope=("gates.maintenance.",),
        patches=(
            ChangePatch(
                path="gates.maintenance.working_pressure_trigger",
                operation=ChangeOperation.SET,
                before=0.85,
                after=0.90,
            ),
        ),
        hypothesis=ImprovementHypothesis(
            statement="A higher threshold should reduce unnecessary maintenance.",
            mechanism="Maintenance should trigger less often under moderate pressure.",
            falsification_condition="Maintenance count falls without preserving task success.",
        ),
        predicted_metrics=(
            MetricPrediction(
                metric="maintenance_entries",
                expected_delta=-1.0,
                rationale="Fewer unnecessary maintenance entries are expected.",
            ),
        ),
        required_tests=("trace_replay", "design_invariants", "benchmark_scripted"),
        resource_budget=ImprovementResourceBudget(),
        risk_level=CandidateRiskLevel.LOW,
        rollback=RollbackPlan(
            strategy="restore baseline threshold",
            restore_baseline_version=version,
            verification_tests=("trace_replay", "benchmark_scripted"),
            automatic=True,
        ),
    )


def test_candidate_contracts_forbid_unknown_fields(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    proposal = _proposal(runtime)
    payload = proposal.model_dump(mode="python")
    payload["execute_now"] = True
    with pytest.raises(ValidationError):
        ImprovementProposal.model_validate(payload)

    patch = proposal.patches[0].model_dump(mode="python")
    patch["source_code"] = "print('should never execute')"
    with pytest.raises(ValidationError):
        ChangePatch.model_validate(patch)


def test_candidate_policy_requires_core_regression_and_rollback_tests(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    proposal = _proposal(runtime)
    weakened = proposal.model_copy(
        update={
            "required_tests": ("benchmark_scripted",),
            "rollback": proposal.rollback.model_copy(
                update={"verification_tests": ("benchmark_scripted",)}
            ),
        }
    )
    result = InitialImprovementPolicy().qualify(
        weakened,
        current_system_version=runtime.recorder.system_version,
    )
    assert result.eligible is False
    assert set(result.reasons) >= {
        "mandatory_candidate_tests_missing",
        "mandatory_rollback_tests_missing",
    }
