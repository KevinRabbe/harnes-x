"""Milestone 10 fake-to-real reasoning-core swap probe."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from harness_x.config import HarnessConfig
from harness_x.core.contracts import ReasoningRequest
from harness_x.core.ids import RoutineId
from harness_x.core.provenance import SourceKind, VerificationState
from harness_x.orchestrator import OperatingMode
from harness_x.reasoning import (
    RawActionProposal,
    RawReasoningOutput,
    ReasoningCore,
    ReasoningService,
    StubReasoningCore,
)
from harness_x.routines import VerificationRoutineRequest
from harness_x.telemetry import SelfSchemaBuilder, TraceReplayer

from .runtime import BenchmarkRuntime


class ReasoningSwapOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    core_name: str
    core_version: str
    model_name: str
    model_inference: bool
    architecture_signature: str = Field(min_length=64, max_length=64)
    context_fingerprint: str = Field(min_length=64, max_length=64)
    action_proposed: bool
    tool_succeeded: bool
    verification_accepted: bool
    proposal_unverified_model_provenance: bool
    replay_valid: bool
    private_reasoning_recorded: bool
    trace_events: int = Field(ge=0)

    @property
    def passed(self) -> bool:
        return (
            self.action_proposed
            and self.tool_succeeded
            and self.verification_accepted
            and self.proposal_unverified_model_provenance
            and self.replay_valid
            and not self.private_reasoning_recorded
        )


class ReasoningSwapReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "reasoning-swap-v1"
    stub: ReasoningSwapOutcome
    real: ReasoningSwapOutcome
    same_surrounding_architecture: bool

    @property
    def passed(self) -> bool:
        return self.stub.passed and self.real.passed and self.same_surrounding_architecture


def _architecture_signature(schema) -> str:
    payload = {
        "components": [
            item.model_dump(mode="json") for item in schema.components
            if item.kind != "telemetry"
        ],
        "memories": [
            {
                "memory_class": item.memory_class,
                "capacity_units": item.capacity_units,
            }
            for item in schema.memories
        ],
        "gates": [item.model_dump(mode="json") for item in schema.gates],
        "tools": [item.model_dump(mode="json") for item in schema.tools],
        "permissions": list(schema.granted_permissions),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _run_core(
    root: Path,
    config: HarnessConfig,
    *,
    name: str,
    core: ReasoningCore,
) -> ReasoningSwapOutcome:
    runtime = BenchmarkRuntime.create(root, config, name=name, working_capacity=32)
    goal_id = runtime.create_root_goal("Verify replaceable reasoning-core action proposal")
    self_schema = SelfSchemaBuilder(
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
            "reasoning core proposes only; tool execution and verification remain external",
        ),
    ).build()

    request = ReasoningRequest(
        task_id=runtime.recorder.task_id,
        goal_id=goal_id,
        routine_id=RoutineId(value="routine_reasoning_swap_v1"),
        instruction=(
            "Propose the calculator action that multiplies 21 by 2. "
            "Do not claim the result is verified."
        ),
        active_goal=runtime.goals.get(goal_id).model_dump(mode="json"),
        working_state=[item.model_dump(mode="json") for item in runtime.working.items()],
        retrieved_memories=[],
        self_schema=self_schema.model_dump(mode="json"),
        available_actions=[spec.model_dump(mode="json") for spec in runtime.registry.specs()],
        budget=runtime.orchestrator.session.budget,
    )
    result = ReasoningService(runtime.recorder, core).invoke(request)
    action = result.actions[0] if len(result.actions) == 1 else None
    proposed = action is not None
    provenance_ok = bool(
        action is not None
        and action.provenance.source_kind == SourceKind.MODEL
        and action.provenance.verification == VerificationState.UNVERIFIED
    )

    tool_succeeded = False
    accepted = False
    if action is not None:
        tool_result = runtime.executor.execute(
            action,
            routine_allowed_tools=("calculator",),
            granted_permissions=runtime.bindings.tool_permissions,
        )
        tool_succeeded = tool_result.succeeded
        if tool_result.succeeded:
            observation = runtime.executor.observation_from(tool_result)
            runtime.orchestrator.enter_verification("reasoning_swap_tool_result")
            verified = runtime.engine.execute(
                "verification",
                VerificationRoutineRequest(
                    candidate_id=action.candidate_id,
                    actual=tool_result.output,
                    expected={"value": 42.0},
                    provenance=observation.provenance,
                    required_keys=("value",),
                ),
            )
            accepted = bool(verified.result.data.get("accepted"))
            if accepted:
                runtime.orchestrator.transition(
                    OperatingMode.TASK_ACTIVE,
                    "reasoning_swap_verified",
                )

    if runtime.orchestrator.session.mode == OperatingMode.TASK_ACTIVE:
        runtime.finish(goal_id)

    events = runtime.recorder.store.events(trace_id=runtime.recorder.trace_id)
    replay_valid = False
    try:
        replay = TraceReplayer().replay(events)
        replay_valid = (
            replay.last_step == len(events)
            and replay.modes.get(str(runtime.recorder.task_id)) == OperatingMode.COMPLETE.value
        )
    except Exception:
        replay_valid = False

    private_reasoning_recorded = any(
        event.event_type.value == "reasoning_completed"
        and event.metadata.get("private_reasoning_recorded") is not False
        for event in events
    )
    return ReasoningSwapOutcome(
        core_name=core.info.name,
        core_version=core.info.version,
        model_name=core.info.model,
        model_inference=core.info.model_inference,
        architecture_signature=_architecture_signature(self_schema),
        context_fingerprint=result.context_fingerprint or "0" * 64,
        action_proposed=proposed,
        tool_succeeded=tool_succeeded,
        verification_accepted=accepted,
        proposal_unverified_model_provenance=provenance_ok,
        replay_valid=replay_valid,
        private_reasoning_recorded=private_reasoning_recorded,
        trace_events=len(events),
    )


def run_reasoning_swap_probe(
    root: str | Path,
    config: HarnessConfig,
    *,
    real_core: ReasoningCore,
) -> ReasoningSwapReport:
    output_root = Path(root)
    stub_core = StubReasoningCore(
        RawReasoningOutput(
            status="continue",
            actions=(
                RawActionProposal(
                    tool_name="calculator",
                    arguments={"operation": "multiply", "a": 21, "b": 2},
                ),
            ),
        )
    )
    stub = _run_core(output_root / "stub", config, name="reasoning_swap_stub", core=stub_core)
    real = _run_core(output_root / "real", config, name="reasoning_swap_real", core=real_core)
    return ReasoningSwapReport(
        stub=stub,
        real=real,
        same_surrounding_architecture=(
            stub.architecture_signature == real.architecture_signature
        ),
    )
