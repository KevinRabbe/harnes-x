"""Isolated baseline/candidate experiment sandbox for bounded improvements.

Milestone 15 applies declarative candidate patches only to immutable snapshots. Trusted
benchmark runners receive those snapshots and isolated output directories. The sandbox
produces empirical evidence and a promotion/rejection recommendation, but it has no
live-system promotion API.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import statistics
import time
from copy import deepcopy
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from harness_x.benchmarks import run_scripted_autonomy_benchmark
from harness_x.config import HarnessConfig
from harness_x.core.events import EventType
from harness_x.core.ids import SystemVersion
from harness_x.telemetry import TraceStore

from .experiment import (
    ArtifactDigest,
    ExperimentDisposition,
    ExperimentRunResult,
    ExperimentVariant,
    MetricComparison,
    ResourceComparison,
    SandboxExperimentReport,
)
from .models import (
    CandidateStatus,
    ChangeOperation,
    ImprovementCandidate,
    ImprovementResourceBudget,
    MetricPrediction,
    canonical_json,
)


_STRICT_FROZEN = ConfigDict(frozen=True, extra="forbid")


class SandboxExperimentError(RuntimeError):
    """Raised when an experiment cannot be run safely or deterministically."""


class SandboxSnapshot(BaseModel):
    """Immutable JSON snapshot supplied to a trusted benchmark runner."""

    model_config = _STRICT_FROZEN

    schema_version: str = "improvement-sandbox-snapshot-v1"
    source_system_version: SystemVersion
    variant_version: SystemVersion
    variant: ExperimentVariant
    state: dict[str, JsonValue]

    @property
    def fingerprint(self) -> str:
        payload = {
            "source_system_version": self.source_system_version.model_dump(mode="json"),
            "variant_version": self.variant_version.model_dump(mode="json"),
            "variant": self.variant.value,
            "state": self.state,
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class SandboxExperimentPlan(BaseModel):
    model_config = _STRICT_FROZEN

    schema_version: str = "improvement-sandbox-plan-v1"
    candidate_id: str
    proposal_fingerprint: str = Field(min_length=64, max_length=64)
    baseline_snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    candidate_snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    suite_name: str
    suite_version: str
    seeds: tuple[int, ...] = Field(min_length=1)
    resource_budget: ImprovementResourceBudget


class SandboxBenchmarkRunner(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def run(
        self,
        snapshot: SandboxSnapshot,
        *,
        seed: int,
        run_directory: Path,
        budget: ImprovementResourceBudget,
    ) -> ExperimentRunResult: ...


def snapshot_from_config(config: HarnessConfig) -> SandboxSnapshot:
    return SandboxSnapshot(
        source_system_version=config.system_version,
        variant_version=config.system_version,
        variant=ExperimentVariant.BASELINE,
        state=config.model_dump(mode="json"),
    )


def _path_parts(path: str) -> tuple[str, ...]:
    parts = tuple(part for part in path.split(".") if part)
    if parts and parts[0] == "config":
        parts = parts[1:]
    if not parts:
        raise SandboxExperimentError("candidate patch path resolved to an empty namespace")
    return parts


def _apply_candidate_snapshot(
    baseline: SandboxSnapshot,
    candidate: ImprovementCandidate,
) -> SandboxSnapshot:
    if candidate.status != CandidateStatus.SANDBOX_ELIGIBLE:
        raise SandboxExperimentError("candidate is not sandbox eligible")
    if candidate.qualification is None or not candidate.qualification.eligible:
        raise SandboxExperimentError("candidate lacks positive static qualification")
    if baseline.source_system_version != candidate.proposal.baseline_version:
        raise SandboxExperimentError("sandbox snapshot does not match candidate baseline version")

    patch_paths = [patch.path for patch in candidate.proposal.patches]
    if len(patch_paths) != len(set(patch_paths)):
        raise SandboxExperimentError("duplicate candidate patch paths are order-dependent")

    state = deepcopy(baseline.state)
    for patch in candidate.proposal.patches:
        parts = _path_parts(patch.path)
        current: object = state
        for part in parts[:-1]:
            if not isinstance(current, dict) or part not in current:
                raise SandboxExperimentError(
                    f"snapshot does not expose candidate namespace {patch.path!r}"
                )
            current = current[part]
        leaf = parts[-1]
        if not isinstance(current, dict) or leaf not in current:
            raise SandboxExperimentError(
                f"snapshot does not expose candidate patch target {patch.path!r}"
            )
        actual_before = current[leaf]
        if canonical_json(actual_before) != canonical_json(patch.before):
            raise SandboxExperimentError(
                f"candidate patch baseline mismatch at {patch.path!r}"
            )
        if patch.operation == ChangeOperation.REORDER:
            if not isinstance(actual_before, list) or not isinstance(patch.after, list):
                raise SandboxExperimentError("reorder patch target is not a JSON list")
        current[leaf] = deepcopy(patch.after)

    short_id = str(candidate.candidate_id).replace("candidate_", "")[:12]
    variant_version = SystemVersion(
        value=f"{baseline.source_system_version.value}+sandbox.{short_id}"
    )
    return SandboxSnapshot(
        source_system_version=baseline.source_system_version,
        variant_version=variant_version,
        variant=ExperimentVariant.CANDIDATE,
        state=state,
    )


def _artifact_digests(root: Path) -> tuple[ArtifactDigest, ...]:
    artifacts: list[ArtifactDigest] = []
    if not root.exists():
        return ()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        data = path.read_bytes()
        artifacts.append(
            ArtifactDigest(
                relative_path=path.relative_to(root).as_posix(),
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
            )
        )
    return tuple(artifacts)


def _mean(values: list[float]) -> float:
    return float(statistics.fmean(values))


def _variance(values: list[float]) -> float:
    return float(statistics.pvariance(values)) if len(values) > 1 else 0.0


def _target_met(prediction: MetricPrediction, actual_delta: float) -> bool:
    threshold = prediction.minimum_acceptable_delta
    if prediction.expected_delta > 0.0:
        required = 0.0 if threshold is None else threshold
        return actual_delta > 0.0 and actual_delta >= required
    if prediction.expected_delta < 0.0:
        required = 0.0 if threshold is None else threshold
        return actual_delta < 0.0 and actual_delta <= required
    if threshold is None:
        return True
    return actual_delta >= threshold


def _budget_violations(
    prefix: str,
    runs: tuple[ExperimentRunResult, ...],
    budget: ImprovementResourceBudget,
) -> tuple[str, ...]:
    reasoning = sum(item.reasoning_steps for item in runs)
    tools = sum(item.tool_actions for item in runs)
    wall = sum(item.wall_time_seconds for item in runs)
    violations: list[str] = []
    if reasoning > budget.max_reasoning_steps:
        violations.append(f"{prefix}_reasoning_step_budget_exceeded")
    if tools > budget.max_tool_actions:
        violations.append(f"{prefix}_tool_action_budget_exceeded")
    if wall > budget.max_wall_time_seconds:
        violations.append(f"{prefix}_wall_time_budget_exceeded")
    return tuple(violations)


def _compare(
    candidate: ImprovementCandidate,
    baseline_snapshot: SandboxSnapshot,
    candidate_snapshot: SandboxSnapshot,
    baseline_runs: tuple[ExperimentRunResult, ...],
    candidate_runs: tuple[ExperimentRunResult, ...],
    *,
    teardown_verified: bool,
    baseline_untouched: bool,
    evidence_directory: Path,
) -> SandboxExperimentReport:
    baseline_keys = {key for run in baseline_runs for key in run.metrics}
    candidate_keys = {key for run in candidate_runs for key in run.metrics}
    metric_keys = sorted(baseline_keys & candidate_keys)

    prediction_by_metric = {item.metric: item for item in candidate.proposal.predicted_metrics}
    unsupported_targets = sorted(set(prediction_by_metric) - set(metric_keys))
    comparisons: list[MetricComparison] = []
    target_misses: list[str] = []
    for metric in metric_keys:
        baseline_values = [run.metrics[metric] for run in baseline_runs]
        candidate_values = [run.metrics[metric] for run in candidate_runs]
        baseline_mean = _mean(baseline_values)
        candidate_mean = _mean(candidate_values)
        delta = candidate_mean - baseline_mean
        prediction = prediction_by_metric.get(metric)
        met = _target_met(prediction, delta) if prediction is not None else None
        if prediction is not None and not met:
            target_misses.append(f"target_not_met:{metric}")
        comparisons.append(
            MetricComparison(
                metric=metric,
                baseline_mean=baseline_mean,
                candidate_mean=candidate_mean,
                delta=delta,
                baseline_variance=_variance(baseline_values),
                candidate_variance=_variance(candidate_values),
                expected_delta=prediction.expected_delta if prediction else None,
                minimum_acceptable_delta=(
                    prediction.minimum_acceptable_delta if prediction else None
                ),
                target_met=met,
            )
        )

    invariant_names = sorted(
        {key for run in baseline_runs for key in run.invariants}
        | {key for run in candidate_runs for key in run.invariants}
    )
    baseline_invariant_failures = [
        name
        for name in invariant_names
        if not all(run.invariants.get(name, False) for run in baseline_runs)
    ]
    new_failure_modes = [
        name
        for name in invariant_names
        if all(run.invariants.get(name, False) for run in baseline_runs)
        and not all(run.invariants.get(name, False) for run in candidate_runs)
    ]

    budget = candidate.proposal.resource_budget
    baseline_budget = _budget_violations("baseline", baseline_runs, budget)
    candidate_budget = _budget_violations("candidate", candidate_runs, budget)
    budget_violations = (*baseline_budget, *candidate_budget)

    baseline_reasoning = sum(item.reasoning_steps for item in baseline_runs)
    candidate_reasoning = sum(item.reasoning_steps for item in candidate_runs)
    baseline_tools = sum(item.tool_actions for item in baseline_runs)
    candidate_tools = sum(item.tool_actions for item in candidate_runs)
    baseline_wall = sum(item.wall_time_seconds for item in baseline_runs)
    candidate_wall = sum(item.wall_time_seconds for item in candidate_runs)
    resources = ResourceComparison(
        baseline_reasoning_steps=baseline_reasoning,
        candidate_reasoning_steps=candidate_reasoning,
        reasoning_step_delta=candidate_reasoning - baseline_reasoning,
        baseline_tool_actions=baseline_tools,
        candidate_tool_actions=candidate_tools,
        tool_action_delta=candidate_tools - baseline_tools,
        baseline_wall_time_seconds=baseline_wall,
        candidate_wall_time_seconds=candidate_wall,
        wall_time_delta_seconds=candidate_wall - baseline_wall,
    )

    invalid_reasons: list[str] = []
    if unsupported_targets:
        invalid_reasons.extend(f"unsupported_target_metric:{item}" for item in unsupported_targets)
    if baseline_invariant_failures:
        invalid_reasons.extend(
            f"baseline_invariant_failed:{item}" for item in baseline_invariant_failures
        )
    if not all(run.passed for run in baseline_runs):
        invalid_reasons.append("baseline_benchmark_failed")
    if baseline_budget:
        invalid_reasons.extend(baseline_budget)
    if not baseline_untouched:
        invalid_reasons.append("baseline_snapshot_changed")
    if not teardown_verified:
        invalid_reasons.append("sandbox_teardown_not_verified")

    regressions = list(target_misses)
    regressions.extend(f"new_failure_mode:{item}" for item in new_failure_modes)
    if not all(run.passed for run in candidate_runs):
        regressions.append("candidate_benchmark_failed")
    regressions.extend(candidate_budget)

    experiment_valid = not invalid_reasons
    if not experiment_valid:
        disposition = ExperimentDisposition.INCONCLUSIVE
        reasons = tuple(dict.fromkeys(invalid_reasons))
    elif regressions:
        disposition = ExperimentDisposition.REJECTION_RECOMMENDED
        reasons = tuple(dict.fromkeys(regressions))
    else:
        disposition = ExperimentDisposition.PROMOTION_RECOMMENDED
        reasons = ("all_declared_targets_met_without_new_failure_modes",)

    return SandboxExperimentReport(
        candidate_id=candidate.candidate_id,
        proposal_fingerprint=candidate.proposal_fingerprint,
        baseline_version=candidate.proposal.baseline_version,
        baseline_snapshot_fingerprint=baseline_snapshot.fingerprint,
        candidate_snapshot_fingerprint=candidate_snapshot.fingerprint,
        suite_name=baseline_runs[0].suite_name,
        suite_version=baseline_runs[0].suite_version,
        seeds=tuple(item.seed for item in baseline_runs),
        baseline_runs=baseline_runs,
        candidate_runs=candidate_runs,
        metric_comparisons=tuple(comparisons),
        resource_comparison=resources,
        new_failure_modes=tuple(new_failure_modes),
        regressions=tuple(dict.fromkeys(regressions)),
        budget_violations=tuple(dict.fromkeys(budget_violations)),
        baseline_untouched=baseline_untouched,
        teardown_verified=teardown_verified,
        experiment_valid=experiment_valid,
        disposition=disposition,
        reasons=reasons,
        evidence_directory=str(evidence_directory),
    )


class ImprovementExperimentSandbox:
    """Runs matched baseline/candidate trials in disposable directories."""

    def __init__(self, runner: SandboxBenchmarkRunner, *, base_seed: int = 15000) -> None:
        self.runner = runner
        self.base_seed = base_seed

    def run(
        self,
        candidate: ImprovementCandidate,
        baseline_snapshot: SandboxSnapshot,
        output_directory: str | Path,
    ) -> SandboxExperimentReport:
        if candidate.status != CandidateStatus.SANDBOX_ELIGIBLE:
            raise SandboxExperimentError("only sandbox-eligible candidates may be tested")
        if candidate.proposal.baseline_version != baseline_snapshot.source_system_version:
            raise SandboxExperimentError("candidate and sandbox baseline versions differ")

        root = Path(output_directory)
        if root.exists() and any(root.iterdir()):
            raise SandboxExperimentError("sandbox output directory must be empty")
        root.mkdir(parents=True, exist_ok=True)
        evidence_root = root / "evidence"
        working_root = root / "working"
        evidence_root.mkdir(parents=True, exist_ok=True)
        working_root.mkdir(parents=True, exist_ok=True)

        baseline_fingerprint_before = baseline_snapshot.fingerprint
        baseline_json = baseline_snapshot.model_dump_json(indent=2) + "\n"
        baseline_path = root / "baseline-snapshot.json"
        baseline_path.write_text(baseline_json, encoding="utf-8")
        baseline_file_hash = hashlib.sha256(baseline_path.read_bytes()).hexdigest()

        candidate_snapshot = _apply_candidate_snapshot(baseline_snapshot, candidate)
        (root / "candidate-snapshot.json").write_text(
            candidate_snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )

        seeds = tuple(
            self.base_seed + index
            for index in range(candidate.proposal.resource_budget.benchmark_runs)
        )
        plan = SandboxExperimentPlan(
            candidate_id=str(candidate.candidate_id),
            proposal_fingerprint=candidate.proposal_fingerprint,
            baseline_snapshot_fingerprint=baseline_snapshot.fingerprint,
            candidate_snapshot_fingerprint=candidate_snapshot.fingerprint,
            suite_name=self.runner.name,
            suite_version=self.runner.version,
            seeds=seeds,
            resource_budget=candidate.proposal.resource_budget,
        )
        (root / "experiment-plan.json").write_text(
            plan.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )

        baseline_runs: list[ExperimentRunResult] = []
        candidate_runs: list[ExperimentRunResult] = []
        try:
            for index, seed in enumerate(seeds, start=1):
                pair = (
                    (ExperimentVariant.BASELINE, baseline_snapshot, baseline_runs),
                    (ExperimentVariant.CANDIDATE, candidate_snapshot, candidate_runs),
                )
                for variant, snapshot, sink in pair:
                    run_work = working_root / variant.value / f"run-{index:02d}"
                    run_work.mkdir(parents=True, exist_ok=False)
                    result = self.runner.run(
                        snapshot,
                        seed=seed,
                        run_directory=run_work,
                        budget=candidate.proposal.resource_budget,
                    )
                    if result.variant != variant:
                        raise SandboxExperimentError("runner returned the wrong experiment variant")
                    if result.seed != seed:
                        raise SandboxExperimentError("runner returned the wrong experiment seed")
                    if result.snapshot_fingerprint != snapshot.fingerprint:
                        raise SandboxExperimentError("runner snapshot fingerprint mismatch")
                    if result.suite_name != self.runner.name or result.suite_version != self.runner.version:
                        raise SandboxExperimentError("runner suite identity mismatch")

                    evidence_dir = evidence_root / variant.value / f"run-{index:02d}"
                    evidence_dir.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(run_work, evidence_dir)
                    digests = _artifact_digests(evidence_dir)
                    completed = result.model_copy(update={"artifacts": digests})
                    (evidence_dir / "run-result.json").write_text(
                        completed.model_dump_json(indent=2) + "\n", encoding="utf-8"
                    )
                    sink.append(completed)
        finally:
            if working_root.exists():
                shutil.rmtree(working_root)

        teardown_verified = not working_root.exists()
        baseline_untouched = (
            baseline_snapshot.fingerprint == baseline_fingerprint_before
            and baseline_path.is_file()
            and hashlib.sha256(baseline_path.read_bytes()).hexdigest() == baseline_file_hash
            and baseline_path.read_text(encoding="utf-8") == baseline_json
        )

        report = _compare(
            candidate,
            baseline_snapshot,
            candidate_snapshot,
            tuple(baseline_runs),
            tuple(candidate_runs),
            teardown_verified=teardown_verified,
            baseline_untouched=baseline_untouched,
            evidence_directory=evidence_root,
        )
        (root / "experiment-report.json").write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return report


class ScriptedAutonomyExperimentRunner:
    """Concrete trusted runner backed by the permanent Milestone 8 benchmark."""

    @property
    def name(self) -> str:
        return "scripted-autonomy"

    @property
    def version(self) -> str:
        return "scripted-autonomy-v1"

    def run(
        self,
        snapshot: SandboxSnapshot,
        *,
        seed: int,
        run_directory: Path,
        budget: ImprovementResourceBudget,
    ) -> ExperimentRunResult:
        state = deepcopy(snapshot.state)
        state["system_version"] = snapshot.variant_version.model_dump(mode="json")
        config = HarnessConfig.model_validate(state)

        start = time.monotonic()
        report = run_scripted_autonomy_benchmark(config, run_directory)
        wall_time = time.monotonic() - start

        scenarios = report.scenarios
        count = len(scenarios)
        metrics = {
            "benchmark_pass_rate": sum(item.passed for item in scenarios) / count,
            "state_correct_rate": sum(item.state_correct for item in scenarios) / count,
            "goal_retention_rate": sum(item.goal_retained for item in scenarios) / count,
            "replay_valid_rate": sum(item.replay_valid for item in scenarios) / count,
            "trace_complete_rate": sum(item.trace_complete for item in scenarios) / count,
            "retrieval_usefulness": sum(item.retrieval_usefulness for item in scenarios) / count,
            "recoveries": float(report.total_recoveries),
            "action_count": float(report.total_actions),
            "verification_failures": float(sum(item.verification_failures for item in scenarios)),
            "working_evictions": float(sum(item.working_evictions for item in scenarios)),
            "maintenance_cycles": float(sum(item.maintenance_cycles for item in scenarios)),
            "max_working_pressure": max(item.max_working_pressure for item in scenarios),
            "trace_events": float(report.total_events),
            "authoritative_transitions": float(report.total_authoritative_transitions),
        }
        invariants = {
            "suite_passed": report.passed,
            "all_goals_retained": all(item.goal_retained for item in scenarios),
            "zero_illegal_transitions": all(item.illegal_transitions == 0 for item in scenarios),
            "all_traces_complete": all(item.trace_complete for item in scenarios),
            "all_replay_valid": all(item.replay_valid for item in scenarios),
        }

        reasoning_steps = 0
        tool_actions = 0
        for trace_path in sorted(run_directory.glob("*.jsonl")):
            for event in TraceStore(trace_path).events():
                reasoning_steps += int(event.event_type == EventType.REASONING_REQUESTED)
                tool_actions += int(event.event_type == EventType.ACTION_EXECUTED)

        notes: list[str] = []
        if reasoning_steps > budget.max_reasoning_steps:
            notes.append("measured reasoning usage exceeded the declared experiment budget")
        if tool_actions > budget.max_tool_actions:
            notes.append("measured tool usage exceeded the declared experiment budget")
        if wall_time > budget.max_wall_time_seconds:
            notes.append("measured wall time exceeded the declared experiment budget")

        return ExperimentRunResult(
            suite_name=self.name,
            suite_version=self.version,
            variant=snapshot.variant,
            seed=seed,
            source_system_version=snapshot.source_system_version,
            variant_version=snapshot.variant_version,
            snapshot_fingerprint=snapshot.fingerprint,
            passed=report.passed,
            metrics=metrics,
            invariants=invariants,
            reasoning_steps=reasoning_steps,
            tool_actions=tool_actions,
            wall_time_seconds=wall_time,
            notes=tuple(notes),
        )
