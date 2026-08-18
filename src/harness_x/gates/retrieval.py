"""Deterministic retrieval gate over explicit task-state signals."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.config import RetrievalGateConfig
from harness_x.telemetry import TraceRecorder

from .base import DeterministicGate, GateDecision


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_routine: str = Field(min_length=1)
    unresolved_entities: tuple[str, ...] = ()
    uncertainty: bool = False
    working_pressure: float = Field(ge=0.0, le=1.0)
    recent_retrieval_count: int = Field(default=0, ge=0)
    query: str | None = None

    @field_validator("current_routine")
    @classmethod
    def normalize_routine(cls, value: str) -> str:
        value = value.strip().casefold()
        if not value:
            raise ValueError("current_routine cannot be blank")
        return value


class RetrievalGate(DeterministicGate):
    gate_id = "retrieval"

    def __init__(self, recorder: TraceRecorder, config: RetrievalGateConfig):
        super().__init__(recorder, policy_version=config.policy_version)
        self.config = config

    def evaluate(self, request: RetrievalRequest) -> GateDecision:
        routine_forces = request.current_routine in {
            routine.casefold() for routine in self.config.always_retrieve_routines
        }
        unresolved = bool(request.unresolved_entities)
        pressure_suppresses = (
            request.working_pressure >= self.config.pressure_suppress_threshold
        )
        recent_limit_hit = (
            request.recent_retrieval_count >= self.config.max_recent_retrievals
        )

        retrieve = routine_forces or unresolved or request.uncertainty
        reason = "need_signal" if retrieve else "no_need_signal"

        if not retrieve and pressure_suppresses:
            reason = "working_pressure"
        elif not retrieve and recent_limit_hit:
            reason = "recent_retrieval_limit"

        targets: list[str] = []
        if retrieve:
            targets.append("episodic")
            if request.current_routine in {"recovery", "debugging"} or request.uncertainty:
                targets.append("error")
            if request.current_routine in {"planning", "research"}:
                targets.append("goal")

        return self._record(
            input_state=request,
            decision={
                "retrieve": retrieve,
                "reason": reason,
                "targets": targets,
                "limit": self.config.default_limit if retrieve else 0,
                "query": request.query,
            },
            input_refs=tuple(request.unresolved_entities),
            confidence=1.0,
            cost=0.0,
        )
