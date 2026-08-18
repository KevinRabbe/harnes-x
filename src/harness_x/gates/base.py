"""Common deterministic gate contracts and causal trace recording."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.core.events import EventType
from harness_x.telemetry import TraceRecorder


class GateDecision(BaseModel):
    """One inspectable, versioned flow-control decision."""

    model_config = ConfigDict(frozen=True)

    gate_id: str = Field(min_length=1)
    decision: dict[str, Any]
    inputs: tuple[str, ...] = ()
    policy_version: str = Field(min_length=1)
    input_fingerprint: str = Field(min_length=64, max_length=64)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    cost: float | None = Field(default=None, ge=0.0)

    @field_validator("gate_id", "policy_version")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("gate identifiers and policy versions cannot be blank")
        return value


def canonical_input_fingerprint(state: BaseModel | dict[str, Any]) -> str:
    """Return a stable SHA-256 fingerprint for a gate input snapshot."""

    payload = (
        state.model_dump(mode="json") if isinstance(state, BaseModel) else state
    )
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class DeterministicGate:
    """Base helper that records decisions but never mutates owned state."""

    gate_id: str

    def __init__(
        self,
        recorder: TraceRecorder,
        *,
        policy_version: str,
    ) -> None:
        self.recorder = recorder
        self.policy_version = policy_version.strip()
        if not self.policy_version:
            raise ValueError("gate policy version cannot be blank")

    def _record(
        self,
        *,
        input_state: BaseModel | dict[str, Any],
        decision: dict[str, Any],
        input_refs: tuple[str, ...] = (),
        confidence: float | None = None,
        cost: float | None = None,
    ) -> GateDecision:
        fingerprint = canonical_input_fingerprint(input_state)
        result = GateDecision(
            gate_id=self.gate_id,
            decision=decision,
            inputs=input_refs,
            policy_version=self.policy_version,
            input_fingerprint=fingerprint,
            confidence=confidence,
            cost=cost,
        )
        self.recorder.emit(
            EventType.GATE_DECISION,
            f"gate.{self.gate_id}",
            input_refs=input_refs,
            metadata={
                "gate_id": result.gate_id,
                "policy_version": result.policy_version,
                "input_fingerprint": result.input_fingerprint,
                "input_state": (
                    input_state.model_dump(mode="json")
                    if isinstance(input_state, BaseModel)
                    else input_state
                ),
                "decision": result.decision,
                "confidence": result.confidence,
                "cost": result.cost,
            },
        )
        return result
