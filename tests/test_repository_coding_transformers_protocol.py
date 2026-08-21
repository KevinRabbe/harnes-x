from __future__ import annotations

import json

from lmformatenforcer import JsonSchemaParser

from harness_x.reasoning import TransformersLocalSettings
from harness_x.reasoning.adapters.repository_coding_transformers import (
    RepositoryCodingTransformersReasoningCore,
    repository_coding_reasoning_output_json_schema,
)


def _branches_by_name() -> dict[str, dict]:
    schema = repository_coding_reasoning_output_json_schema()
    branches = schema["properties"]["actions"]["items"]["anyOf"]
    return {
        branch["properties"]["tool_name"]["enum"][0]: branch
        for branch in branches
    }


def _traverse(payload: dict[str, object]) -> None:
    parser = JsonSchemaParser(repository_coding_reasoning_output_json_schema())
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    for char in text:
        parser = parser.add_character(char)
    assert parser.can_end()


def test_repository_coding_schema_declares_exact_live_registry_surface() -> None:
    by_name = _branches_by_name()
    assert set(by_name) == {
        "repository_map",
        "file_outline",
        "symbol_search",
        "symbol_definition",
        "symbol_references",
        "git_status",
        "git_diff",
        "workspace_list",
        "workspace_read",
        "workspace_search",
        "workspace_write",
        "workspace_patch",
        "process_run",
    }

    patch = by_name["workspace_patch"]["properties"]["arguments"]
    assert {"mode", "path"} <= set(patch["required"])
    assert patch["properties"]["mode"]["enum"] == ["exact", "range"]
    assert "expected_sha256" in patch["properties"]
    assert "old_text" in patch["properties"]
    assert patch["additionalProperties"] is False


def test_repository_coding_schema_real_lmfe_traversal() -> None:
    _traverse({"status": "complete", "actions": []})
    _traverse(
        {
            "status": "continue",
            "actions": [
                {
                    "tool_name": "symbol_definition",
                    "arguments": {"name": "RepositoryIntelligenceService"},
                }
            ],
        }
    )
    _traverse(
        {
            "status": "continue",
            "actions": [
                {
                    "tool_name": "workspace_patch",
                    "arguments": {
                        "mode": "exact",
                        "path": "app.py",
                        "old_text": "return a - b",
                        "new_text": "return a + b",
                    },
                }
            ],
        }
    )
    _traverse(
        {
            "status": "continue",
            "actions": [
                {
                    "tool_name": "workspace_patch",
                    "arguments": {
                        "mode": "range",
                        "path": "app.py",
                        "start_line": 10,
                        "end_line": 12,
                        "expected_sha256": "0" * 64,
                        "replacement": "return fixed",
                    },
                }
            ],
        }
    )


def test_repository_coding_core_is_lazy_and_declares_distinct_identity() -> None:
    core = RepositoryCodingTransformersReasoningCore(
        TransformersLocalSettings(model="Qwen/Qwen3-4B-Instruct-2507")
    )
    assert core.info.name == "transformers_local_repository_coding"
    assert core.info.version == "transformers-local-repository-coding-v1"
    assert core.info.transport == "in_process_transformers"
    assert core.info.model_inference is True
