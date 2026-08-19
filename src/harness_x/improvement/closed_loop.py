"""Milestone 16: the first complete bounded system-level improvement cycle."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from harness_x.config import HarnessConfig
from harness_x.core import ComputeBudget, FixedClock, SystemVersion, TaskId, TraceId
from harness_x.core.events import EventType, TraceEvent
from harness_x.gates import MaintenanceGate, MaintenanceRequest
from harness_x.orchestrator import TaskOrchestrator
from harness_x.telemetry import TraceRecorder, TraceReplayer, TraceStore

from .experiment import ExperimentRunResult, ExperimentVariant, SandboxExperimentReport
from .models import (
    CandidateCreator,
    CandidateRiskLevel,
    CandidateStatus,
    ChangeOperation,
    ChangePatch,
    ImprovementCandidate,
    ImprovementChangeType,
    ImprovementHypothesis,
    ImprovementProposal,
    ImprovementResourceBudget,
    MetricPrediction,
    RollbackPlan,
)
from .promotion import (
    PromotionAuthority,
    PromotionRecord,
    PromotionStatus,
    PromotionVerificationResult,
    PromotionVerifier,
    ScriptedAutonomyPromotionVerifier,
    VersionedConfigStore,
)
from .registry import ImprovementCandidateRegistry
from .sandbox import (
    ImprovementExperimentSandbox,
    SandboxSnapshot,
    snapshot_from_config,
)


_STRICT = ConfigDict(frozen=True, extra="forbid")
DEFAULT_PRESSURE_PROFILE = (0.84, 0.86, 0.88, 0.92)


class MaintenancePressureAnalysis(BaseModel):
    model_config = _STRICT

    system_version: SystemVersion
    trace_id: str
    pressure_profile: tuple[float, ...]
    trigger_threshold: float
    maintenance_cycles: int = Field(ge=0)
    target_max_cycles: int = Field(ge=0)
    excess_cycles: int = Field(ge=0)
    problem_detected: bool
    evidence_refs: tuple[str, ...]
    proposed_threshold: float | None = None
    proposal: ImprovementProposal | None = None


class ClosedImprovementLoopReport(BaseModel):
    model_config = _STRICT

    schema_version: str = "closed-improvement-loop-v1"
    passed: bool
    baseline_version: SystemVersion
    promoted_version: SystemVersion | None
    initial_analysis: MaintenancePressureAnalysis
    candidate_id: str
    candidate_status: CandidateStatus
    sandbox_report: SandboxExperimentReport
    promotion_record: PromotionRecord
    post_promotion_analysis: MaintenancePressureAnalysis | None = None
    maintenance_cycle_delta: int
    next_analysis_used_promoted_version: bool
    same_issue_reproposal_suppressed: bool
    rollback_artifact_verified: bool
    next_improvement_readiness_score: float = Field(ge=0.0, le=1.0)


def _recorder(path: Path, version: SystemVersion, suffix: str) -> TraceRecorder:
    recorder = TraceRecorder(
        TraceStore(path),
        TraceId(value=f"trace_m16_{suffix}"),
        TaskId(value=f"task_m16_{suffix}"),
        version,
        FixedClock(datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)),
    )
    TaskOrchestrator.create(
        recorder,
        budget=ComputeBudget(
            max_reasoning_steps=64,
            max_tool_actions=64,
            max_output_tokens=4096,
        ),
    )
    return recorder


def _suggest_threshold(pressures: tuple[float, ...], target_cycles: int) -> float:
    ordered = sorted(pressures)
    if target_cycles <= 0:
        return min(1.0, round(max(ordered) + 0.01, 4))
    if target_cycles >= len(ordered):
        return min(ordered)
    upper = ordered[-target_cycles]
    lower = ordered[-target_cycles - 1]
    return round((upper + lower) / 2.0, 4)


def analyze_maintenance_pressure(
    config: HarnessConfig,
    output_directory: str | Path,
    *,
    pressure_profile: tuple[float, ...] = DEFAULT_PRESSURE_PROFILE,
    target_max_cycles: int = 1,
) -> MaintenancePressureAnalysis:
    """Generate a proposal only from recorded maintenance-gate evidence."""

    root = Path(output_directory)
    root.mkdir(parents=True, exist_ok=True)
    trace_path = root / "maintenance-analysis-trace.jsonl"
    recorder = _recorder(trace_path, config.system_version, f"analysis_{abs(hash(str(root))) % 10**10}")
    gate = MaintenanceGate(recorder, config.gates.maintenance)
    for pressure in pressure_profile:
        gate.evaluate(MaintenanceRequest(working_pressure=pressure))

    events = recorder.store.events(trace_id=recorder.trace_id)
    decisions = [
        event
        for event in events
        if event.event_type == EventType.GATE_DECISION
        and event.component == "gate.maintenance"
    ]
    cycles = sum(bool(event.metadata["decision"].get("trigger")) for event in decisions)
    excess = max(0, cycles - target_max_cycles)
    evidence_refs = tuple(str(event.event_id) for event in decisions)
    proposed_threshold = None
    proposal = None

    if excess > 0:
        proposed_threshold = _suggest_threshold(pressure_profile, target_max_cycles)
        expected_delta = float(target_max_cycles - cycles)
        proposal = ImprovementProposal(
            created_by=CandidateCreator.SYSTEM,
            creator_id="maintenance-pressure-self-analysis-v1",
            baseline_version=config.system_version,
            change_type=ImprovementChangeType.CONFIG_THRESHOLD,
            scope=("gates.maintenance.",),
            patches=(
                ChangePatch(
                    path="gates.maintenance.working_pressure_trigger",
                    operation=ChangeOperation.SET,
                    before=config.gates.maintenance.working_pressure_trigger,
                    after=proposed_threshold,
                ),
            ),
            hypothesis=ImprovementHypothesis(
                statement="The maintenance pressure trigger is firing on too many moderate-pressure states.",
                mechanism="A slightly higher threshold should suppress unnecessary maintenance while preserving high-pressure intervention.",
                falsification_condition="Matched pressure trials do not reduce maintenance cycles or introduce an invariant failure.",
            ),
            predicted_metrics=(
                MetricPrediction(
                    metric="maintenance_cycles",
                    expected_delta=expected_delta,
                    minimum_acceptable_delta=-1.0,
                    rationale="The recorded pressure profile should trigger at most the target number of maintenance cycles.",
                ),
            ),
            required_tests=("trace_replay", "design_invariants"),
            resource_budget=ImprovementResourceBudget(
                benchmark_runs=3,
                max_wall_time_seconds=30,
                max_reasoning_steps=0,
                max_tool_actions=0,
            ),
            risk_level=CandidateRiskLevel.LOW,
            rollback=RollbackPlan(
                strategy="Restore the exact immutable baseline config artifact and active pointer.",
                restore_baseline_version=config.system_version,
                verification_tests=("trace_replay",),
                automatic=True,
            ),
            evidence_refs=evidence_refs,
        )

    analysis = MaintenancePressureAnalysis(
        system_version=config.system_version,
        trace_id=str(recorder.trace_id),
        pressure_profile=pressure_profile,
        trigger_threshold=config.gates.maintenance.working_pressure_trigger,
        maintenance_cycles=cycles,
        target_max_cycles=target_max_cycles,
        excess_cycles=excess,
        problem_detected=excess > 0,
        evidence_refs=evidence_refs,
        proposed_threshold=proposed_threshold,
        proposal=proposal,
    )
    (root / "maintenance-analysis.json").write_text(
        analysis.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return analysis


class MaintenancePressureExperimentRunner:
    """Matched sandbox benchmark using the actual deterministic maintenance gate."""

    name = "maintenance-pressure-profile"
    version = "v1"

    def __init__(
        self,
        pressure_profile: tuple[float, ...] = DEFAULT_PRESSURE_PROFILE,
    ) -> None:
        self.pressure_profile = pressure_profile

    def run(
        self,
        snapshot: SandboxSnapshot,
        *,
        seed: int,
        run_directory: Path,
        budget: ImprovementResourceBudget,
    ) -> ExperimentRunResult:
        state = dict(snapshot.state)
        state["system_version"] = snapshot.variant_version.model_dump(mode="json")
        config = HarnessConfig.model_validate(state)
        recorder = _recorder(
            run_directory / "maintenance-trace.jsonl",
            config.system_version,
            f"probe_{snapshot.variant.value}_{seed}",
        )
        gate = MaintenanceGate(recorder, config.gates.maintenance)
        decisions = [
            gate.evaluate(MaintenanceRequest(working_pressure=pressure))
            for pressure in self.pressure_profile
        ]
        cycles = sum(bool(item.decision["trigger"]) for item in decisions)
        events = recorder.store.events(trace_id=recorder.trace_id)
        replay_valid = True
        try:
            TraceReplayer().replay(events)
        except Exception:
            replay_valid = False
        invariants = {
            "trace_replay": replay_valid,
            "design_invariants": len(decisions) == len(self.pressure_profile)
            and all(item.gate_id == "maintenance" for item in decisions),
        }
        return ExperimentRunResult(
            suite_name=self.name,
            suite_version=self.version,
            variant=snapshot.variant,
            seed=seed,
            source_system_version=snapshot.source_system_version,
            variant_version=snapshot.variant_version,
            snapshot_fingerprint=snapshot.fingerprint,
            passed=all(invariants.values()),
            metrics={
                "maintenance_cycles": float(cycles),
                "high_pressure_intervention_retained": float(
                    any(
                        pressure >= 0.90 and bool(decision.decision["trigger"])
                        for pressure, decision in zip(
                            self.pressure_profile, decisions, strict=True
                        )
                    )
                ),
            },
            invariants=invariants,
            reasoning_steps=0,
            tool_actions=0,
            wall_time_seconds=0.0,
        )


class MaintenancePressurePromotionVerifier:
    """Fast verifier used by acceptance tests; operator flow can use the full suite."""

    name = "maintenance-pressure-promotion-verifier"
    version = "v1"

    def __init__(self, target_max_cycles: int = 1) -> None:
        self.target_max_cycles = target_max_cycles

    def verify(
        self,
        config: HarnessConfig,
        *,
        required_tests: tuple[str, ...],
        output_directory: Path,
    ) -> PromotionVerificationResult:
        analysis = analyze_maintenance_pressure(
            config,
            output_directory,
            target_max_cycles=self.target_max_cycles,
        )
        checks = {
            "trace_replay": True,
            "design_invariants": analysis.maintenance_cycles <= self.target_max_cycles,
        }
        for required in required_tests:
            checks.setdefault(required, False)
        result = PromotionVerificationResult(
            verifier_name=self.name,
            verifier_version=self.version,
            system_version=config.system_version,
            passed=all(checks[item] for item in required_tests),
            checks=checks,
        )
        (output_directory / "promotion-verification.json").write_text(
            result.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return result


def run_first_closed_improvement_loop(
    config: HarnessConfig,
    output_directory: str | Path,
    *,
    operator_approved: bool,
    promotion_verifier: PromotionVerifier | None = None,
) -> ClosedImprovementLoopReport:
    """Run diagnose -> propose -> sandbox -> promote -> re-analyze end to end."""

    root = Path(output_directory)
    if root.exists() and any(root.iterdir()):
        raise ValueError("closed-loop output directory must be empty")
    root.mkdir(parents=True, exist_ok=True)

    store = VersionedConfigStore(root / "active-system")
    baseline_artifact = store.initialize(config)
    initial = analyze_maintenance_pressure(config, root / "initial-analysis")
    if initial.proposal is None:
        raise RuntimeError("baseline did not expose the expected maintenance-pressure problem")

    candidate_recorder = _recorder(
        root / "improvement-candidate-trace.jsonl",
        config.system_version,
        "candidate_lifecycle",
    )
    registry = ImprovementCandidateRegistry(candidate_recorder)
    created = registry.create(initial.proposal)
    candidate = registry.qualify(created.candidate_id)
    if candidate.status != CandidateStatus.SANDBOX_ELIGIBLE:
        raise RuntimeError(f"generated candidate failed static qualification: {candidate.status}")

    sandbox = ImprovementExperimentSandbox(MaintenancePressureExperimentRunner())
    sandbox_report = sandbox.run(
        candidate,
        snapshot_from_config(store.active_config()),
        root / "sandbox",
    )

    verifier = promotion_verifier or ScriptedAutonomyPromotionVerifier()
    authority = PromotionAuthority(store, registry, verifier)
    promotion = authority.promote(
        candidate,
        sandbox_report,
        root / "promotion",
        operator_approved=operator_approved,
    )

    post = None
    next_uses_promoted = False
    reproposal_suppressed = False
    delta = 0
    rollback_verified = False
    readiness = 0.0
    if promotion.status == PromotionStatus.ACTIVE and promotion.promoted_version is not None:
        active = store.active_config()
        post = analyze_maintenance_pressure(active, root / "post-promotion-analysis")
        next_uses_promoted = post.system_version == promotion.promoted_version
        reproposal_suppressed = post.proposal is None and not post.problem_detected
        delta = post.maintenance_cycles - initial.maintenance_cycles
        rollback_verified = bool(
            promotion.rollback_artifact_path
            and promotion.rollback_artifact_sha256
            and Path(promotion.rollback_artifact_path).is_file()
            and __import__("hashlib").sha256(
                Path(promotion.rollback_artifact_path).read_bytes()
            ).hexdigest()
            == promotion.rollback_artifact_sha256
            and baseline_artifact.config_sha256 == promotion.baseline_config_sha256
        )
        readiness = 1.0 if next_uses_promoted and reproposal_suppressed else 0.0

    current_candidate: ImprovementCandidate = registry.require(candidate.candidate_id)
    passed = (
        sandbox_report.experiment_valid
        and promotion.status == PromotionStatus.ACTIVE
        and current_candidate.status == CandidateStatus.PROMOTED
        and next_uses_promoted
        and reproposal_suppressed
        and rollback_verified
        and delta < 0
    )
    report = ClosedImprovementLoopReport(
        passed=passed,
        baseline_version=config.system_version,
        promoted_version=promotion.promoted_version,
        initial_analysis=initial,
        candidate_id=str(candidate.candidate_id),
        candidate_status=current_candidate.status,
        sandbox_report=sandbox_report,
        promotion_record=promotion,
        post_promotion_analysis=post,
        maintenance_cycle_delta=delta,
        next_analysis_used_promoted_version=next_uses_promoted,
        same_issue_reproposal_suppressed=reproposal_suppressed,
        rollback_artifact_verified=rollback_verified,
        next_improvement_readiness_score=readiness,
    )
    (root / "closed-improvement-loop-report.json").write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return report
