"""Trace-driven scripted routine registry and execution engine."""

from __future__ import annotations

from pydantic import BaseModel

from harness_x.core.events import EventType

from .base import (
    RoutineBindings,
    RoutineError,
    RoutineExecution,
    RoutineExecutionContext,
    RoutineResult,
    RoutineStatus,
    ScriptedRoutine,
    routine_request_fingerprint,
)


class RoutineEngine:
    """Runs versioned procedures while authoritative owners retain state authority."""

    def __init__(self, bindings: RoutineBindings) -> None:
        self.bindings = bindings
        self._routines: dict[str, ScriptedRoutine] = {}
        self._routine_ids: set[str] = set()

    def register(self, routine: ScriptedRoutine) -> None:
        name = routine.spec.name
        routine_id = str(routine.spec.routine_id)
        if name in self._routines:
            raise RoutineError(f"routine name {name!r} is already registered")
        if routine_id in self._routine_ids:
            raise RoutineError(f"routine ID {routine_id!r} is already registered")
        self._routines[name] = routine
        self._routine_ids.add(routine_id)

    def specs(self):
        return tuple(self._routines[name].spec for name in sorted(self._routines))

    def execute(self, routine_name: str, request: BaseModel) -> RoutineExecution:
        try:
            routine = self._routines[routine_name]
        except KeyError as exc:
            raise RoutineError(f"unknown routine {routine_name!r}") from exc

        if not isinstance(request, routine.request_type):
            raise RoutineError(
                f"routine {routine_name!r} requires {routine.request_type.__name__}, "
                f"got {type(request).__name__}"
            )

        current_mode = self.bindings.orchestrator.session.mode
        if current_mode not in routine.spec.precondition_modes:
            allowed = ", ".join(mode.value for mode in routine.spec.precondition_modes)
            raise RoutineError(
                f"routine {routine_name!r} cannot run in {current_mode.value}; "
                f"allowed modes: {allowed}"
            )

        fingerprint = routine_request_fingerprint(request)
        recorder = self.bindings.orchestrator.recorder
        started = recorder.emit(
            EventType.ROUTINE_STARTED,
            f"routine.{routine.spec.name}",
            metadata={
                "routine_id": str(routine.spec.routine_id),
                "routine_name": routine.spec.name,
                "routine_version": routine.spec.version,
                "request_fingerprint": fingerprint,
                "request": request.model_dump(mode="json"),
                "preconditions": [
                    mode.value for mode in routine.spec.precondition_modes
                ],
                "required_state_views": list(routine.spec.required_state_views),
                "allowed_tools": list(routine.spec.allowed_tools),
                "allowed_memory_writes": [
                    memory.value for memory in routine.spec.allowed_memory_writes
                ],
                "step_policy": list(routine.spec.step_policy),
                "verification_requirements": list(
                    routine.spec.verification_requirements
                ),
                "termination_rule": routine.spec.termination_rule,
            },
        )
        context = RoutineExecutionContext(
            self,
            self.bindings,
            routine.spec,
            started_step=started.step,
        )

        try:
            result = routine.run(context, request)
        except Exception as exc:
            recorder.emit(
                EventType.ROUTINE_FINISHED,
                f"routine.{routine.spec.name}",
                metadata={
                    "routine_id": str(routine.spec.routine_id),
                    "routine_version": routine.spec.version,
                    "request_fingerprint": fingerprint,
                    "status": RoutineStatus.FAILED.value,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise

        if not isinstance(result, RoutineResult):
            raise RoutineError(
                f"routine {routine_name!r} returned an invalid result type"
            )

        finished = recorder.emit(
            EventType.ROUTINE_FINISHED,
            f"routine.{routine.spec.name}",
            output_refs=result.output_refs,
            metadata={
                "routine_id": str(routine.spec.routine_id),
                "routine_version": routine.spec.version,
                "request_fingerprint": fingerprint,
                "status": result.status.value,
                "data": result.data,
            },
        )
        return RoutineExecution(
            routine_id=routine.spec.routine_id,
            routine_name=routine.spec.name,
            routine_version=routine.spec.version,
            request_fingerprint=fingerprint,
            started_step=started.step,
            finished_step=finished.step,
            result=result,
        )
