"""Deterministic focus ranking and pin proposals for working state."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from harness_x.config import FocusGateConfig
from harness_x.core.ids import MemoryId
from harness_x.telemetry import TraceRecorder

from .base import DeterministicGate, GateDecision


class FocusCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory_id: MemoryId
    priority: float = Field(ge=0.0, le=1.0)
    pinned: bool = False
    created_step: int = Field(ge=1)
    last_used_step: int = Field(ge=1)


class FocusRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidates: tuple[FocusCandidate, ...] = ()


class FocusGate(DeterministicGate):
    gate_id = "focus"

    def __init__(self, recorder: TraceRecorder, config: FocusGateConfig):
        super().__init__(recorder, policy_version=config.policy_version)
        self.config = config

    def evaluate(self, request: FocusRequest) -> GateDecision:
        ranked = sorted(
            request.candidates,
            key=lambda item: (
                not item.pinned,
                -item.priority,
                -item.last_used_step,
                item.created_step,
                str(item.memory_id),
            ),
        )
        focused = ranked[: self.config.max_focus_items]
        proposed_pins = [
            item
            for item in focused
            if not item.pinned
            and item.priority >= self.config.pin_priority_threshold
        ][: self.config.max_auto_pins]

        return self._record(
            input_state=request,
            decision={
                "focus_order": [str(item.memory_id) for item in focused],
                "proposed_pin_ids": [
                    str(item.memory_id) for item in proposed_pins
                ],
                "candidate_count": len(request.candidates),
            },
            input_refs=tuple(str(item.memory_id) for item in request.candidates),
            confidence=1.0,
            cost=0.0,
        )
