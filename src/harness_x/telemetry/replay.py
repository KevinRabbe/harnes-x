"""Deterministic reconstruction of authoritative trace state."""

from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from harness_x.core.errors import ReplayError, ReplayMismatchError
from harness_x.core.events import EventType, TraceEvent
from harness_x.core.ids import TraceId

from .trace_store import TraceFixture


class ReplayState(BaseModel):
    """Minimal authoritative projection available before the full orchestrator exists."""

    model_config = ConfigDict(frozen=True)

    trace_id: TraceId
    last_step: int = 0
    tasks: list[str] = Field(default_factory=list)
    goals: dict[str, str] = Field(default_factory=dict)
    modes: dict[str, str] = Field(default_factory=dict)
    memories: list[str] = Field(default_factory=list)
    candidates: dict[str, str] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    budgets: dict[str, dict[str, Any]] = Field(default_factory=dict)


class TraceReplayer:
    """Reduce an ordered event sequence into deterministic authoritative state."""

    def replay(self, events: Iterable[TraceEvent]) -> ReplayState:
        sequence = list(events)
        if not sequence:
            raise ReplayError("cannot replay an empty event sequence")

        trace_id = sequence[0].trace_id
        tasks: set[str] = set()
        goals: dict[str, str] = {}
        modes: dict[str, str] = {}
        memories: set[str] = set()
        candidates: dict[str, str] = {}
        errors: list[str] = []
        budgets: dict[str, dict[str, Any]] = {}
        previous_step = 0
        previous_timestamp = None

        for event in sequence:
            if event.trace_id != trace_id:
                raise ReplayError("event sequence contains multiple trace IDs")
            if event.step != previous_step + 1:
                raise ReplayError(
                    f"expected step {previous_step + 1}, got {event.step}"
                )
            if (
                previous_timestamp is not None
                and event.timestamp < previous_timestamp
            ):
                raise ReplayError(
                    f"timestamp moved backwards at step {event.step}"
                )

            task = str(event.task_id)
            if event.event_type == EventType.TASK_CREATED:
                if task in tasks:
                    raise ReplayError(f"task {task} was created twice")
                tasks.add(task)
            elif task not in tasks:
                raise ReplayError(
                    f"event at step {event.step} references task {task} "
                    "before task_created"
                )

            if event.event_type == EventType.GOAL_CREATED:
                refs = self._refs(event.output_refs, "goal_", "goal_created")
                for ref in refs:
                    if ref in goals:
                        raise ReplayError(f"goal {ref} was created twice")
                    goals[ref] = str(event.metadata.get("status", "active"))

            elif event.event_type == EventType.GOAL_UPDATED:
                refs = self._refs(
                    event.input_refs or event.output_refs,
                    "goal_",
                    "goal_updated",
                )
                for ref in refs:
                    if ref not in goals:
                        raise ReplayError(f"goal {ref} updated before creation")
                    if "status" in event.metadata:
                        goals[ref] = str(event.metadata["status"])

            elif event.event_type == EventType.MODE_CHANGED:
                target = event.metadata.get("to")
                if not isinstance(target, str) or not target.strip():
                    raise ReplayError("mode_changed requires metadata.to")
                modes[task] = target

            elif event.event_type == EventType.MEMORY_WRITTEN:
                for ref in self._refs(
                    event.output_refs,
                    "mem_",
                    "memory_written",
                ):
                    memories.add(ref)

            elif event.event_type == EventType.MEMORY_EVICTED:
                for ref in self._refs(
                    event.input_refs,
                    "mem_",
                    "memory_evicted",
                ):
                    if ref not in memories:
                        raise ReplayError(f"memory {ref} evicted before write")
                    memories.remove(ref)

            elif event.event_type == EventType.BUDGET_CHANGED:
                budget = event.metadata.get("budget")
                if not isinstance(budget, dict):
                    raise ReplayError(
                        "budget_changed requires metadata.budget object"
                    )
                budgets[task] = budget

            elif event.event_type == EventType.CANDIDATE_CREATED:
                for ref in self._refs(
                    event.output_refs,
                    "candidate_",
                    "candidate_created",
                ):
                    if ref in candidates:
                        raise ReplayError(f"candidate {ref} was created twice")
                    candidates[ref] = "created"

            elif event.event_type in {
                EventType.CANDIDATE_EVALUATED,
                EventType.CANDIDATE_PROMOTED,
                EventType.CANDIDATE_REJECTED,
            }:
                refs = self._refs(
                    event.input_refs or event.output_refs,
                    "candidate_",
                    event.event_type.value,
                )
                status = {
                    EventType.CANDIDATE_EVALUATED: "evaluated",
                    EventType.CANDIDATE_PROMOTED: "promoted",
                    EventType.CANDIDATE_REJECTED: "rejected",
                }[event.event_type]
                for ref in refs:
                    if ref not in candidates:
                        raise ReplayError(
                            f"candidate {ref} changed before creation"
                        )
                    candidates[ref] = status

            elif event.event_type == EventType.ERROR_RECORDED:
                code = event.metadata.get("code")
                errors.append(
                    str(code) if code is not None else f"step:{event.step}"
                )

            previous_step = event.step
            previous_timestamp = event.timestamp

        return ReplayState(
            trace_id=trace_id,
            last_step=previous_step,
            tasks=sorted(tasks),
            goals=goals,
            modes=modes,
            memories=sorted(memories),
            candidates=candidates,
            errors=errors,
            budgets=budgets,
        )

    def assert_fixture(self, fixture: TraceFixture) -> ReplayState:
        actual = self.replay(fixture.events)
        actual_dump = actual.model_dump(mode="json")
        if actual_dump != fixture.expected_state:
            raise ReplayMismatchError(
                "replayed final state does not match fixture: "
                f"expected={fixture.expected_state!r}, actual={actual_dump!r}"
            )
        return actual

    @staticmethod
    def _refs(refs: list[str], prefix: str, event_name: str) -> list[str]:
        if not refs:
            raise ReplayError(
                f"{event_name} requires at least one reference"
            )
        if any(not ref.startswith(prefix) for ref in refs):
            raise ReplayError(
                f"{event_name} contains a reference without {prefix!r} prefix"
            )
        return refs
