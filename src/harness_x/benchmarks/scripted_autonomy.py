"""Five deterministic long-horizon scenarios for the Milestone 8 checkpoint."""

from __future__ import annotations

from pathlib import Path

from harness_x.config import HarnessConfig
from harness_x.core.contracts import Observation
from harness_x.core.events import EventType
from harness_x.core.ids import MemoryId
from harness_x.memory import EpisodeOutcome, SemanticState
from harness_x.orchestrator import OperatingMode, TaskOrchestrator
from harness_x.routines import RecoveryRoutineRequest, RoutineStatus

from .models import BenchmarkReport, ScenarioMetrics
from .routines import BenchmarkMaintenanceRequest, BenchmarkStepRequest
from .runtime import BenchmarkRuntime, derive_metrics


def _observation(runtime: BenchmarkRuntime, step_key: str, **content: object) -> Observation:
    return Observation(
        task_id=runtime.recorder.task_id,
        kind=f"benchmark:{step_key}",
        content={"step_key": step_key, **content},
        provenance=runtime.provenance,
    )


def _step(
    runtime: BenchmarkRuntime,
    *,
    goal_id,
    step_key: str,
    dependencies: tuple[str, ...] = (),
    tool_name: str = "calculator",
    tool_arguments: dict | None = None,
    expected_result: dict | None = None,
    required_result_keys: tuple[str, ...] = ("value",),
    priority: float = 0.55,
):
    return runtime.engine.execute(
        "benchmark_step",
        BenchmarkStepRequest(
            goal_id=goal_id,
            step_key=step_key,
            dependencies=dependencies,
            observation=_observation(runtime, step_key, phase="scripted"),
            tool_name=tool_name,
            tool_arguments=tool_arguments or {
                "operation": "add",
                "a": 1,
                "b": 1,
            },
            expected_result=expected_result or {"value": 2.0},
            required_result_keys=required_result_keys,
            observation_priority=priority,
        ),
    )


def _step_with_maintenance(runtime: BenchmarkRuntime, **kwargs):
    """Execute a step, servicing explicit maintenance if pressure blocks progress."""
    execution = _step(runtime, **kwargs)
    attempts = 0
    while (
        execution.result.status == RoutineStatus.BLOCKED
        and execution.result.data.get("reason") == "maintenance_required"
    ):
        attempts += 1
        if attempts > 4:
            raise RuntimeError("benchmark maintenance failed to restore progress")
        maintained = runtime.engine.execute(
            "benchmark_maintenance",
            BenchmarkMaintenanceRequest(target_pressure=0.50),
        )
        if maintained.result.status != RoutineStatus.SUCCEEDED:
            raise RuntimeError(f"benchmark maintenance blocked: {maintained.result.data}")
        execution = _step(runtime, **kwargs)
    return execution


def _successful_episodes(runtime: BenchmarkRuntime) -> tuple:
    return tuple(
        episode
        for episode in runtime.episodic.all()
        if episode.outcome == EpisodeOutcome.SUCCESS
        and "benchmark_step" in episode.tags
    )


def _dependency_scenario(root: Path, config: HarnessConfig) -> ScenarioMetrics:
    runtime = BenchmarkRuntime.create(root, config, name="dependency", working_capacity=64)
    goal_id = runtime.create_root_goal("Complete a dependency-ordered scripted chain")

    premature = _step(
        runtime,
        goal_id=goal_id,
        step_key="dep_02",
        dependencies=("dep_01",),
        tool_arguments={"operation": "add", "a": 2, "b": 1},
        expected_result={"value": 3.0},
    )
    blocked_out_of_order = (
        premature.result.status == RoutineStatus.BLOCKED
        and premature.result.data.get("reason") == "unmet_dependency"
        and runtime.orchestrator.session.mode == OperatingMode.TASK_ACTIVE
    )

    expected_order: list[str] = []
    for index in range(1, 15):
        step_key = f"dep_{index:02d}"
        dependencies = () if index == 1 else (f"dep_{index - 1:02d}",)
        execution = _step(
            runtime,
            goal_id=goal_id,
            step_key=step_key,
            dependencies=dependencies,
            tool_arguments={"operation": "add", "a": index, "b": 1},
            expected_result={"value": float(index + 1)},
        )
        if execution.result.status != RoutineStatus.SUCCEEDED:
            raise RuntimeError(f"dependency scenario blocked at {step_key}: {execution.result.data}")
        expected_order.append(step_key)

    successes = sorted(_successful_episodes(runtime), key=lambda item: item.end_step)
    actual_order = [str(item.metadata.get("step_key")) for item in successes]
    dependency_order = actual_order == expected_order
    runtime.finish(goal_id)

    checks = {
        "out_of_order_step_blocked": blocked_out_of_order,
        "dependency_order_preserved": dependency_order,
        "all_dependency_steps_completed": len(successes) == len(expected_order),
    }
    return derive_metrics(
        runtime,
        goal_id,
        state_correct=all(checks.values()),
        checks=checks,
    )


