from __future__ import annotations

import hashlib
import json
from pathlib import Path

from harness_x.coding.procedure_reliability import ProcedureReliabilityPolicy, ProcedureReliabilityStore
from harness_x.coding.procedure_revision import ProcedureRevisionProposal, ProcedureRevisionStore
from harness_x.coding.procedure_revision_runtime import ProcedureRevisionContextReasoningCore
from harness_x.coding.project_memory import ProjectMemoryStore, ProposedProjectFact, ProposedProjectProcedure
from harness_x.reasoning import RawReasoningOutput, ReasoningCoreInfo
from harness_x.reasoning.context_builder import ContextBuildResult


class CaptureCore:
    def __init__(self) -> None:
        self.last_context = None
        self._info = ReasoningCoreInfo(
            name="m30-context-capture",
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
    return store.record_episode(task=name, succeeded=succeeded, source_ref=f"m30-context:{name}")


def _context() -> ContextBuildResult:
    payload = {
        "schema_version": "reasoning-context-v1",
        "instruction": "stress revision procedure candidate context",
        "sections": {"goal": {"task": "Stress bounded M30 revision context"}},
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


def test_revision_context_stays_bounded_and_unpromoted_replacements_stay_hidden(tmp_path: Path) -> None:
    memory = ProjectMemoryStore(tmp_path, project_key="m30-context-pressure")

    for index in range(10):
        fact = ProposedProjectFact(
            key=f"revision-pressure-fact-{index}",
            statement=f"revision procedure fact {index} " + ("F" * 2800),
            task_categories=("revision", "stress"),
        )
        first = _episode(memory, f"fact-{index}-support-1")
        memory.support_candidates(first, (fact,))
        second = _episode(memory, f"fact-{index}-support-2")
        memory.support_candidates(second, (fact,))

    reliability = ProcedureReliabilityStore(
        tmp_path,
        project_id=memory.project_id,
        policy=ProcedureReliabilityPolicy(consecutive_failures_to_suspend=1),
    )
    revisions = ProcedureRevisionStore(tmp_path, project_id=memory.project_id)

    for parent_index in range(3):
        parent_proposal = ProposedProjectProcedure(
            key=f"revision-pressure-parent-{parent_index}",
            statement=f"revision procedure parent {parent_index} " + ("P" * 2200),
            steps=("old step " + ("O" * 900),),
            task_categories=("revision", "stress"),
        )
        first = _episode(memory, f"parent-{parent_index}-support-1")
        memory.support_candidates(first, (parent_proposal,))
        second = _episode(memory, f"parent-{parent_index}-support-2")
        parent = memory.support_candidates(second, (parent_proposal,))[0]
        failed = _episode(memory, f"parent-{parent_index}-failed", succeeded=False)
        suspended = reliability.record_usage(procedure=parent, episode=failed, success=False)

        for candidate_index in range(4):
            revisions.propose(
                proposal=ProcedureRevisionProposal(
                    parent_procedure_id=parent.entry_id,
                    statement=(
                        f"revision candidate {parent_index}-{candidate_index} "
                        + (chr(65 + candidate_index) * 2500)
                    ),
                    steps=(
                        f"new step {candidate_index} " + ("S" * 1200),
                        "verify isolated outcome " + ("V" * 900),
                    ),
                    task_categories=("revision", "stress"),
                    rationale=(
                        f"candidate {candidate_index} addresses verified failure "
                        + ("R" * 2000)
                    ),
                ),
                parent=parent,
                origin_episode=failed,
                reliability=suspended,
            )

    core = CaptureCore()
    wrapper = ProcedureRevisionContextReasoningCore(
        core,
        memory,
        reliability,
        revisions,
        allow_revision_trials=True,
        max_total_chars=76_000,
    )

    wrapper.generate(_context())

    enriched = core.last_context
    assert enriched is not None
    assert enriched.char_count <= 76_000
    assert "procedure_revision" in enriched.payload["sections"]
    revision_section = enriched.payload["sections"]["procedure_revision"]
    assert revision_section["trial_allowed"] is True
    data = revision_section["data"]
    assert data["candidate_counts"]["candidate"] == 12
    assert len(data.get("open_candidates", ())) <= 12
    selected = enriched.payload["sections"]["project_memory"]["data"].get(
        "selected_active_memory", ()
    )
    assert not any(str(row.get("key", "")).startswith("hx-revision/") for row in selected)
