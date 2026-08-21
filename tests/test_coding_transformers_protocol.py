from __future__ import annotations

import json

import pytest
from lmformatenforcer import JsonSchemaParser

from harness_x.reasoning import (
    RawActionProposal,
    RawReasoningOutput,
    ReasoningCoreError,
    TransformersLocalSettings,
)
from harness_x.reasoning.adapters.coding_transformers import (
    CodingTransformersReasoningCore,
    _CodingOutputTruncated,
    coding_protocol_violation,
    coding_reasoning_output_json_schema,
    top_level_json_object_end,
)


def _traverse(payload: dict[str, object]) -> None:
    parser = JsonSchemaParser(coding_reasoning_output_json_schema())
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    for char in text:
        parser = parser.add_character(char)
    assert parser.can_end()


def _base(status: str, actions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": status,
        "actions": actions,
    }


def test_coding_schema_traverses_real_tool_contracts_and_complete_shape() -> None:
    _traverse(
        _base(
            "continue",
            [
                {
                    "tool_name": "workspace_patch",
                    "arguments": {
                        "path": "app.py",
                        "old_text": "return a - b",
                        "new_text": "return a + b",
                        "expected_occurrences": 1,
                    },
                }
            ],
        )
    )
    _traverse(
        _base(
            "continue",
            [
                {
                    "tool_name": "process_run",
                    "arguments": {
                        "argv": ["python", "-m", "pytest", "-q"],
                        "cwd": ".",
                    },
                }
            ],
        )
    )
    _traverse(_base("complete", []))


def test_top_level_json_completion_handles_strings_and_trailing_text() -> None:
    text = '{"status":"continue","actions":[{"tool_name":"workspace_patch","arguments":{"old_text":"}"}}]} trailing'
    end = top_level_json_object_end(text)
    assert end is not None
    assert text[:end].endswith("}")
    assert text[end:] == " trailing"
    assert top_level_json_object_end('{"status":"continue"') is None


def test_software_protocol_rejects_noop_continue() -> None:
    output = RawReasoningOutput(status="continue")
    assert coding_protocol_violation(output) == (
        "status=continue requires exactly one tool action"
    )


def test_software_protocol_rejects_action_bearing_complete() -> None:
    output = RawReasoningOutput(
        status="complete",
        actions=(
            RawActionProposal(tool_name="workspace_list", arguments={"path": "."}),
        ),
    )
    assert coding_protocol_violation(output) == (
        "status=complete requires zero tool actions"
    )


def test_software_protocol_accepts_actionable_continue_and_actionless_complete() -> None:
    actionable = RawReasoningOutput(
        status="continue",
        actions=(
            RawActionProposal(tool_name="workspace_read", arguments={"path": "app.py"}),
        ),
    )
    assert coding_protocol_violation(actionable) is None
    assert coding_protocol_violation(RawReasoningOutput(status="complete")) is None


class RepairFixtureCore(CodingTransformersReasoningCore):
    def __init__(self, outputs: list[RawReasoningOutput | Exception]) -> None:
        super().__init__(TransformersLocalSettings(model="fixture-model"))
        self.outputs = list(outputs)
        self.repair_instructions: list[str | None] = []

    def _ensure_loaded(self) -> None:
        return None

    def _generate_once(self, context, *, repair_instruction):
        self.repair_instructions.append(repair_instruction)
        item = self.outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_coding_core_repairs_one_protocol_violation() -> None:
    core = RepairFixtureCore(
        [
            RawReasoningOutput(status="continue"),
            RawReasoningOutput(status="complete"),
        ]
    )
    output = core.generate(object())
    assert output.status == "complete"
    assert core.repair_instructions[0] is None
    assert "status=continue requires exactly one tool action" in (
        core.repair_instructions[1] or ""
    )


def test_coding_core_repairs_one_token_limit_exhaustion_with_compact_directive() -> None:
    core = RepairFixtureCore(
        [
            _CodingOutputTruncated("generated_tokens=1024, limit=1024"),
            RawReasoningOutput(
                status="continue",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_patch",
                        arguments={
                            "path": "app.py",
                            "old_text": "return a - b",
                            "new_text": "return a + b",
                        },
                    ),
                ),
            ),
        ]
    )
    output = core.generate(object())
    assert output.status == "continue"
    assert output.actions[0].tool_name == "workspace_patch"
    assert "OUTPUT-LIMIT REPAIR REQUIRED" in (core.repair_instructions[1] or "")
    assert "smallest unique" in (core.repair_instructions[1] or "")


def test_coding_core_fails_after_bounded_protocol_repair() -> None:
    core = RepairFixtureCore(
        [
            RawReasoningOutput(status="continue"),
            RawReasoningOutput(status="continue"),
        ]
    )
    with pytest.raises(ReasoningCoreError, match="after bounded repair"):
        core.generate(object())


def test_coding_core_fails_after_bounded_output_limit_repair() -> None:
    core = RepairFixtureCore(
        [
            _CodingOutputTruncated("generated_tokens=1024, limit=1024"),
            _CodingOutputTruncated("generated_tokens=1024, limit=1024"),
        ]
    )
    with pytest.raises(ReasoningCoreError, match="exhausted max_new_tokens"):
        core.generate(object())


def test_coding_transformers_core_is_lazy_and_declares_coding_identity() -> None:
    core = CodingTransformersReasoningCore(
        TransformersLocalSettings(model="Qwen/Qwen3-4B-Instruct-2507")
    )
    assert core.info.name == "transformers_local_coding"
    assert core.info.version == "transformers-local-coding-v3"
    assert core.info.transport == "in_process_transformers"
    assert core.info.model_inference is True
