"""Software-owned reasoning invocation, normalization, provenance, and tracing."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from harness_x.core.contracts import (
    ActionProposal,
    Proposal,
    ReasoningRequest,
    ReasoningResult,
)
from harness_x.core.events import EventType
from harness_x.core.ids import CandidateId
from harness_x.core.provenance import Provenance, SourceKind, VerificationState
from harness_x.telemetry.trace_store import TraceRecorder

from .base import RawReasoningOutput, ReasoningCore
from .context_builder import BoundedContextBuilder, ContextBuildResult


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _candidate_id(
    *,
    context_fingerprint: str,
    core_identity: str,
    kind: str,
    index: int,
    payload: dict[str, Any],
) -> CandidateId:
    material = _canonical(
        {
            "context_fingerprint": context_fingerprint,
            "core_identity": core_identity,
            "kind": kind,
            "index": index,
            "payload": payload,
        }
    ).encode("utf-8")
    return CandidateId(value=f"candidate_{hashlib.sha256(material).hexdigest()[:32]}")


class ReasoningService:
    """The authoritative boundary around a replaceable reasoning core.

    The core never allocates system IDs, provenance, verification state, or mutations.
    This service converts untrusted structured model output into proposals and records
    only bounded input/output metadata, never private chain-of-thought text.
    """

    def __init__(
        self,
        recorder: TraceRecorder,
        core: ReasoningCore,
        *,
        context_builder: BoundedContextBuilder | None = None,
    ) -> None:
        self.recorder = recorder
        self.core = core
        self.context_builder = context_builder or BoundedContextBuilder()

    def invoke(self, request: ReasoningRequest) -> ReasoningResult:
        if request.task_id != self.recorder.task_id:
            raise ValueError("reasoning request belongs to another task")

        context = self.context_builder.build(request)
        info = self.core.info
        started = self.recorder.emit(
            EventType.REASONING_REQUESTED,
            "reasoning.service",
            input_refs=(str(request.goal_id), str(request.routine_id)),
            metadata={
                "core": info.model_dump(mode="json"),
                "context_fingerprint": context.fingerprint,
                "context_chars": context.char_count,
                "dropped_working_items": context.dropped_working_items,
                "dropped_retrieved_items": context.dropped_retrieved_items,
                "dropped_actions": context.dropped_actions,
                "self_schema_reduced": context.self_schema_reduced,
            },
        )
        raw = self.core.generate(context)
        result = self._normalize(request, context, raw)
        self.recorder.emit(
            EventType.REASONING_COMPLETED,
            "reasoning.service",
            input_refs=(str(started.event_id),),
            output_refs=tuple(
                [str(item.candidate_id) for item in result.proposals]
                + [str(item.candidate_id) for item in result.actions]
            ),
            metadata={
                "core": info.model_dump(mode="json"),
                "context_fingerprint": context.fingerprint,
                "status": result.status,
                "proposal_count": len(result.proposals),
                "action_count": len(result.actions),
                "observation_count": len(result.observations),
                "requested_additional_steps": result.requested_additional_steps,
                "model_inference": info.model_inference,
                "private_reasoning_recorded": False,
            },
        )
        return result

    def _normalize(
        self,
        request: ReasoningRequest,
        context: ContextBuildResult,
        raw: RawReasoningOutput,
    ) -> ReasoningResult:
        info = self.core.info
        core_identity = f"{info.name}:{info.version}:{info.model}:{info.transport}"
        provenance = Provenance(
            source_kind=SourceKind.MODEL,
            source_ref=f"reasoning:{core_identity}",
            created_at=self.recorder.clock.now(),
            system_version=self.recorder.system_version,
            trace_id=self.recorder.trace_id,
            verification=VerificationState.UNVERIFIED,
        )

        proposals: list[Proposal] = []
        for index, item in enumerate(raw.proposals):
            payload = item.model_dump(mode="json")
            proposals.append(
                Proposal(
                    candidate_id=_candidate_id(
                        context_fingerprint=context.fingerprint,
                        core_identity=core_identity,
                        kind="proposal",
                        index=index,
                        payload=payload,
                    ),
                    task_id=request.task_id,
                    summary=item.summary,
                    payload=item.payload,
                    provenance=provenance,
                )
            )

        actions: list[ActionProposal] = []
        for index, item in enumerate(raw.actions):
            payload = item.model_dump(mode="json")
            actions.append(
                ActionProposal(
                    candidate_id=_candidate_id(
                        context_fingerprint=context.fingerprint,
                        core_identity=core_identity,
                        kind="action",
                        index=index,
                        payload=payload,
                    ),
                    task_id=request.task_id,
                    tool_name=item.tool_name,
                    arguments=item.arguments,
                    provenance=provenance,
                )
            )

        return ReasoningResult(
            task_id=request.task_id,
            status=raw.status,
            proposals=proposals,
            actions=actions,
            observations=list(raw.observations),
            requested_additional_steps=raw.requested_additional_steps,
            core_name=info.name,
            core_version=info.version,
            model_name=info.model,
            model_inference=info.model_inference,
            context_fingerprint=context.fingerprint,
        )
