"""Deterministic write-routing gate for accepted state proposals."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.config import WriteGateConfig
from harness_x.memory import MemoryClass
from harness_x.telemetry import TraceRecorder

from .base import DeterministicGate, GateDecision


class WriteRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: bool
    kind: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    verification_ref: str | None = None

    @field_validator("kind")
    @classmethod
    def normalize_kind(cls, value: str) -> str:
        value = value.strip().casefold()
        if not value:
            raise ValueError("write kind cannot be blank")
        return value

    @field_validator("source_ref")
    @classmethod
    def normalize_source_ref(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source_ref cannot be blank")
        return value


class WriteGate(DeterministicGate):
    gate_id = "write"

    def __init__(self, recorder: TraceRecorder, config: WriteGateConfig):
        super().__init__(recorder, policy_version=config.policy_version)
        self.config = config
        allowed = {memory_class.value for memory_class in MemoryClass}
        configured = set(config.memory_class_by_kind.values()) | {
            config.default_memory_class
        }
        invalid = sorted(configured - allowed)
        if invalid:
            raise ValueError(f"write gate configured unknown memory classes: {invalid}")

    def evaluate(self, request: WriteRequest) -> GateDecision:
        target = self.config.memory_class_by_kind.get(
            request.kind,
            self.config.default_memory_class,
        )
        write = request.accepted
        reason = "accepted" if write else "not_accepted"
        refs = [request.source_ref]
        if request.verification_ref:
            refs.append(request.verification_ref)

        return self._record(
            input_state=request,
            decision={
                "write": write,
                "memory_class": target if write else None,
                "reason": reason,
            },
            input_refs=tuple(refs),
            confidence=1.0,
            cost=0.0,
        )
