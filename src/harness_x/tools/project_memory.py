"""Read-only model-facing recall over M28 project-scoped memory."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness_x.coding.project_memory import ProjectMemoryRecallRow, ProjectMemoryStore

from .base import SideEffectLevel, ToolDefinition, ToolSpec


class ProjectMemoryRecallInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(default="", max_length=1000)
    kinds: tuple[str, ...] = Field(default=(), max_length=4)
    include_candidates: bool = False
    limit: int = Field(default=12, ge=1, le=50)

    @model_validator(mode="after")
    def require_filter(self) -> "ProjectMemoryRecallInput":
        if not self.query.strip() and not any(item.strip() for item in self.kinds):
            raise ValueError("project_memory_recall requires query or kinds")
        return self


class ProjectMemoryRecallOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    state_revision: int = Field(ge=1)
    state_fingerprint: str = Field(min_length=64, max_length=64)
    episode_count: int = Field(ge=0)
    matches: tuple[ProjectMemoryRecallRow, ...] = ()


def project_memory_recall_definition(store: ProjectMemoryStore) -> ToolDefinition:
    def handler(request: ProjectMemoryRecallInput) -> ProjectMemoryRecallOutput:
        matches = store.recall(
            query=request.query,
            kinds=request.kinds,
            include_candidates=request.include_candidates,
            limit=request.limit,
        )
        return ProjectMemoryRecallOutput(
            project_id=store.state.project_id,
            state_revision=store.state.revision,
            state_fingerprint=store.state.fingerprint,
            episode_count=store.state.episode_count,
            matches=matches,
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="project_memory_recall",
            version="project-memory-recall-v1",
            input_schema=ProjectMemoryRecallInput.model_json_schema(),
            output_schema=ProjectMemoryRecallOutput.model_json_schema(),
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=10.0,
            idempotent=True,
        ),
        input_model=ProjectMemoryRecallInput,
        output_model=ProjectMemoryRecallOutput,
        handler=handler,
    )
