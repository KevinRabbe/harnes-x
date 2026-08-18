"""Deterministic maintenance trigger from measurable system pressure."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from harness_x.config import MaintenanceGateConfig
from harness_x.telemetry import TraceRecorder

from .base import DeterministicGate, GateDecision


class MaintenanceRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    working_pressure: float = Field(ge=0.0, le=1.0)
    unresolved_error_count: int = Field(default=0, ge=0)
    repeated_failure_count: int = Field(default=0, ge=0)


class MaintenanceGate(DeterministicGate):
    gate_id = "maintenance"

    def __init__(self, recorder: TraceRecorder, config: MaintenanceGateConfig):
        super().__init__(recorder, policy_version=config.policy_version)
        self.config = config

    def evaluate(self, request: MaintenanceRequest) -> GateDecision:
        reasons: list[str] = []
        if request.working_pressure >= self.config.working_pressure_trigger:
            reasons.append("working_pressure")
        if request.unresolved_error_count >= self.config.unresolved_error_trigger:
            reasons.append("unresolved_errors")
        if request.repeated_failure_count >= self.config.repeated_failure_trigger:
            reasons.append("repeated_failures")

        trigger = bool(reasons)
        return self._record(
            input_state=request,
            decision={
                "trigger": trigger,
                "recommended_mode": "maintenance" if trigger else None,
                "reasons": reasons,
            },
            confidence=1.0,
            cost=0.0,
        )
