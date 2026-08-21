"""Read-only model-facing recall over M27 durable task evidence."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness_x.coding.long_horizon_state import LongHorizonStateStore

from .base import SideEffectLevel, ToolDefinition, ToolSpec


class TaskStateRecallInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(default="", max_length=500)
    kinds: tuple[str, ...] = Field(default=(), max_length=16)
    limit: int = Field(default=12, ge=1, le=50)

    @model_validator(mode="after")
    def require_filter(self) -> "TaskStateRecallInput":
        if not self.query.strip() and not any(item.strip() for item in self.kinds):
            raise ValueError("task_state_recall requires query or kinds")
        return self


class TaskStateRecallRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    kind: str
    summary: str
    source_ref: str
    importance: float
    success: bool | None = None
    created_revision: int
    metadata: object = Field(default_factory=dict)


class TaskStateRecallOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    state_revision: int = Field(ge=1)
    state_fingerprint: str = Field(min_length=64, max_length=64)
    evidence_total: int = Field(ge=0)
    matches: tuple[TaskStateRecallRow, ...] = ()


def task_state_recall_definition(store: LongHorizonStateStore) -> ToolDefinition:
    def handler(request: TaskStateRecallInput) -> TaskStateRecallOutput:
        state = store.state
        if state is None:
            raise RuntimeError("long-horizon task state is not initialized")
        matches = store.recall(
            query=request.query,
            kinds=request.kinds,
            limit=request.limit,
        )
        return TaskStateRecallOutput(
            session_id=state.session_id,
            state_revision=state.revision,
            state_fingerprint=state.fingerprint,
            evidence_total=state.evidence_total,
            matches=tuple(
                TaskStateRecallRow.model_validate(item.model_dump(mode="python"))
                for item in matches
            ),
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="task_state_recall",
            version="task-state-recall-v1",
            input_schema=TaskStateRecallInput.model_json_schema(),
            output_schema=TaskStateRecallOutput.model_json_schema(),
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=10.0,
            idempotent=True,
        ),
        input_model=TaskStateRecallInput,
        output_model=TaskStateRecallOutput,
        handler=handler,
    )
