from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from harness_x.benchmarks.runtime import BenchmarkRuntime
from harness_x.config import load_config
from harness_x.core.ids import CandidateId, SystemVersion
from harness_x.improvement import (
    CandidateCreator,
    CandidateRiskLevel,
    CandidateStatus,
    ChangeOperation,
    ChangePatch,
    ImprovementCandidate,
    ImprovementCandidateError,
    ImprovementCandidateRegistry,
    ImprovementChangeType,
    ImprovementHypothesis,
    ImprovementProposal,
    ImprovementResourceBudget,
    InitialImprovementPolicy,
    MetricPrediction,
    RollbackPlan,
)
from harness_x.telemetry import TraceReplayer


def _runtime(tmp_path: Path, name: str = "improvement") -> BenchmarkRuntime:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")
    return BenchmarkRuntime.create(tmp_path / name, config, name=name)


def _patch(change_type: ImprovementChangeType) -> tuple[str, ChangePatch]:
    if change_type == ImprovementChangeType.CONFIG_THRESHOLD:
        return (
            "gates.maintenance.",
            ChangePatch(
                path="gates.maintenance.working_pressure_trigger",
                operation=ChangeOperation.SET,
                before=0.85,
                after=0.90,
            ),
        )
    if change_type == ImprovementChangeType.RETRIEVAL_SCORING_POLICY:
        return (
            "gates.retrieval.",
            ChangePatch(
                path="gates.retrieval.scoring",
                operation=ChangeOperation.REPLACE_POLICY,
                before={"recency": 1.0, "priority": 1.0},
                after={"recency": 0.8, "priority": 1.2},
            ),
        )
    if change_type == ImprovementChangeType.ROUTINE_ORDERING:
        return (
            "routines.task.",
            ChangePatch(
                path="routines.task.step_order",
                operation=ChangeOperation.REORDER,
                before=["retrieve", "focus", "reason"],
                after=["focus", "retrieve", "reason"],
            ),
        )
    if change_type == ImprovementChangeType.CONTEXT_BUILDER_POLICY:
        return (
            "reasoning.context.",
            ChangePatch(
                path="reasoning.context.pressure_policy",
                operation=ChangeOperation.REPLACE_POLICY,
                before={"drop": "retrieval_first"},
                after={"drop": "low_priority_working_first"},
            ),
        )
    if change_type == ImprovementChangeType.VERIFICATION_FREQUENCY:
        return (
            "verification.",
            ChangePatch(
                path="verification.every_n_actions",
                operation=ChangeOperation.SET,
                before=3,
                after=2,
            ),
        )
    if change_type == ImprovementChangeType.MEMORY_RETENTION_COMPACTION:
        return (
            "memory.",
            ChangePatch(
                path="memory.episodic.compaction_policy",
                operation=ChangeOperation.REPLACE_POLICY,
                before={"after_episodes": 100},
                after={"after_episodes": 80},
            ),
        )
    return (
        "unsafe.",
        ChangePatch(
            path="unsafe.change",
            operation=ChangeOperation.REPLACE_POLICY,
            before={"enabled": False},
            after={"enabled": True},
        ),
    )


def _proposal(
    runtime: BenchmarkRuntime,
    *,
    change_type: ImprovementChangeType = ImprovementChangeType.CONFIG_THRESHOLD,
    baseline: SystemVersion | None = None,
    risk: CandidateRiskLevel = CandidateRiskLevel.LOW,
    automatic_rollback: bool = True,
    budget: ImprovementResourceBudget | None = None,
    supersedes: CandidateId | None = None,
) -> ImprovementProposal:
    scope, patch = _patch(change_type)
    baseline = baseline or runtime.recorder.system_version
    return ImprovementProposal(
        created_by=CandidateCreator.SYSTEM,
        creator_id="self-improvement-planner-v1",
        baseline_version=baseline,
        change_type=change_type,
        scope=(scope,),
        patches=(patch,),
        hypothesis=ImprovementHypothesis(
            statement="This bounded change should improve recovery efficiency.",
            mechanism="It changes one explicit control policy while keeping authority external.",
            falsification_condition="The target metric fails to improve across fixed benchmark runs.",
        ),
        predicted_metrics=(
            MetricPrediction(
                metric="recovery_success_rate",
                expected_delta=0.05,
                minimum_acceptable_delta=0.01,
                rationale="The changed policy should reduce avoidable recovery churn.",
            ),
        ),
        required_tests=("benchmark_scripted", "trace_replay", "design_invariants"),
        resource_budget=budget or ImprovementResourceBudget(),
        risk_level=risk,
        rollback=RollbackPlan(
            strategy="restore the exact baseline policy snapshot",
            restore_baseline_version=baseline,
            verification_tests=("trace_replay", "benchmark_scripted"),
            automatic=automatic_rollback,
        ),
        supersedes=supersedes,
    )


