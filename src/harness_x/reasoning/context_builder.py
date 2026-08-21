"""Deterministic bounded conversion from authoritative state to model context."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from harness_x.core.contracts import ReasoningRequest

from .base import ReasoningCoreError


class ContextBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_chars: int = Field(default=24_000, ge=1_024)
    max_working_items: int = Field(default=16, ge=0)
    max_retrieved_items: int = Field(default=12, ge=0)
    max_available_actions: int = Field(default=16, ge=0)


class ContextBuildResult(BaseModel):
    """Portable bounded context passed to a reasoning adapter."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "reasoning-context-v1"
    fingerprint: str = Field(min_length=64, max_length=64)
    serialized: str
    payload: dict[str, Any]
    char_count: int = Field(ge=0)
    dropped_working_items: int = Field(ge=0)
    dropped_retrieved_items: int = Field(ge=0)
    dropped_actions: int = Field(ge=0)
    self_schema_reduced: bool = False


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _stable_item_key(item: dict[str, Any]) -> tuple[str, str]:
    identifier = str(
        item.get("memory_id")
        or item.get("goal_id")
        or item.get("tool_name")
        or item.get("name")
        or ""
    )
    return identifier, _canonical(item)


def _working_key(item: dict[str, Any]) -> tuple[int, float, str, str]:
    pinned = 1 if bool(item.get("pinned")) else 0
    try:
        priority = float(item.get("priority", 0.0))
    except (TypeError, ValueError):
        priority = 0.0
    stable = _stable_item_key(item)
    return (-pinned, -priority, stable[0], stable[1])


class BoundedContextBuilder:
    """Build deterministic model input without exposing complete memory stores."""

    def __init__(self, budget: ContextBudget | None = None) -> None:
        self.budget = budget or ContextBudget()

    def build(self, request: ReasoningRequest) -> ContextBuildResult:
        working_all = sorted(
            (dict(item) for item in request.working_state),
            key=_working_key,
        )
        retrieved_all = sorted(
            (dict(item) for item in request.retrieved_memories),
            key=_stable_item_key,
        )
        actions_all = sorted(
            (dict(item) for item in request.available_actions),
            key=_stable_item_key,
        )

        working = working_all[: self.budget.max_working_items]
        retrieved = retrieved_all[: self.budget.max_retrieved_items]
        actions = actions_all[: self.budget.max_available_actions]
        reduced_schema = False

        payload = self._payload(
            request,
            working=working,
            retrieved=retrieved,
            actions=actions,
            self_schema=dict(request.self_schema),
        )
        serialized = _canonical(payload)

        # Reduce lowest-priority context first. We always preserve goal, routine,
        # budget, instruction, and at least the declared self-schema identity.
        while len(serialized) > self.budget.max_chars and retrieved:
            retrieved.pop()
            payload = self._payload(
                request,
                working=working,
                retrieved=retrieved,
                actions=actions,
                self_schema=dict(request.self_schema),
            )
            serialized = _canonical(payload)

        while len(serialized) > self.budget.max_chars and working:
            working.pop()
            payload = self._payload(
                request,
                working=working,
                retrieved=retrieved,
                actions=actions,
                self_schema=dict(request.self_schema),
            )
            serialized = _canonical(payload)

        while len(serialized) > self.budget.max_chars and len(actions) > 1:
            actions.pop()
            payload = self._payload(
                request,
                working=working,
                retrieved=retrieved,
                actions=actions,
                self_schema=dict(request.self_schema),
            )
            serialized = _canonical(payload)

        if len(serialized) > self.budget.max_chars and request.self_schema:
            reduced_schema = True
            self_schema = {
                key: request.self_schema[key]
                for key in (
                    "schema_version",
                    "system_version",
                    "operating_mode",
                    "state_fingerprint",
                    "reasoning_core",
                    "known_limitations",
                )
                if key in request.self_schema
            }
            payload = self._payload(
                request,
                working=working,
                retrieved=retrieved,
                actions=actions,
                self_schema=self_schema,
            )
            serialized = _canonical(payload)

        if len(serialized) > self.budget.max_chars:
            raise ReasoningCoreError(
                "reasoning context cannot fit the configured character budget "
                "without dropping governing state"
            )

        fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return ContextBuildResult(
            fingerprint=fingerprint,
            serialized=serialized,
            payload=payload,
            char_count=len(serialized),
            dropped_working_items=len(working_all) - len(working),
            dropped_retrieved_items=len(retrieved_all) - len(retrieved),
            dropped_actions=len(actions_all) - len(actions),
            self_schema_reduced=reduced_schema,
        )

    @staticmethod
    def _payload(
        request: ReasoningRequest,
        *,
        working: list[dict[str, Any]],
        retrieved: list[dict[str, Any]],
        actions: list[dict[str, Any]],
        self_schema: dict[str, Any],
    ) -> dict[str, Any]:
        sections: dict[str, Any] = {
            "active_goal": {
                "authority": "authoritative",
                "data": request.active_goal or request.active_state,
            },
            "working_state": {
                "authority": "authoritative",
                "items": working,
            },
            "retrieved_memories": {
                "authority": "evidence_with_individual_verification_state",
                "rule": (
                    "Candidate claims, hypotheses, suspected causes, and unverified "
                    "observations are not facts merely because they are present here."
                ),
                "items": retrieved,
            },
            "self_schema": {
                "authority": "authoritative_runtime_description",
                "data": self_schema,
            },
            "available_actions": {
                "authority": "declared_capabilities_not_execution_authority",
                "items": actions,
            },
            "compute_budget": {
                "authority": "externally_enforced",
                "data": request.budget.model_dump(mode="json"),
            },
            "legacy_context": {
                "authority": "caller_supplied_context",
                "data": request.context,
            },
        }

        # Earlier fixtures sometimes supplied runtime state through ``active_state``
        # instead of ``active_goal``. Preserve that historical serialized shape when
        # only one is present. When both are provided, however, they are independent
        # authoritative views and must both reach the reasoning core. Coding tasks use
        # this second view for controller-owned progress and verification state.
        if request.active_goal and request.active_state:
            sections["active_state"] = {
                "authority": "authoritative_runtime_state",
                "data": request.active_state,
            }

        return {
            "schema_version": "reasoning-context-v1",
            "task_id": str(request.task_id),
            "goal_id": str(request.goal_id),
            "routine_id": str(request.routine_id),
            "instruction": request.instruction,
            "sections": sections,
        }
