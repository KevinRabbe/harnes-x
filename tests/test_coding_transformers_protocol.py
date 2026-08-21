from __future__ import annotations

import copy

import pytest

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
from harness_x.training.lmfe_compat import assert_lmfe_schema_supported


def test_coding_output_schema_uses_real_tool_input_contracts() -> None:
    schema = coding_reasoning_output_json_schema()
    branches = schema["properties"]["actions"]["items"]["anyOf"]
    by_name = {
        branch["properties"]["tool_name"]["enum"][0]: branch
        for branch in branches
    }

    assert set(by_name) == {
        "workspace_list",
        "workspace_read",
        "workspace_search",
        "workspace_write",
        "workspace_patch",
        "process_run",
    }
    patch_args = by_name["workspace_patch"]["properties"]["arguments"]
    assert set(patch_args["required"]) == {
        "path",
        "old_text",
        "new_text",
    }
    assert patch_args["additionalProperties"] is False
    process_args = by_name["process_run"]["properties"]["arguments"]
    assert "argv" in process_args["required"]
    assert process_args["additionalProperties"] is False


def test_coding_output_schema_real_lmfe_traversal() -> None:
    assert_lmfe_schema_supported(coding_reasoning_output_json_schema())


def test_coding_protocol_rejects_noop_continue() -> None:
    output = RawReasoningOutput(status="continue")
    assert coding_protocol_violation(output) == (
        "status=continue requires exactly one tool action"
    )


def test_coding_protocol_rejects_action_bearing_complete() -> None:
    output = RawReasoningOutput(
        status="complete",
        actions=(
            RawActionProposal(
                tool_name="workspace_read",
                arguments={"path": "app.py"},
            ),
        ),
    )
    assert coding_protocol_violation(output) == (
        "status=complete requires zero tool actions"
    )


def test_coding_protocol_accepts_actionable_continue() -> None:
    output = RawReasoningOutput(
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
    )
    assert coding_protocol_violation(output) is None


def test_top_level_json_object_end_handles_nested_and_escaped_strings() -> None:
    text = ' {"a":{"b":"x}\\\"y"},"c":[1,2]} trailing'
    end = top_level_json_object_end(text)
    assert end is not None
    assert text[:end].strip() == '{"a":{"b":"x}\\\"y"},"c":[1,2]}'


def test_top_level_json_object_end_rejects_incomplete_string() -> None:
    assert top_level_json_object_end('{"status":"cont') is None


class RepairFixtureCore(CodingTransformersReasoningCore):
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.repair_instructions: list[str | None] = []

    def _ensure_loaded(self) -> None:
        return None

    def _generate_once(self, context, *, repair_instruction):
        self.repair_instructions.append(repair_instruction)
        next_item = self.outputs.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


def test_coding_core_repairs_noop_continue_once() -> None:
    core = RepairFixtureCore(
        [
            RawReasoningOutput(status="continue"),
            RawReasoningOutput(
                status="continue",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_read",
                        arguments={"path": "app.py"},
                    ),
                ),
            ),
        ]
    )
    result = core.generate(object())
    assert len(core.repair_instructions) == 2
    assert core.repair_instructions[0] is None
    assert "PROTOCOL REPAIR REQUIRED" in (core.repair_instructions[1] or "")
    assert result.actions[0].tool_name == "workspace_read"


def test_coding_core_repairs_action_bearing_complete_once() -> None:
    core = RepairFixtureCore(
        [
            RawReasoningOutput(
                status="complete",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_read",
                        arguments={"path": "app.py"},
                    ),
                ),
            ),
            RawReasoningOutput(status="complete"),
        ]
    )
    result = core.generate(object())
    assert len(core.repair_instructions) == 2
    assert "requires zero tool actions" in (core.repair_instructions[1] or "")
    assert result.status == "complete"


def test_coding_core_repairs_output_limit_with_compact_instruction() -> None:
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
    result = core.generate(object())
    assert len(core.repair_instructions) == 2
    assert "OUTPUT-LIMIT REPAIR REQUIRED" in (core.repair_instructions[1] or "")
    assert result.actions[0].tool_name == "workspace_patch"


def test_coding_core_fails_after_bounded_protocol_repair() -> None:
    core = RepairFixtureCore(
        [RawReasoningOutput(status="continue"), RawReasoningOutput(status="continue")]
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
    assert core.info.version == "transformers-local-coding-v4"
    assert core.info.transport == "in_process_transformers"
    assert core.info.model_inference is True
