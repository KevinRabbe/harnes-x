"""Authoritative task lifecycle controller with zero model dependencies."""

from __future__ import annotations

from pathlib import Path

from harness_x.core.contracts import ComputeBudget
from harness_x.core.errors import (
    BudgetExhaustedError,
    CheckpointError,
    InvalidTransitionError,
    OrchestratorError,
)
from harness_x.core.events import EventType
from harness_x.core.ids import TaskId
from harness_x.telemetry.trace_store import TraceRecorder

from .budgets import BudgetDelta, BudgetUsage, exceeded_dimensions, snapshot_budget
from .modes import OperatingMode, can_transition
from .scheduler import (
    BudgetNotice,
    CheckpointNotice,
    SchedulerHooks,
    TransitionNotice,
)
from .session import CheckpointStore, SessionCheckpoint, TaskSession


class TaskOrchestrator:
    """Sole owner of authoritative lifecycle mutations for one task."""

    COMPONENT = "orchestrator"

    def __init__(
        self,
        session: TaskSession,
        recorder: TraceRecorder,
        *,
        hooks: SchedulerHooks | None = None,
        checkpoint_store: CheckpointStore | None = None,
    ) -> None:
        self._validate_recorder(session, recorder)
        self._session = session
        self.recorder = recorder
        self.hooks = hooks or SchedulerHooks()
        self.checkpoint_store = checkpoint_store or CheckpointStore()

    @property
    def session(self) -> TaskSession:
        return self._session

    @classmethod
    def create(
        cls,
        recorder: TraceRecorder,
        *,
        budget: ComputeBudget | None = None,
        parent_task_id: TaskId | None = None,
        hooks: SchedulerHooks | None = None,
        checkpoint_store: CheckpointStore | None = None,
    ) -> "TaskOrchestrator":
        if recorder.store.events(trace_id=recorder.trace_id):
            raise OrchestratorError("a new task requires an empty trace")
        now = recorder.clock.now()
        session = TaskSession(
            task_id=recorder.task_id,
            trace_id=recorder.trace_id,
            system_version=recorder.system_version,
            budget=budget or ComputeBudget(),
            parent_task_id=parent_task_id,
            created_at=now,
            updated_at=now,
        )
        controller = cls(
            session,
            recorder,
            hooks=hooks,
            checkpoint_store=checkpoint_store,
        )
        recorder.emit(
            EventType.TASK_CREATED,
            cls.COMPONENT,
            metadata={
                "mode": session.mode.value,
                "parent_task_id": (
                    str(parent_task_id) if parent_task_id is not None else None
                ),
                "budget": snapshot_budget(session.budget, session.usage).model_dump(
                    mode="json"
                ),
            },
        )
        return controller

    @classmethod
    def restore(
        cls,
        checkpoint: SessionCheckpoint,
        recorder: TraceRecorder,
        *,
        hooks: SchedulerHooks | None = None,
        checkpoint_store: CheckpointStore | None = None,
    ) -> "TaskOrchestrator":
        session = checkpoint.to_session()
        cls._validate_recorder(session, recorder)
        events = recorder.store.events(trace_id=checkpoint.trace_id)
        current_step = events[-1].step if events else 0
        allowed_steps = {checkpoint.last_event_step}
        if events and events[-1].event_type == EventType.CHECKPOINT_CREATED:
            recorded_step = events[-1].metadata.get("last_event_step")
            if recorded_step == checkpoint.last_event_step:
                allowed_steps.add(checkpoint.last_event_step + 1)
        if current_step not in allowed_steps:
            raise CheckpointError(
                "checkpoint is stale relative to the current trace; refusing to fork history"
            )
        return cls(
            session,
            recorder,
            hooks=hooks,
            checkpoint_store=checkpoint_store,
        )

    @classmethod
    def restore_from_path(
        cls,
        path: str | Path,
        recorder: TraceRecorder,
        *,
        hooks: SchedulerHooks | None = None,
        checkpoint_store: CheckpointStore | None = None,
    ) -> "TaskOrchestrator":
        store = checkpoint_store or CheckpointStore()
        checkpoint = store.load(path)
        return cls.restore(
            checkpoint,
            recorder,
            hooks=hooks,
            checkpoint_store=store,
        )

    @staticmethod
    def _validate_recorder(session: TaskSession, recorder: TraceRecorder) -> None:
        if session.task_id != recorder.task_id:
            raise OrchestratorError("recorder task_id does not match the session")
        if session.trace_id != recorder.trace_id:
            raise OrchestratorError("recorder trace_id does not match the session")
        if session.system_version != recorder.system_version:
            raise OrchestratorError("recorder system_version does not match the session")

    @staticmethod
    def _reason(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("transition/budget reason cannot be blank")
        return normalized

    def start(self, reason: str = "task_started") -> TaskSession:
        return self.transition(OperatingMode.TASK_ACTIVE, reason)

    def enter_verification(self, reason: str) -> TaskSession:
        return self.transition(OperatingMode.VERIFY, reason)

    def enter_recovery(self, reason: str) -> TaskSession:
        return self.transition(OperatingMode.RECOVERY, reason)

    def enter_maintenance(self, reason: str) -> TaskSession:
        return self.transition(OperatingMode.MAINTENANCE, reason)

    def complete(self, reason: str) -> TaskSession:
        return self.transition(OperatingMode.COMPLETE, reason)

    def fail(self, reason: str) -> TaskSession:
        return self.transition(OperatingMode.FAILED, reason)

    def transition(self, target: OperatingMode, reason: str) -> TaskSession:
        if self._session.mode == OperatingMode.SUSPENDED and target != OperatingMode.FAILED:
            raise InvalidTransitionError(
                "suspended tasks must use resume() so the recorded resume mode is enforced"
            )
        return self._transition(target, reason, allow_resume=False)

    def _transition(
        self,
        target: OperatingMode,
        reason: str,
        *,
        allow_resume: bool,
    ) -> TaskSession:
        reason = self._reason(reason)
        source = self._session.mode
        if source == target:
            raise InvalidTransitionError(f"task is already in {target.value}")
        if not can_transition(source, target):
            raise InvalidTransitionError(
                f"illegal transition {source.value} -> {target.value}"
            )
        if source == OperatingMode.SUSPENDED and not allow_resume and target != OperatingMode.FAILED:
            raise InvalidTransitionError("suspended task requires resume()")
        if source == OperatingMode.SUSPENDED and allow_resume:
            if self._session.resume_mode != target:
                raise InvalidTransitionError(
                    f"task may only resume to {self._session.resume_mode}"
                )

        now = self.recorder.clock.now()
        if target == OperatingMode.SUSPENDED:
            resume_mode = source
        else:
            resume_mode = None

        next_session = self._session.model_copy(
            update={
                "mode": target,
                "resume_mode": resume_mode,
                "last_transition_reason": reason,
                "updated_at": now,
            }
        )
        self.recorder.emit(
            EventType.MODE_CHANGED,
            self.COMPONENT,
            metadata={
                "from": source.value,
                "to": target.value,
                "reason": reason,
            },
        )
        self._session = next_session
        self.hooks.notify_transition(
            TransitionNotice(
                task_id=self._session.task_id,
                source=source,
                target=target,
                reason=reason,
                timestamp=now,
            )
        )
        return self._session

    def suspend(
        self,
        reason: str,
        *,
        checkpoint_path: str | Path | None = None,
    ) -> TaskSession:
        session = self.transition(OperatingMode.SUSPENDED, reason)
        if checkpoint_path is not None:
            self.checkpoint(checkpoint_path)
        return session

    def resume(self, reason: str = "task_resumed") -> TaskSession:
        if self._session.mode != OperatingMode.SUSPENDED:
            raise InvalidTransitionError("only a suspended task can resume")
        if self._session.resume_mode is None:
            raise InvalidTransitionError("suspended task has no recorded resume mode")
        target = self._session.resume_mode
        return self._transition(target, reason, allow_resume=True)

    def consume_budget(
        self,
        delta: BudgetDelta,
        *,
        reason: str,
    ) -> TaskSession:
        reason = self._reason(reason)
        if self._session.mode in {
            OperatingMode.READY,
            OperatingMode.COMPLETE,
            OperatingMode.FAILED,
            OperatingMode.SUSPENDED,
        }:
            raise OrchestratorError(
                f"budget cannot be consumed while task is {self._session.mode.value}"
            )

        projected = self._session.usage.projected(delta)
        exceeded = exceeded_dimensions(self._session.budget, projected)
        now = self.recorder.clock.now()
        if exceeded:
            current_snapshot = snapshot_budget(
                self._session.budget,
                self._session.usage,
            )
            self.recorder.emit(
                EventType.BUDGET_CHANGED,
                self.COMPONENT,
                metadata={
                    "budget": current_snapshot.model_dump(mode="json"),
                    "delta": delta.model_dump(mode="json"),
                    "reason": reason,
                    "accepted": False,
                    "exhausted": list(exceeded),
                },
            )
            self.hooks.notify_budget_change(
                BudgetNotice(
                    task_id=self._session.task_id,
                    delta=delta,
                    snapshot=current_snapshot,
                    reason=reason,
                    timestamp=now,
                )
            )
            self._transition(
                OperatingMode.SUSPENDED,
                "budget_exhausted:" + ",".join(exceeded),
                allow_resume=False,
            )
            raise BudgetExhaustedError(
                "budget exhausted: " + ", ".join(exceeded)
            )

        next_session = self._session.model_copy(
            update={"usage": projected, "updated_at": now}
        )
        current_snapshot = snapshot_budget(next_session.budget, projected)
        self.recorder.emit(
            EventType.BUDGET_CHANGED,
            self.COMPONENT,
            metadata={
                "budget": current_snapshot.model_dump(mode="json"),
                "delta": delta.model_dump(mode="json"),
                "reason": reason,
                "accepted": True,
                "exhausted": [],
            },
        )
        self._session = next_session
        self.hooks.notify_budget_change(
            BudgetNotice(
                task_id=self._session.task_id,
                delta=delta,
                snapshot=current_snapshot,
                reason=reason,
                timestamp=now,
            )
        )
        return self._session

    def checkpoint(self, path: str | Path) -> SessionCheckpoint:
        if self._session.mode != OperatingMode.SUSPENDED:
            raise CheckpointError("checkpoint requires a suspended task")
        now = self.recorder.clock.now()
        last_event_step = self.recorder.store.next_step(self._session.trace_id) - 1
        checkpoint = SessionCheckpoint.from_session(
            self._session,
            checkpointed_at=now,
            last_event_step=last_event_step,
        )
        destination = self.checkpoint_store.save(checkpoint, path)
        self.recorder.emit(
            EventType.CHECKPOINT_CREATED,
            self.COMPONENT,
            output_refs=[f"checkpoint:{destination}"],
            metadata={
                "path": str(destination),
                "last_event_step": last_event_step,
                "resume_mode": checkpoint.resume_mode.value,
            },
        )
        self.hooks.notify_checkpoint(
            CheckpointNotice(
                task_id=self._session.task_id,
                path=str(destination),
                last_event_step=last_event_step,
                timestamp=now,
            )
        )
        return checkpoint

    def create_child(
        self,
        child_recorder: TraceRecorder,
        *,
        budget: ComputeBudget | None = None,
        hooks: SchedulerHooks | None = None,
    ) -> "TaskOrchestrator":
        if self._session.is_terminal:
            raise OrchestratorError("terminal tasks cannot create children")
        if child_recorder.task_id == self._session.task_id:
            raise OrchestratorError("child task_id must differ from parent task_id")
        if child_recorder.task_id in self._session.child_task_ids:
            raise OrchestratorError("child task already exists")

        child = TaskOrchestrator.create(
            child_recorder,
            budget=budget or self._session.budget,
            parent_task_id=self._session.task_id,
            hooks=hooks,
            checkpoint_store=self.checkpoint_store,
        )
        now = self.recorder.clock.now()
        next_children = self._session.child_task_ids + (child_recorder.task_id,)
        next_session = self._session.model_copy(
            update={"child_task_ids": next_children, "updated_at": now}
        )
        self.recorder.emit(
            EventType.TASK_CHILD_ADDED,
            self.COMPONENT,
            output_refs=[str(child_recorder.task_id)],
            metadata={
                "child_trace_id": str(child_recorder.trace_id),
            },
        )
        self._session = next_session
        return child
