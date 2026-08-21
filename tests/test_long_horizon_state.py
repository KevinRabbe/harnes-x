from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_x.coding.long_horizon_runtime import (
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
from harness_x.tools.long_horizon import (
    TaskStateRecallInput,
    task_state_recall_definition,
)


class SequenceCore:
    def __init__(self, outputs: list[RawReasoningOutput]) -> None:
        self.outputs = list(outputs)
        self.contexts: list[str] = []
        self._info = ReasoningCoreInfo(
            name="m27-sequence-core",
            version="m27-sequence-v1",
            model="deterministic-sequence",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        self.contexts.append(context.serialized)
        if not self.outputs:
            raise RuntimeError("sequence core ran out of outputs")
        return self.outputs.pop(0)


def _store(tmp_path: Path) -> tuple[Path, LongHorizonStateStore]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("app.txt").write_text("initial\n", encoding="utf-8")
    store = LongHorizonStateStore(tmp_path / "state", workspace)
    store.initialize(task="Build the app", acceptance_requirements=("tests pass",))
    return workspace, store


def test_state_update_cannot_rewrite_immutable_task_or_acceptance(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    before = store.state
    assert before is not None

    updated = store.apply_model_update(
        LongHorizonStateUpdateProposal.model_validate(
            {
                "kind": "long_horizon_state_update",
                "strategy": {
                    "current_focus": "Implement authentication",
                    "next_actions": ["inspect auth routes", "add tests"],
                    "risks": ["session compatibility"],
                },
                "add_obligations": [
                    {
                        "text": "Preserve existing login behavior",
                        "rationale": "Regression would break callers",
                        "priority": 0.95,
                    }
                ],
                "decisions": [
                    {
                        "statement": "Reuse the existing session abstraction",
                        "rationale": "Avoid duplicate authority",
                    }
                ],
            }
        )
    )

    assert updated.task == "Build the app"
    assert updated.acceptance_requirements == ("tests pass",)
    assert updated.strategy.current_focus == "Implement authentication"
    assert updated.obligations[0].obligation_id == "obl_000001"
    assert updated.obligations[0].status.value == "open"
    assert updated.decisions[0].decision_id == "decision_000001"
    assert updated.fingerprint != before.fingerprint

    with pytest.raises(ValueError, match="unknown obligation"):
        store.apply_model_update(
            LongHorizonStateUpdateProposal.model_validate(
                {
                    "kind": "long_horizon_state_update",
                    "resolve_obligation_ids": ["obl_999999"],
                }
            )
        )


def test_active_evidence_is_bounded_but_old_ledger_evidence_is_recallable(
    tmp_path: Path,
) -> None:
    _, store = _store(tmp_path)
    old = store.record_evidence(
        kind="design_fact",
        summary="UNIQUE_OLD_DECISION_TOKEN must remain compatible",
        source_ref="test:old",
        importance=0.05,
    )
    for index in range(300):
        store.record_evidence(
            kind="noise",
            summary=f"later evidence {index}",
            source_ref=f"test:{index}",
            importance=0.5,
        )

    state = store.state
    assert state is not None
    assert state.evidence_total == 301
    assert len(state.evidence_index) <= 256
    assert old.evidence_id not in {item.evidence_id for item in state.evidence_index}

    recalled = store.recall(query="UNIQUE_OLD_DECISION_TOKEN", limit=5)
    assert [item.evidence_id for item in recalled] == [old.evidence_id]

    definition = task_state_recall_definition(store)
    output = definition.handler(
        TaskStateRecallInput(query="UNIQUE_OLD_DECISION_TOKEN", limit=5)
    )
    assert output.evidence_total == 301
    assert output.matches[0].evidence_id == old.evidence_id


def test_resume_recomputes_fingerprint_and_requires_checkpoint_workspace_match(
    tmp_path: Path,
) -> None:
    workspace, store = _store(tmp_path)
    checkpoint = store.checkpoint("safe point")
    state_path = store.state_path
    stored_before = json.loads(state_path.read_text(encoding="utf-8"))

    resumed = LongHorizonStateStore(
        tmp_path / "other-output",
        workspace,
        resume_state_path=state_path,
    )
    state = resumed.initialize(
        task="Build the app",
        acceptance_requirements=("tests pass",),
    )
    assert state.resumed is True
    assert state.revision > checkpoint.revision
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["fingerprint"] == state.fingerprint
    assert persisted["fingerprint"] != stored_before["fingerprint"]

    workspace.joinpath("app.txt").write_text("externally changed\n", encoding="utf-8")
    drifted = LongHorizonStateStore(
        tmp_path / "third-output",
        workspace,
        resume_state_path=state_path,
    )
    with pytest.raises(ValueError, match="workspace fingerprint"):
        drifted.initialize(
            task="Build the app",
            acceptance_requirements=("tests pass",),
        )


def test_tampered_state_fails_fingerprint_validation(tmp_path: Path) -> None:
    workspace, store = _store(tmp_path)
    store.checkpoint("safe point")
    payload = json.loads(store.state_path.read_text(encoding="utf-8"))
    payload["task"] = "tampered task"
    store.state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="fingerprint verification failed"):
        LongHorizonStateStore(
            tmp_path / "resume",
            workspace,
            resume_state_path=store.state_path,
        )


def test_append_first_orphan_evidence_is_reconciled_after_restart(tmp_path: Path) -> None:
    workspace, store = _store(tmp_path)
    state_before = store.state_path.read_text(encoding="utf-8")
    evidence = store.record_evidence(
        kind="crash_probe",
        summary="evidence appended before hypothetical crash",
        source_ref="test:crash",
        importance=0.9,
    )
    # Simulate a crash after the fsynced ledger append but before the atomic state update.
    store.state_path.write_text(state_before, encoding="utf-8")

    resumed = LongHorizonStateStore(
        tmp_path / "resume",
        workspace,
        resume_state_path=store.state_path,
        require_resume_workspace_match=False,
    )
    state = resumed.initialize(
        task="Build the app",
        acceptance_requirements=("tests pass",),
    )
    assert state.evidence_total == 1
    assert evidence.evidence_id in {item.evidence_id for item in state.evidence_index}
    assert resumed.recall(query="hypothetical crash")[0].evidence_id == evidence.evidence_id


def test_model_state_proposal_can_accompany_real_edit_without_extra_tool_turn(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("result.txt").write_text("bad\n", encoding="utf-8")
    core = SequenceCore(
        [
            RawReasoningOutput(
                status="continue",
                proposals=(
                    RawProposal(
                        summary="remember the implementation strategy",
                        payload={
                            "kind": "long_horizon_state_update",
                            "strategy": {
                                "current_focus": "repair result and verify",
                                "next_actions": ["patch result", "run verification"],
                                "risks": ["do not lose the acceptance marker"],
                            },
                            "add_obligations": [
                                {
                                    "text": "Keep the final result marker equal to ok",
                                    "rationale": "required by verification",
                                    "priority": 0.95,
                                }
                            ],
                            "decisions": [
                                {
                                    "statement": "Use the existing result.txt file",
                                    "rationale": "smallest compatible change",
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
    )
    runtime = LongHorizonVerifiedRepositoryCodingTaskRuntime(
        workspace,
        core,
        tmp_path / "run",
        verification_plan=VerificationPlan(
            checks=(
                FileContainsVerificationCheck(
                    check_id="result_ok",
                    name="result contains ok",
                    path="result.txt",
                    needle="ok",
                ),
            )
        ),
        baseline_verification=False,
        max_reasoning_steps=5,
        max_tool_actions=12,
    )

    report = runtime.run("Make result.txt contain ok")

    assert report.succeeded is True
    assert report.tool_actions == 2  # one model edit + one software-owned verifier read
    state = runtime.long_horizon_store.state
    assert state is not None
    assert state.strategy.current_focus == "repair result and verify"
    assert state.obligations[0].text == "Keep the final result marker equal to ok"
    assert state.decisions[0].statement == "Use the existing result.txt file"
    assert state.checkpoint_count >= 3
    assert any(item.kind == "tool:workspace_patch" for item in state.evidence_index)
    assert any(item.kind.startswith("verification:") for item in state.evidence_index)
    assert Path(report.long_horizon_state_path).is_file()
    assert Path(report.long_horizon_evidence_path).is_file()
    assert any("long_horizon_task_state" in context for context in core.contexts)
    assert any("task_state_recall" in context for context in core.contexts)
