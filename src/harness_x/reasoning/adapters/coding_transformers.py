"""Coding-specific local Transformers core with an actionable turn protocol."""

from __future__ import annotations

import json
from typing import Any

from ..base import RawReasoningOutput, ReasoningCoreError
from ..context_builder import ContextBuildResult
from .transformers_local import TransformersLocalReasoningCore


_CODING_SYSTEM_PROMPT = """You are a replaceable coding reasoning core inside Harness X.
You do not own system state, memory, tools, permissions, budgets, verification, or filesystem/process authority.
Return ONLY one JSON object matching the declared constrained schema.

Control protocol:
- status=continue MUST propose exactly one concrete tool action.
- status=complete MUST propose zero actions and means the user task is semantically complete from your perspective.
- status=blocked MUST propose zero actions and means no safe/progress-making action is available.
- Harness X independently schedules and owns verification; passing verification evidence may appear in working state.

The JSON fields are:
- status: complete | continue | blocked
- proposals: optional non-authoritative suggestions
- actions: tool actions proposed for software validation/execution
- observations: short observations for the software-owned working state
- requested_additional_steps: non-negative integer

Do not invent candidate IDs, provenance, permissions, verification state, or state mutations.
Do not emit markdown fences, XML/tool_call tags, prose outside the JSON object, or private chain-of-thought.
"""


def _common_properties(*, status: str, min_actions: int, max_actions: int) -> dict[str, Any]:
    return {
        "status": {"type": "string", "enum": [status]},
        "proposals": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "maxLength": 1200},
                    "payload": {"type": "object"},
                },
                "required": ["summary", "payload"],
                "additionalProperties": False,
            },
        },
        "actions": {
            "type": "array",
            "minItems": min_actions,
            "maxItems": max_actions,
            "items": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "maxLength": 128},
                    "arguments": {"type": "object"},
                },
                "required": ["tool_name", "arguments"],
                "additionalProperties": False,
            },
        },
        "observations": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "maxLength": 1200},
        },
        "requested_additional_steps": {
            "type": "integer",
            "minimum": 0,
            "maximum": 64,
        },
    }


def _turn_branch(*, status: str, min_actions: int, max_actions: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": _common_properties(
            status=status,
            min_actions=min_actions,
            max_actions=max_actions,
        ),
        "required": [
            "status",
            "proposals",
            "actions",
            "observations",
            "requested_additional_steps",
        ],
        "additionalProperties": False,
    }


def coding_reasoning_output_json_schema() -> dict[str, Any]:
    """Finite schema that excludes no-op ``continue`` turns."""

    return {
        "oneOf": [
            _turn_branch(status="continue", min_actions=1, max_actions=1),
            _turn_branch(status="complete", min_actions=0, max_actions=0),
            _turn_branch(status="blocked", min_actions=0, max_actions=0),
        ]
    }


class CodingTransformersReasoningCore(TransformersLocalReasoningCore):
    """Direct local core whose decoder can only emit actionable coding states."""

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._info = self._info.model_copy(
            update={
                "name": "transformers_local_coding",
                "version": "transformers-local-coding-v1",
            }
        )

    def generate(self, context: ContextBuildResult) -> RawReasoningOutput:
        self._ensure_loaded()
        assert self._tokenizer is not None
        assert self._model is not None

        try:
            import torch
            from lmformatenforcer import CharacterLevelParserConfig, JsonSchemaParser
            from harness_x.training.lmfe_compat import (
                build_lmfe_prefix_allowed_tokens_fn,
            )
        except ImportError as exc:
            raise ReasoningCoreError(
                "constrained local coding requires lm-format-enforcer==0.11.3"
            ) from exc

        messages = [
            {"role": "system", "content": _CODING_SYSTEM_PROMPT},
            {"role": "user", "content": context.serialized},
        ]
        try:
            inputs = self._tokenizer.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
            )
        except Exception as exc:
            raise ReasoningCoreError(
                f"failed to build local coding input: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            device = self._model.device
            inputs = {key: value.to(device) for key, value in inputs.items()}
            prompt_tokens = int(inputs["input_ids"].shape[-1])
            parser = JsonSchemaParser(
                coding_reasoning_output_json_schema(),
                config=CharacterLevelParserConfig(max_json_array_length=8),
            )
            prefix_allowed_tokens_fn, self._tokenizer_data = (
                build_lmfe_prefix_allowed_tokens_fn(
                    self._tokenizer,
                    parser,
                    tokenizer_data=self._tokenizer_data,
                )
            )
            with torch.inference_mode():
                outputs = self._model.generate(
                    **inputs,
                    max_new_tokens=self.settings.max_new_tokens,
                    do_sample=False,
                    use_cache=True,
                    prefix_allowed_tokens_fn=prefix_allowed_tokens_fn,
                    pad_token_id=self._tokenizer.eos_token_id,
                )
            generated = outputs[0][prompt_tokens:]
            text = self._tokenizer.decode(
                generated,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            ).strip()
        except Exception as exc:
            raise ReasoningCoreError(
                f"local constrained coding generation failed: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ReasoningCoreError(
                f"local coding model violated constrained JSON output: {exc}"
            ) from exc
        try:
            return RawReasoningOutput.model_validate(payload)
        except Exception as exc:
            raise ReasoningCoreError(
                f"local coding output violated the reasoning contract: {exc}"
            ) from exc