def _interruption_scenario(root: Path, config: HarnessConfig) -> ScenarioMetrics:
    runtime = BenchmarkRuntime.create(root, config, name="interruption", working_capacity=48)
    goal_id = runtime.create_root_goal("Resume a dependency task from an exact checkpoint")

    for index in range(1, 6):
        step_key = f"int_{index:02d}"
        execution = _step(
            runtime,
            goal_id=goal_id,
            step_key=step_key,
            dependencies=() if index == 1 else (f"int_{index - 1:02d}",),
            tool_arguments={"operation": "multiply", "a": index, "b": 2},
            expected_result={"value": float(index * 2)},
        )
        if execution.result.status != RoutineStatus.SUCCEEDED:
            raise RuntimeError(f"interruption pre-checkpoint step failed: {step_key}")

    checkpoint_path = root / "interruption.checkpoint.json"
    if checkpoint_path.exists():
        checkpoint_path.unlink()
    runtime.orchestrator.suspend(
        "benchmark_scripted_interruption",
        checkpoint_path=checkpoint_path,
    )
    checkpoint = runtime.orchestrator.checkpoint_store.load(checkpoint_path)
    restored = TaskOrchestrator.restore(
        checkpoint,
        runtime.recorder,
        checkpoint_store=runtime.orchestrator.checkpoint_store,
    )
    runtime.replace_orchestrator(restored)
    resumed = runtime.orchestrator.resume("benchmark_resume_from_checkpoint")
    resumed_exact_mode = resumed.mode == OperatingMode.TASK_ACTIVE

    for index in range(6, 11):
        step_key = f"int_{index:02d}"
        execution = _step(
            runtime,
            goal_id=goal_id,
            step_key=step_key,
            dependencies=(f"int_{index - 1:02d}",),
            tool_arguments={"operation": "multiply", "a": index, "b": 2},
            expected_result={"value": float(index * 2)},
        )
        if execution.result.status != RoutineStatus.SUCCEEDED:
            raise RuntimeError(f"interruption post-resume step failed: {step_key}")

    successes = _successful_episodes(runtime)
    runtime.finish(goal_id)
    checks = {
        "checkpoint_file_written": checkpoint_path.is_file(),
        "restored_exact_resume_mode": resumed_exact_mode,
        "pre_checkpoint_memory_remained_usable": len(successes) == 10,
    }
    return derive_metrics(
        runtime,
        goal_id,
        state_correct=all(checks.values()),
        checks=checks,
        notes=(
            "Milestone 8 validates exact orchestrator checkpoint restore with in-process memory continuity; cold restart reconstruction of every memory object remains a later persistence boundary.",
        ),
    )


def _memory_pressure_scenario(root: Path, config: HarnessConfig) -> ScenarioMetrics:
    runtime = BenchmarkRuntime.create(root, config, name="memory_pressure", working_capacity=8)
    goal_id = runtime.create_root_goal("Survive repeated bounded-working-state pressure")

    anchor = runtime.working.add(
        kind="governing_anchor",
        content={"rule": "pinned benchmark anchor must survive"},
        priority=1.0,
        size_units=1,
        source=str(goal_id),
        provenance=runtime.provenance,
        pinned=True,
    )

    for index in range(1, 25):
        step_key = f"pressure_{index:02d}"
        execution = _step_with_maintenance(
            runtime,
            goal_id=goal_id,
            step_key=step_key,
            tool_arguments={"operation": "add", "a": index, "b": 10},
            expected_result={"value": float(index + 10)},
            priority=0.30,
        )
        if execution.result.status != RoutineStatus.SUCCEEDED:
            raise RuntimeError(f"memory-pressure scenario blocked at {step_key}")

    anchor_survived = runtime.working.get(anchor.memory_id).pinned
    successes = _successful_episodes(runtime)
    events = runtime.recorder.store.events(trace_id=runtime.recorder.trace_id)
    evictions = sum(
        event.event_type == EventType.MEMORY_EVICTED
        and event.component == "memory.working"
        for event in events
    )
    maintenance_cycles = sum(
        event.event_type == EventType.MODE_CHANGED
        and event.metadata.get("to") == OperatingMode.MAINTENANCE.value
        for event in events
    )
    runtime.finish(goal_id)
    checks = {
        "pinned_anchor_survived": anchor_survived,
        "all_pressure_steps_completed": len(successes) == 24,
        "working_state_evicted_items": evictions > 0,
        "maintenance_was_exercised": maintenance_cycles > 0,
    }
    return derive_metrics(
        runtime,
        goal_id,
        state_correct=all(checks.values()),
        checks=checks,
    )


