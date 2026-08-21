from __future__ import annotations

import hashlib
import json
from pathlib import Path

from harness_x.coding.long_horizon_runtime import (
    LongHorizonContextReasoningCore,
    LongHorizonVerifiedRepositoryCodingTaskRuntime,
)
from harness_x.coding.long_horizon_state import (
    LongHorizonStateStore,
    LongHorizonStateUpdateProposal,
)
from harness_x.coding.verification import FileContainsVerificationCheck, VerificationPlan
from harness_x.reasoning import (
    RawActionProposal,
    RawProposal,
    RawReasoningOutput,
    ReasoningCoreInfo,
)
from harness_x.reasoning.context_builder import ContextBuildResult


class SequenceCore:
    def __init__(self, outputs: list[RawReasoningOutput]) -> None:
        self.outputs = list(outputs)
        self._info = ReasoningCoreInfo(
            name="m27-resume-sequence",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        if not self.outputs:
            raise RuntimeError("sequence exhausted")
        return self.outputs.pop(0)


class CaptureCore:
    def __init__(self) -> None:
        self.last_context = None
        self._info = ReasoningCoreInfo(
            name="m27-capture",
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


def _plan() -> VerificationPlan:
    return VerificationPlan(
        checks=(
            FileContainsVerificationCheck(
                check_id="result_ok",
                name="result contains ok",
                path="result.txt",
                needle="ok",
            ),
        )
    )


def test_second_runtime_process_resumes_same_durable_session(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("result.txt").write_text("bad\n", encoding="utf-8")
    first = LongHorizonVerifiedRepositoryCodingTaskRuntime(
        workspace,
        SequenceCore(
            [
                RawReasoningOutput(
                    status="continue",
                    proposals=(
                        RawProposal(
                            summary="persist architecture decision",
                            payload={
                                "kind": "long_horizon_state_update",
                                "decisions": [
                                    {
                                        "statement": "Keep result.txt as the compatibility surface",
                                        "rationale": "downstream verification already targets it",
                                    }
                                ],
                            },
                        ),
                    ),
                    actions=(
                        RawActionProposal(
                            tool_name="workspace_patch",
                            arguments={
                                "mode": "exact",
                                "path": "result.txt",
                                "old_text": "bad",
                                "new_text": "ok",
                            },
                        ),
                    ),
                ),
                RawReasoningOutput(status="complete"),
            ]
        ),
        tmp_path / "run-1",
        verification_plan=_plan(),
        baseline_verification=False,
    )
    first_report = first.run("Make result.txt contain ok")
    first_session = first_report.long_horizon_session_id
    first_checkpoints = first_report.long_horizon_checkpoint_count

    second = LongHorizonVerifiedRepositoryCodingTaskRuntime(
        workspace,
        SequenceCore([RawReasoningOutput(status="complete")]),
        tmp_path / "run-2",
        verification_plan=_plan(),
        resume_state_path=first_report.long_horizon_state_path,
        baseline_verification=False,
    )
    second_report = second.run("Make result.txt contain ok")

    assert second_report.succeeded is True
    assert second_report.long_horizon_resumed is True
    assert second_report.long_horizon_session_id == first_session
    assert second_report.long_horizon_checkpoint_count > first_checkpoints
    state = second.long_horizon_store.state
    assert state is not None
    assert state.decisions[0].statement == "Keep result.txt as the compatibility surface"
    assert state.evidence_total > first.long_horizon_store.state.evidence_total


def test_long_horizon_context_stays_bounded_with_large_durable_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("app.txt").write_text("ok\n", encoding="utf-8")
    store = LongHorizonStateStore(tmp_path / "state", workspace)
    store.initialize(task="Large task", acceptance_requirements=("remain correct",))

    for group in range(3):
        store.apply_model_update(
            LongHorizonStateUpdateProposal.model_validate(
                {
                    "kind": "long_horizon_state_update",
                    "add_obligations": [
                        {
                            "text": f"obligation-{group}-{index} " + ("O" * 1300),
                            "rationale": "R" * 1200,
                            "priority": 0.9 - (index * 0.01),
                        }
                        for index in range(8)
                    ],
                }
            )
        )
    for index in range(30):
        store.record_evidence(
            kind="stress",
            summary=f"evidence-{index} " + ("E" * 2100),
            source_ref=f"stress:{index}",
            importance=0.6,
            metadata={"detail": "M" * 2500},
        )

    base_payload = {
        "schema_version": "reasoning-context-v1",
        "sections": {"goal": {"task": "Large task"}},
    }
    serialized = json.dumps(base_payload, sort_keys=True, separators=(",", ":"))
    context = ContextBuildResult(
        fingerprint=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        serialized=serialized,
        payload=base_payload,
        char_count=len(serialized),
        dropped_working_items=0,
        dropped_retrieved_items=0,
        dropped_actions=0,
    )
    core = CaptureCore()
    wrapper = LongHorizonContextReasoningCore(core, store, max_total_chars=68_000)

    wrapper.generate(context)

    enriched = core.last_context
    assert enriched is not None
    assert enriched.char_count <= 68_000
    assert "long_horizon_task_state" in enriched.payload["sections"]
    assert "task_state_recall" in enriched.serialized
    assert store.state is not None
    assert len(store.state.obligations) == 24
    assert store.state.evidence_total == 30