@pytest.mark.parametrize(
    "change_type",
    [
        ImprovementChangeType.CONFIG_THRESHOLD,
        ImprovementChangeType.RETRIEVAL_SCORING_POLICY,
        ImprovementChangeType.ROUTINE_ORDERING,
        ImprovementChangeType.CONTEXT_BUILDER_POLICY,
        ImprovementChangeType.VERIFICATION_FREQUENCY,
        ImprovementChangeType.MEMORY_RETENTION_COMPACTION,
    ],
)
def test_initial_policy_allows_only_bounded_first_experiment_classes(
    tmp_path: Path,
    change_type: ImprovementChangeType,
) -> None:
    runtime = _runtime(tmp_path, f"allowed_{change_type.value}")
    proposal = _proposal(runtime, change_type=change_type)
    result = InitialImprovementPolicy().qualify(
        proposal, current_system_version=runtime.recorder.system_version
    )
    assert result.eligible is True
    assert result.reasons == ()


@pytest.mark.parametrize(
    "change_type",
    [
        ImprovementChangeType.TOOL,
        ImprovementChangeType.CODE,
        ImprovementChangeType.ADAPTER,
    ],
)
def test_initial_policy_rejects_unbounded_change_classes(
    tmp_path: Path,
    change_type: ImprovementChangeType,
) -> None:
    runtime = _runtime(tmp_path, f"blocked_{change_type.value}")
    result = InitialImprovementPolicy().qualify(
        _proposal(runtime, change_type=change_type),
        current_system_version=runtime.recorder.system_version,
    )
    assert result.eligible is False
    assert "change_type_not_permitted_in_initial_policy" in result.reasons


