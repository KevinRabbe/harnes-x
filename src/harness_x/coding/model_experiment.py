"""Secret-free comparable model experiment records for personal Harness X research."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .model_selection import ResolvedModelSelection


def _selection_fingerprint(selection: ResolvedModelSelection) -> str:
    # Hash the complete secret-free resolved selection. The record itself deliberately stores no
    # endpoint, env-var name, prompt, response, credential, workspace, or filesystem path.
    payload = selection.model_dump(mode="json")
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class ModelExperimentRecord(BaseModel):
    """Bounded comparison metadata; never a prompt/response transcript."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["model-experiment-record-v1"] = "model-experiment-record-v1"
    provider: str = Field(min_length=1, max_length=64)
    backend: str = Field(min_length=1, max_length=32)
    profile_id: str | None = Field(default=None, max_length=64)
    model: str = Field(min_length=1, max_length=300)
    capabilities: tuple[str, ...] = Field(default=(), max_length=16)
    selection_fingerprint: str = Field(min_length=64, max_length=64)
    success: bool
    latency_ms: float = Field(ge=0.0, le=86_400_000.0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0.0)
    evaluation_score: float | None = None


def build_model_experiment_record(
    selection: ResolvedModelSelection,
    *,
    success: bool,
    latency_ms: float,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    estimated_cost_usd: float | None = None,
    evaluation_score: float | None = None,
) -> ModelExperimentRecord:
    return ModelExperimentRecord(
        provider=selection.provider.value,
        backend=selection.backend.value,
        profile_id=selection.profile_id,
        model=selection.model,
        capabilities=tuple(item.value for item in selection.capabilities),
        selection_fingerprint=_selection_fingerprint(selection),
        success=success,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimated_cost_usd,
        evaluation_score=evaluation_score,
    )
