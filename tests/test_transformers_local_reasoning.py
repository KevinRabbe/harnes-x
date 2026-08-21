from __future__ import annotations

import json

from lmformatenforcer import JsonSchemaParser

from harness_x.reasoning import TransformersLocalReasoningCore, TransformersLocalSettings
from harness_x.reasoning.adapters.transformers_local import reasoning_output_json_schema


def _traverse(schema: dict[str, object], payload: dict[str, object]) -> None:
    parser = JsonSchemaParser(schema)
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    for char in text:
        parser = parser.add_character(char)
    assert parser.can_end()


def test_local_reasoning_schema_accepts_nested_coding_action() -> None:
    _traverse(
        reasoning_output_json_schema(),
        {
            "status": "continue",
            "proposals": [],
            "actions": [
                {
                    "tool_name": "workspace_write",
                    "arguments": {
                        "path": "src/app.ts",
                        "content": "export const ready = true;\n",
                        "overwrite": True,
                        "metadata": {
                            "nested": [1, True, None, {"kind": "evidence"}]
                        },
                    },
                }
            ],
            "observations": ["Creating the requested file."],
            "requested_additional_steps": 1,
        },
    )


def test_local_reasoning_core_is_lazy_and_declares_identity() -> None:
    core = TransformersLocalReasoningCore(
        TransformersLocalSettings(
            model="Qwen/Qwen3-4B-Instruct-2507",
            revision="abc123",
        )
    )
    assert core.info.name == "transformers_local"
    assert core.info.transport == "in_process_transformers"
    assert core.info.model_inference is True
    assert core.info.model == "Qwen/Qwen3-4B-Instruct-2507"


def test_local_reasoning_settings_normalize_blank_revision() -> None:
    settings = TransformersLocalSettings(revision="   ")
    assert settings.revision is None