def _failure_recovery_scenario(root: Path, config: HarnessConfig) -> ScenarioMetrics:
    runtime = BenchmarkRuntime.create(root, config, name="failure_recovery", working_capacity=48)
    goal_id = runtime.create_root_goal("Recover from tool and verifier failures")

    expected_recoveries = 0
    for index in range(1, 13):
        step_key = f"recover_{index:02d}"
        dependency = () if index == 1 else (f"recover_{index - 1:02d}",)

        if index in {3, 10}:
            primary = _step(
                runtime,
                goal_id=goal_id,
                step_key=step_key,
                dependencies=dependency,
                tool_name="unreliable",
                tool_arguments={"value": step_key, "fail": True},
                expected_result={"value": step_key},
            )
        elif index == 7:
            primary = _step(
                runtime,
                goal_id=goal_id,
                step_key=step_key,
                dependencies=dependency,
                tool_arguments={"operation": "add", "a": index, "b": 1},
                expected_result={"value": 999.0},
            )
        else:
            primary = _step(
                runtime,
                goal_id=goal_id,
                step_key=step_key,
                dependencies=dependency,
                tool_arguments={"operation": "add", "a": index, "b": 1},
                expected_result={"value": float(index + 1)},
            )

        if primary.result.status == RoutineStatus.BLOCKED and primary.result.data.get(
            "reason"
        ) in {"tool_execution_failed", "verification_failed"}:
            expected_recoveries += 1
            error_id = MemoryId(value=str(primary.result.data["error_memory_id"]))
            recovered = runtime.engine.execute(
                "recovery",
                RecoveryRoutineRequest(
                    error_memory_id=error_id,
                    query=step_key,
                ),
            )
            if recovered.result.status != RoutineStatus.SUCCEEDED:
                raise RuntimeError(f"recovery routine failed for {step_key}")

            if index in {3, 10}:
                retry = _step(
                    runtime,
                    goal_id=goal_id,
                    step_key=step_key,
                    dependencies=dependency,
                    tool_name="unreliable",
                    tool_arguments={"value": step_key, "fail": False},
                    expected_result={"value": step_key},
                )
            else:
                retry = _step(
                    runtime,
                    goal_id=goal_id,
                    step_key=step_key,
                    dependencies=dependency,
                    tool_arguments={"operation": "add", "a": index, "b": 1},
                    expected_result={"value": float(index + 1)},
                )
            if retry.result.status != RoutineStatus.SUCCEEDED:
                raise RuntimeError(f"alternative path failed for {step_key}")
            runtime.errors.resolve(
                error_id,
                resolution_evidence=(str(retry.result.data["episode_memory_id"]),),
                confirmed_cause="scripted primary path intentionally failed",
            )
        elif primary.result.status != RoutineStatus.SUCCEEDED:
            raise RuntimeError(f"unexpected failure state for {step_key}: {primary.result.data}")

    successes = _successful_episodes(runtime)
    unresolved = len(runtime.errors.unresolved())
    events = runtime.recorder.store.events(trace_id=runtime.recorder.trace_id)
    recoveries = sum(
        event.event_type == EventType.MODE_CHANGED
        and event.metadata.get("to") == OperatingMode.RECOVERY.value
        for event in events
    )
    verifier_failures = sum(
        event.event_type == EventType.VERIFICATION_COMPLETED
        and event.metadata.get("accepted") is False
        for event in events
    )
    runtime.finish(goal_id)
    checks = {
        "all_logical_steps_completed": len(successes) == 12,
        "expected_recovery_count": recoveries == expected_recoveries == 3,
        "verification_failure_exercised": verifier_failures >= 1,
        "resolved_errors_not_left_open": unresolved == 0,
    }
    return derive_metrics(
        runtime,
        goal_id,
        state_correct=all(checks.values()),
        checks=checks,
    )


