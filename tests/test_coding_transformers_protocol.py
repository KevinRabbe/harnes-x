from __future__ import annotations

import json

from lmformatenforcer import JsonSchemaParser

from harness_x.reasoning import TransformersLocalSettings
from harness_x.reasoning.adapters.coding_transformers import (
    CodingTransformersReasoningCore,
    coding_reasoning_output_json_schema,
)


def _accepts(payload: dict[str, object]) -> bool:
    parser = JsonSchemaParser(coding_reasoning_output_json_schema())
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    try:
        for char in text:
            parser = parser.add_character(char)
    except Exception:
        return False
    return parser.can_end()


def _base(status: str, actions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": status,
        "proposals": [],
        "actions": actions,
        "observations": [],
        "requested_additional_steps": 0,
    }


def test_coding_schema_accepts_continue_with_exactly_one_nested_action() -> None:
    assert _accepts(
        _base(
            "continue",
            [
                {
                    "tool_name": "workspace_patch",
                    "arguments": {
                        "path": "app.py",
                        "old_text": "return a - b",
                        "new_text": "return a + b",
                        "metadata": {"nested": [1, True, None]},
                    },
                }
            ],
        )
    )


def test_coding_schema_rejects_noop_continue() -> None:
    assert not _accepts(_base("continue", []))


def test_coding_schema_accepts_actionless_complete() -> None:
    assert _accepts(_base("complete", []))


def test_coding_schema_rejects_complete_with_action() -> None:
    assert not _accepts(
        _base(
            "complete",
            [{"tool_name": "workspace_list", "arguments": {"path": "."}}],
        )
    )


def test_coding_transformers_core_is_lazy_and_declares_coding_identity() -> None:
    core = CodingTransformersReasoningCore(
        TransformersLocalSettings(model="Qwen/Qwen3-4B-Instruct-2507")
    )
    assert core.info.name == "transformers_local_coding"
    assert core.info.transport == "in_process_transformers"
    assert core.info.model_inference is True
