from __future__ import annotations

import hashlib
import json
from pathlib import Path

from harness_x.coding.procedure_reliability import (
    ProcedureReliabilityPolicy,
    ProcedureReliabilityStore,
)
from harness_x.coding.procedure_reliability_runtime import (
    ProcedureReliabilityContextReasoningCore,
)
from harness_x.coding.project_memory import (
    ProjectMemoryStore,
    ProposedProjectFact,
    ProposedProjectProcedure,
)
from harness_x.reasoning import RawReasoningOutput, ReasoningCoreInfo
from harness_x.reasoning.context_builder import ContextBuildResult


class CaptureCore:
    def __init__(self) -> None:
        self.last_context = None
        self._info = ReasoningCoreInfo(
            name="m29-context-capture",
            version="1",
            model="capture",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        self.last_context = context
        return RawReasoningOutput(status="blocked")


def _episode(store: ProjectMemoryStore, name: str, *, succeeded: bool = True):
    return store.record_episode(
        task=name,
        succeeded=succeeded,
        source_ref=f"context:{name}",
    )


def _context() -> ContextBuildResult:
    payload = {
        "schema_version": "reasoning-context-v1",
        "instruction": "",
        "sections": {"goal": {"task": "Stress bounded project context"}},
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return ContextBuildResult(
        fingerprint=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        serialized=serialized,
        payload=payload,
        char_count=len(serialized),
        dropped_working_items=0,
        dropped_retrieved_items=0,
        dropped_actions=0,
    )


def test_reliability_aware_context_stays_bounded_with_large_memory_and_suspensions(
    tmp_path: Path,
) -> None:
    memory = ProjectMemoryStore(tmp_path, project_key="context-pressure")

    # Large active facts pressure the M28 selected-memory projection while remaining outside
    # M29's procedure gate.
    for index in range(14):
        fact = ProposedProjectFact(
            key=f"large-fact-{index}",
            statement=f"fact-{index} " + ("F" * 2850),
            task_categories=("stress",),
        )
        first = _episode(memory, f"fact-{index}-support-1")
        memory.support_candidates(first, (fact,))
        second = _episode(memory, f"fact-{index}-support-2")
        memory.support_candidates(second, (fact,))

    policy = ProcedureReliabilityPolicy(consecutive_failures_to_suspend=1)
    reliability = ProcedureReliabilityStore(
        tmp_path,
        project_id=memory.project_id,
        policy=policy,
    )

    # Many independently supported procedures are then suspended from one verified failed
    # reuse each. They should be absent from selected project memory but represented by a
    # bounded reliability summary.
    for index in range(18):
        procedure = ProposedProjectProcedure(
            key=f"stress-procedure-{index}",
            statement=f"procedure-{index} " + ("P" * 2600),
            steps=(
                "step-a " + ("A" * 1200),
                "step-b " + ("B" * 1200),
            ),
            task_categories=("stress", "procedure"),
        )
        first = _episode(memory, f"procedure-{index}-support-1")
        memory.support_candidates(first, (procedure,))
        second = _episode(memory, f"procedure-{index}-support-2")
        active = memory.support_candidates(second, (procedure,))[0]
        failed = _episode(memory, f"procedure-{index}-reuse-failed", succeeded=False)
        reliability.record_usage(
            procedure=active,
            episode=failed,
            success=False,
            failure_mode="pressure-test failure " + ("X" * 1400),
        )

    core = CaptureCore()
    wrapper = ProcedureReliabilityContextReasoningCore(
        core,
        memory,
        reliability,
        max_total_chars=76_000,
    )

    wrapper.generate(_context())

    enriched = core.last_context
    assert enriched is not None
    assert enriched.char_count <= 76_000
    assert "project_memory" in enriched.payload["sections"]
    reliability_section = enriched.payload["sections"].get("procedure_reliability")
    assert reliability_section is not None
    data = reliability_section["data"]
    assert data["suspended_count"] == 18
    assert len(data.get("suspended", ())) <= 18
    selected = enriched.payload["sections"]["project_memory"]["data"].get(
        "selected_active_memory", ()
    )
    assert not any(row.get("kind") == "procedure" for row in selected)