def test_candidate_registry_owns_identity_and_only_qualifies_for_sandbox(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    registry = ImprovementCandidateRegistry(runtime.recorder)

    created = registry.create(_proposal(runtime))
    assert created.status == CandidateStatus.PROPOSED
    assert str(created.candidate_id).startswith("candidate_")
    assert created.revision == 1

    qualified = registry.qualify(created.candidate_id)
    assert qualified.status == CandidateStatus.SANDBOX_ELIGIBLE
    assert qualified.revision == 2
    assert qualified.qualification is not None
    assert qualified.qualification.eligible is True
    assert registry.history(created.candidate_id) == (created, qualified)
    assert not hasattr(registry, "promote")
    assert not hasattr(registry, "apply")
    assert not hasattr(registry, "execute")

    events = runtime.recorder.store.events(trace_id=runtime.recorder.trace_id)
    candidate_events = [
        event for event in events if event.component == "improvement.candidates"
    ]
    assert [event.event_type.value for event in candidate_events] == [
        "candidate_created",
        "candidate_evaluated",
    ]
    assert candidate_events[1].metadata["eligible_for_sandbox"] is True
    assert all(
        event.metadata["candidate_kind"] == "system_improvement"
        for event in candidate_events
    )

    replay = TraceReplayer().replay(events)
    assert replay.candidates[str(created.candidate_id)] == "evaluated"


def test_stale_baseline_nonautomatic_rollback_high_risk_and_large_budget_are_rejected(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path, "static_reject")
    stale = SystemVersion(value="old-system")
    proposal = _proposal(
        runtime,
        baseline=stale,
        risk=CandidateRiskLevel.HIGH,
        automatic_rollback=False,
        budget=ImprovementResourceBudget(
            benchmark_runs=21,
            max_wall_time_seconds=3601,
            max_reasoning_steps=10001,
            max_tool_actions=1001,
        ),
    )
    registry = ImprovementCandidateRegistry(runtime.recorder)
    created = registry.create(proposal)
    rejected = registry.qualify(created.candidate_id)

    assert rejected.status == CandidateStatus.REJECTED
    assert rejected.qualification is not None
    assert set(rejected.qualification.reasons) >= {
        "baseline_version_is_stale",
        "automatic_rollback_required",
        "risk_level_exceeds_initial_policy",
        "benchmark_run_budget_exceeded",
        "wall_time_budget_exceeded",
        "reasoning_step_budget_exceeded",
        "tool_action_budget_exceeded",
    }
    with pytest.raises(ImprovementCandidateError):
        registry.qualify(created.candidate_id)


def test_candidate_invalidation_requires_evidence_and_preserves_history(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "invalidate")
    registry = ImprovementCandidateRegistry(runtime.recorder)
    created = registry.create(_proposal(runtime))
    qualified = registry.qualify(created.candidate_id)

    with pytest.raises(ImprovementCandidateError):
        registry.invalidate(
            created.candidate_id,
            reason="new evidence contradicts the proposal",
            evidence_refs=(),
        )

    invalidated = registry.invalidate(
        created.candidate_id,
        reason="new replay evidence contradicts the proposal",
        evidence_refs=("trace:regression-run-17",),
    )
    assert invalidated.status == CandidateStatus.INVALIDATED
    assert invalidated.revision == 3
    assert invalidated.evidence_refs == ("trace:regression-run-17",)
    assert registry.history(created.candidate_id) == (created, qualified, invalidated)

    replay = TraceReplayer().replay(
        runtime.recorder.store.events(trace_id=runtime.recorder.trace_id)
    )
    assert replay.candidates[str(created.candidate_id)] == "invalidated"


def test_replacement_candidate_links_lineage_without_mutating_original(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "lineage")
    registry = ImprovementCandidateRegistry(runtime.recorder)
    original = registry.create(_proposal(runtime))
    original_qualified = registry.qualify(original.candidate_id)

    replacement = registry.create(_proposal(runtime, supersedes=original.candidate_id))
    assert replacement.proposal.supersedes == original.candidate_id
    assert registry.require(original.candidate_id) == original_qualified

    different_type = _proposal(
        runtime,
        change_type=ImprovementChangeType.VERIFICATION_FREQUENCY,
        supersedes=original.candidate_id,
    )
    with pytest.raises(ImprovementCandidateError):
        registry.create(different_type)


def test_candidate_round_trip_preserves_identity_proposal_and_fingerprint(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "roundtrip")
    registry = ImprovementCandidateRegistry(runtime.recorder)
    candidate = registry.create(_proposal(runtime))

    restored = ImprovementCandidate.model_validate_json(candidate.model_dump_json())
    assert restored == candidate
    assert restored.proposal_fingerprint == restored.proposal.fingerprint

    with pytest.raises(ValidationError):
        ImprovementCandidate.model_validate(
            {
                **candidate.model_dump(mode="python"),
                "proposal_fingerprint": "0" * 64,
            }
        )


def test_schema_rejects_source_code_paths_and_non_reorders() -> None:
    with pytest.raises(ValidationError):
        ChangePatch(
            path="src/harness_x/gates.py",
            operation=ChangeOperation.SET,
            before="old",
            after="new",
        )

    with pytest.raises(ValidationError):
        ChangePatch(
            path="routines.task.step_order",
            operation=ChangeOperation.REORDER,
            before=["retrieve", "reason"],
            after=["retrieve", "verify"],
        )


def test_scope_and_operation_must_match_declared_change_class(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "namespace")
    proposal = _proposal(runtime)
    mismatched = proposal.model_copy(
        update={
            "change_type": ImprovementChangeType.ROUTINE_ORDERING,
        }
    )
    # model_copy intentionally bypasses validation; policy must still fail closed.
    result = InitialImprovementPolicy().qualify(
        mismatched, current_system_version=runtime.recorder.system_version
    )
    assert result.eligible is False
    assert "scope_outside_change_type_namespace" in result.reasons


def test_proposal_requires_measurable_hypothesis_tests_and_rollback(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "measurable")
    proposal = _proposal(runtime)
    zero_prediction = proposal.model_copy(
        update={
            "predicted_metrics": (
                MetricPrediction(
                    metric="recovery_success_rate",
                    expected_delta=0.0,
                    rationale="No measurable change predicted.",
                ),
            )
        }
    )
    result = InitialImprovementPolicy().qualify(
        zero_prediction, current_system_version=runtime.recorder.system_version
    )
    assert result.eligible is False
    assert "no_measurable_metric_change_predicted" in result.reasons
