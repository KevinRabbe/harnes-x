from __future__ import annotations

import hashlib
import json
from pathlib import Path

from harness_x.coding.project_memory import (
    ProjectMemoryEntryState,
    ProjectMemoryStore,
    ProposedProjectFact,
    ProposedProjectProcedure,
)
from harness_x.coding.project_memory_runtime import ProjectMemoryContextReasoningCore
from harness_x.reasoning import RawProposal, RawReasoningOutput, ReasoningCoreInfo
from harness_x.reasoning.context_builder import ContextBuildResult


class NoopCore:
    def __init__(self) -> None:
        self.last_context = None
        self._info = ReasoningCoreInfo(
            name="m28-hardening",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        self.last_context = context
        return RawReasoningOutput(status="blocked")


def _episode(store: ProjectMemoryStore, name: str):
    return store.record_episode(
        task=name,
        succeeded=True,
        source_ref=f"test:{name}",
        verification_refs=(f"verification:{name}",),
    )


def _procedure(*, categories=("python",), statement="Run targeted tests before the full suite"):
    return ProposedProjectProcedure(
        key="test-order",
        statement=statement,
        steps=("Run targeted pytest", "Run full pytest"),
        task_categories=categories,
    )


def test_same_verified_episode_cannot_count_twice_toward_promotion(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path / "memory", project_key="project")
    episode = _episode(store, "one")
    first = store.support_candidates(episode, (_procedure(),))[0]
    second = store.support_candidates(episode, (_procedure(),))[0]

    assert first.entry_id == second.entry_id
    assert second.support_count == 1
    assert second.state == ProjectMemoryEntryState.CANDIDATE


def test_candidate_identity_ignores_categories_and_presentation_only_differences(
    tmp_path: Path,
) -> None:
    store = ProjectMemoryStore(tmp_path / "memory", project_key="project")
    first = store.support_candidates(
        _episode(store, "one"),
        (_procedure(categories=("python", "unit-test")),),
    )[0]
    second = store.support_candidates(
        _episode(store, "two"),
        (
            ProposedProjectProcedure(
                key="TEST-ORDER",
                statement="  run   TARGETED tests before the FULL suite ",
                steps=(" run TARGETED pytest ", "RUN full pytest"),
                task_categories=("regression", "python"),
            ),
        ),
    )[0]

    assert second.entry_id == first.entry_id
    assert second.state == ProjectMemoryEntryState.ACTIVE
    assert second.support_count == 2
    assert set(second.task_categories) == {"python", "unit-test", "regression"}
    assert second.conflicts_with == ()


def test_usage_is_recorded_before_new_candidate_can_suspend_the_used_procedure(
    tmp_path: Path,
) -> None:
    store = ProjectMemoryStore(tmp_path / "memory", project_key="project")
    for name in ("one", "two"):
        active = store.support_candidates(_episode(store, name), (_procedure(),))[0]
    assert active.state == ProjectMemoryEntryState.ACTIVE

    wrapper = ProjectMemoryContextReasoningCore(NoopCore(), store)
    wrapper._consume_project_proposals(
        RawReasoningOutput(
            status="continue",
            proposals=(
                RawProposal(
                    summary="used old procedure and learned conflicting evidence",
                    payload={
                        "kind": "project_memory_update",
                        "used_procedure_ids": [active.entry_id],
                        "candidates": [
                            {
                                "kind": "procedure",
                                "key": "test-order",
                                "statement": "Run only the full suite and skip targeted tests",
                                "steps": ["Run full pytest only"],
                                "task_categories": ["python"],
                            }
                        ],
                    },
                ),
            ),
        )
    )
    wrapper.finalize_task(
        task="conflict-producing verified task",
        succeeded=True,
        source_ref="test:verified-report",
        long_horizon_session_id=None,
        long_horizon_state_fingerprint=None,
        workspace_fingerprint=None,
        changed_files=(),
        verification_refs=("verification:pass",),
        failure_mode=None,
    )

    previous = next(item for item in store.state.entries if item.entry_id == active.entry_id)
    assert previous.usage_count == 1
    assert previous.success_count == 1
    assert previous.state == ProjectMemoryEntryState.CONFLICTED
    assert store.active_entries() == ()


def test_project_memory_context_remains_bounded_under_many_large_active_entries(
    tmp_path: Path,
) -> None:
    store = ProjectMemoryStore(tmp_path / "memory", project_key="project")
    candidates = tuple(
        ProposedProjectFact(
            key=f"project-convention-{index}",
            statement=f"Project convention {index}: " + ("E" * 2400),
            task_categories=("project", "convention"),
        )
        for index in range(20)
    )
    store.support_candidates(_episode(store, "one"), candidates)
    store.support_candidates(_episode(store, "two"), candidates)
    assert len(store.active_entries()) == 20

    payload = {
        "schema_version": "reasoning-context-v1",
        "instruction": "Inspect project convention requirements",
        "sections": {"goal": {"task": "project convention task"}},
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    context = ContextBuildResult(
        fingerprint=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        serialized=serialized,
        payload=payload,
        char_count=len(serialized),
        dropped_working_items=0,
        dropped_retrieved_items=0,
        dropped_actions=0,
    )
    core = NoopCore()
    wrapper = ProjectMemoryContextReasoningCore(core, store, max_total_chars=12_000)

    wrapper.generate(context)

    assert core.last_context is not None
    assert core.last_context.char_count <= 12_000
    assert "project_memory" in core.last_context.payload["sections"]
    assert "project_memory_recall" in core.last_context.serialized