def _contradiction_scenario(root: Path, config: HarnessConfig) -> ScenarioMetrics:
    runtime = BenchmarkRuntime.create(root, config, name="contradiction", working_capacity=32)
    goal_id = runtime.create_root_goal("Preserve contradictory verified observations")

    alpha_run = _step(
        runtime,
        goal_id=goal_id,
        step_key="claim_alpha",
        tool_name="unreliable",
        tool_arguments={"value": "alpha", "fail": False},
        expected_result={"value": "alpha"},
    )
    beta_run = _step(
        runtime,
        goal_id=goal_id,
        step_key="claim_beta",
        dependencies=("claim_alpha",),
        tool_name="unreliable",
        tool_arguments={"value": "beta", "fail": False},
        expected_result={"value": "beta"},
    )
    if (
        alpha_run.result.status != RoutineStatus.SUCCEEDED
        or beta_run.result.status != RoutineStatus.SUCCEEDED
    ):
        raise RuntimeError("contradiction evidence steps did not verify")

    alpha_episode = runtime.episodic.get(
        MemoryId(value=str(alpha_run.result.data["episode_memory_id"]))
    )
    beta_episode = runtime.episodic.get(
        MemoryId(value=str(beta_run.result.data["episode_memory_id"]))
    )

    alpha = runtime.semantic.create_candidate(
        claim_key="active_endpoint",
        statement="The active endpoint is alpha",
        value="alpha",
        confidence=0.90,
        source_episode_ids=(alpha_episode.memory_id,),
        provenance=runtime.provenance,
    )
    alpha = runtime.semantic.evaluate(
        alpha.memory_id,
        accepted=True,
        evidence_refs=(str(alpha_episode.metadata["verification_event_id"]),),
        reason="independent tool result verified",
        confidence=0.95,
    )
    alpha = runtime.semantic.promote(
        alpha.memory_id,
        reason="promote verified alpha observation",
    )

    beta = runtime.semantic.create_candidate(
        claim_key="active_endpoint",
        statement="The active endpoint is beta",
        value="beta",
        confidence=0.90,
        source_episode_ids=(beta_episode.memory_id,),
        provenance=runtime.provenance,
    )
    beta = runtime.semantic.evaluate(
        beta.memory_id,
        accepted=True,
        evidence_refs=(str(beta_episode.metadata["verification_event_id"]),),
        reason="independent tool result verified",
        confidence=0.95,
    )
    beta = runtime.semantic.promote(
        beta.memory_id,
        reason="promote verified beta observation",
    )

    alpha_now = runtime.semantic.get(alpha.memory_id)
    beta_now = runtime.semantic.get(beta.memory_id)
    contradictions = runtime.semantic.contradictions("active_endpoint")
    symmetric = (
        beta_now.memory_id in alpha_now.contradiction_ids
        and alpha_now.memory_id in beta_now.contradiction_ids
    )
    both_verified = (
        alpha_now.state == SemanticState.VERIFIED
        and beta_now.state == SemanticState.VERIFIED
    )
    runtime.finish(goal_id)
    checks = {
        "both_observations_independently_verified": both_verified,
        "contradiction_links_are_symmetric": symmetric,
        "both_conflicting_claims_remain_represented": len(contradictions) == 2,
    }
    return derive_metrics(
        runtime,
        goal_id,
        state_correct=all(checks.values()),
        checks=checks,
    )


def run_scripted_autonomy_benchmark(
    config: HarnessConfig,
    output_dir: str | Path,
) -> BenchmarkReport:
    """Run all Milestone 8 scenarios and return a machine-readable report."""

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    scenarios = (
        _dependency_scenario(root, config),
        _interruption_scenario(root, config),
        _memory_pressure_scenario(root, config),
        _failure_recovery_scenario(root, config),
        _contradiction_scenario(root, config),
    )
    total_events = sum(item.trace_events for item in scenarios)
    total_transitions = sum(item.authoritative_transitions for item in scenarios)
    total_actions = sum(item.action_count for item in scenarios)
    total_recoveries = sum(item.recoveries for item in scenarios)
    passed = (
        all(item.passed for item in scenarios)
        and total_transitions >= 300
        and total_events >= 300
    )
    return BenchmarkReport(
        passed=passed,
        total_events=total_events,
        total_authoritative_transitions=total_transitions,
        total_actions=total_actions,
        total_recoveries=total_recoveries,
        scenarios=scenarios,
    )
