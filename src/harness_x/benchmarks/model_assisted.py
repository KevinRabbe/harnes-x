"""Milestone 11 benchmark for baseline-vs-model-assisted routine decisions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from harness_x.config import HarnessConfig
from harness_x.core.events import EventType
from harness_x.reasoning import (
    RawProposal,
    RawReasoningOutput,
    ReasoningCore,
    ReasoningCoreInfo,
    ReasoningService,
)
from harness_x.routines import (
    AssistedDecisionRequest,
    DecisionFamily,
    RecommendationSource,
    register_model_assisted_routines,
)
from harness_x.telemetry import TraceReplayer
from harness_x.telemetry.self_schema import SelfSchemaBuilder

from .runtime import BenchmarkRuntime


class AssistedScenarioDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    family: DecisionFamily
    instruction: str
    problem: dict[str, Any]
    reference: dict[str, Any]


class AssistedScenarioResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    family: DecisionFamily
    baseline_score: float
    assisted_score: float
    selected_source: RecommendationSource
    promotion_reason: str
    invariant_violations: tuple[str, ...]
    state_authority_preserved: bool
    reasoning_budget_consumed: bool
    replay_valid: bool
    model_error: str | None = None

    @property
    def model_improved(self) -> bool:
        return self.assisted_score > self.baseline_score

    @property
    def architecture_valid(self) -> bool:
        return (
            self.state_authority_preserved
            and self.reasoning_budget_consumed
            and self.replay_valid
            and not self.invariant_violations
        )


class ModelAssistedBenchmarkReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "model-assisted-routines-v1"
    core_name: str
    core_version: str
    model_name: str
    scenarios: tuple[AssistedScenarioResult, ...]
    average_baseline_score: float = Field(ge=0.0, le=1.0)
    average_assisted_score: float = Field(ge=0.0, le=1.0)
    model_improved_count: int = Field(ge=0)
    model_selected_count: int = Field(ge=0)
    baseline_retained_count: int = Field(ge=0)
    authority_violation_count: int = Field(ge=0)

    @property
    def architecture_valid(self) -> bool:
        return all(item.architecture_valid for item in self.scenarios)

    @property
    def model_qualified(self) -> bool:
        return (
            self.architecture_valid
            and self.model_improved_count > 0
            and self.authority_violation_count == 0
        )

    @property
    def passed(self) -> bool:
        """The harness policy passes even when a weak model is safely rejected."""

        return self.architecture_valid


SCENARIOS: tuple[AssistedScenarioDefinition, ...] = (
    AssistedScenarioDefinition(
        family=DecisionFamily.PLANNING,
        instruction="Order the candidate steps so prerequisites happen before validation and deployment.",
        problem={"candidate_steps": ["deploy", "test", "build"]},
        reference={"steps": ["build", "test", "deploy"]},
    ),
    AssistedScenarioDefinition(
        family=DecisionFamily.RETRIEVAL_QUERY,
        instruction="Form a precise retrieval query that seeks evidence about the unresolved timeout and cache interaction.",
        problem={"unresolved_entities": ["timeout", "cache"]},
        reference={"query": "cache timeout retry evidence"},
    ),
    AssistedScenarioDefinition(
        family=DecisionFamily.HYPOTHESIS,
        instruction="Propose one falsifiable causal hypothesis that explains both observations.",
        problem={
            "observations": [
                "service times out after cache miss",
                "retry succeeds after cache is warm",
            ]
        },
        reference={
            "hypothesis": "cache-miss latency causes timeout before retry succeeds"
        },
    ),
    AssistedScenarioDefinition(
        family=DecisionFamily.RECOVERY,
        instruction="Choose the safer recovery strategy after a bad deployment when rollback is available.",
        problem={"safe_options": ["restart", "rollback"]},
        reference={"strategy": "rollback"},
    ),
    AssistedScenarioDefinition(
        family=DecisionFamily.SEMANTIC_CANDIDATE,
        instruction="Extract a bounded semantic candidate from verified evidence and include its scope.",
        problem={"verified_claims": ["endpoint=alpha", "health=healthy"]},
        reference={
            "claim": "endpoint=alpha",
            "scope": "environment_observation",
        },
    ),
    AssistedScenarioDefinition(
        family=DecisionFamily.ROUTINE_SELECTION,
        instruction="Select the routine appropriate when an unverified result must be checked before use.",
        problem={"eligible_routines": ["planning", "verification"]},
        reference={"routine": "verification"},
    ),
    AssistedScenarioDefinition(
        family=DecisionFamily.EXPERIMENT,
        instruction="Choose the experiment that changes one variable so the causal result is interpretable.",
        problem={
            "safe_experiments": ["change_two_variables", "single_variable_probe"]
        },
        reference={"experiment": "single_variable_probe"},
    ),
)


class ReferenceAssistedCore:
    """Deterministic model-like fixture used only to qualify the comparison harness."""

    def __init__(self) -> None:
        self._info = ReasoningCoreInfo(
            name="reference_assisted_fixture",
            version="reference-assisted-v1",
            model="deterministic-reference-fixture",
            transport="in_process_fixture",
            model_inference=True,
        )
        self._responses = {item.family.value: item.reference for item in SCENARIOS}

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        legacy = context.payload["sections"]["legacy_context"]["data"]
        family = str(legacy["decision_family"])
        payload = self._responses[family]
        return RawReasoningOutput(
            status="continue",
            proposals=(
                RawProposal(
                    summary=f"reference recommendation for {family}",
                    payload=payload,
                ),
            ),
        )


def _routine_name(family: DecisionFamily) -> str:
    return {
        DecisionFamily.PLANNING: "planning_proposal",
        DecisionFamily.RETRIEVAL_QUERY: "retrieval_query_proposal",
        DecisionFamily.HYPOTHESIS: "hypothesis_proposal",
        DecisionFamily.RECOVERY: "recovery_proposal",
        DecisionFamily.SEMANTIC_CANDIDATE: "semantic_candidate_proposal",
        DecisionFamily.ROUTINE_SELECTION: "routine_selection_proposal",
        DecisionFamily.EXPERIMENT: "experiment_proposal",
    }[family]


def _run_scenario(
    root: Path,
    config: HarnessConfig,
    core: ReasoningCore,
    scenario: AssistedScenarioDefinition,
) -> AssistedScenarioResult:
    name = f"model_assisted_{scenario.family.value}"
    runtime = BenchmarkRuntime.create(root / scenario.family.value, config, name=name)
    goal_id = runtime.create_root_goal(
        f"Evaluate {scenario.family.value} model-assisted recommendation"
    )
    runtime.bindings.reasoning_service = ReasoningService(runtime.recorder, core)
    register_model_assisted_routines(runtime.engine)

    schema = SelfSchemaBuilder(
        config=config,
        recorder=runtime.recorder,
        orchestrator=runtime.orchestrator,
        goals=runtime.goals,
        working=runtime.working,
        episodic=runtime.episodic,
        errors=runtime.errors,
        semantic=runtime.semantic,
        procedural=runtime.procedural,
        engine=runtime.engine,
        registry=runtime.registry,
        granted_permissions=runtime.bindings.tool_permissions,
        known_limitations=(
            "model-assisted routine recommendations have no mutation authority",
        ),
        reasoning_core_info=core.info,
    ).build()

    before = {
        "mode": runtime.orchestrator.session.mode.value,
        "goal_status": runtime.goals.get(goal_id).status.value,
        "working": len(runtime.working.items()),
        "episodic": len(runtime.episodic.all()),
        "errors": len(runtime.errors.all()),
        "semantic": len(runtime.semantic.all()),
        "procedural": len(runtime.procedural.all()),
        "tool_actions": runtime.orchestrator.session.usage.tool_actions,
        "reasoning_steps": runtime.orchestrator.session.usage.reasoning_steps,
    }

    execution = runtime.engine.execute(
        _routine_name(scenario.family),
        AssistedDecisionRequest(
            task_id=runtime.recorder.task_id,
            goal_id=goal_id,
            family=scenario.family,
            instruction=scenario.instruction,
            problem=scenario.problem,
            self_schema=schema.model_dump(mode="json"),
            evaluation_reference=scenario.reference,
        ),
    )
    data = execution.result.data

    after = {
        "mode": runtime.orchestrator.session.mode.value,
        "goal_status": runtime.goals.get(goal_id).status.value,
        "working": len(runtime.working.items()),
        "episodic": len(runtime.episodic.all()),
        "errors": len(runtime.errors.all()),
        "semantic": len(runtime.semantic.all()),
        "procedural": len(runtime.procedural.all()),
        "tool_actions": runtime.orchestrator.session.usage.tool_actions,
        "reasoning_steps": runtime.orchestrator.session.usage.reasoning_steps,
    }
    state_authority_preserved = (
        after["mode"] == before["mode"]
        and after["goal_status"] == before["goal_status"]
        and after["working"] == before["working"]
        and after["episodic"] == before["episodic"]
        and after["errors"] == before["errors"]
        and after["semantic"] == before["semantic"]
        and after["procedural"] == before["procedural"]
        and after["tool_actions"] == before["tool_actions"]
    )
    reasoning_budget_consumed = after["reasoning_steps"] == before["reasoning_steps"] + 1

    events = runtime.recorder.store.events(trace_id=runtime.recorder.trace_id)
    replay_valid = False
    try:
        replay = TraceReplayer().replay(events)
        replay_valid = replay.last_step == len(events)
    except Exception:
        replay_valid = False

    assisted_eval = data["assisted_evaluation"]
    baseline_eval = data["baseline_evaluation"]
    invariant_violations = tuple(assisted_eval["invariant_violations"])
    compared = [
        event
        for event in events
        if event.event_type == EventType.ASSISTED_DECISION_COMPARED
    ]
    if len(compared) != 1:
        replay_valid = False

    return AssistedScenarioResult(
        family=scenario.family,
        baseline_score=float(baseline_eval["score"] or 0.0),
        assisted_score=float(assisted_eval["score"] or 0.0),
        selected_source=RecommendationSource(data["selected_source"]),
        promotion_reason=str(data["promotion_reason"]),
        invariant_violations=invariant_violations,
        state_authority_preserved=state_authority_preserved,
        reasoning_budget_consumed=reasoning_budget_consumed,
        replay_valid=replay_valid,
        model_error=data.get("model_error"),
    )


def run_model_assisted_benchmark(
    root: str | Path,
    config: HarnessConfig,
    *,
    core: ReasoningCore,
) -> ModelAssistedBenchmarkReport:
    output_root = Path(root)
    output_root.mkdir(parents=True, exist_ok=True)
    results = tuple(
        _run_scenario(output_root, config, core, scenario)
        for scenario in SCENARIOS
    )
    baseline_average = sum(item.baseline_score for item in results) / len(results)
    assisted_average = sum(item.assisted_score for item in results) / len(results)
    improved = sum(item.model_improved for item in results)
    selected = sum(
        item.selected_source == RecommendationSource.MODEL for item in results
    )
    violations = sum(len(item.invariant_violations) for item in results)
    return ModelAssistedBenchmarkReport(
        core_name=core.info.name,
        core_version=core.info.version,
        model_name=core.info.model,
        scenarios=results,
        average_baseline_score=baseline_average,
        average_assisted_score=assisted_average,
        model_improved_count=improved,
        model_selected_count=selected,
        baseline_retained_count=len(results) - selected,
        authority_violation_count=violations,
    )
