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


def coding_reasoning_output_json_schema() -> dict[str, Any]:
    """Finite syntactic schema for one coding turn.

    LMFE 0.11.3 reliably constrains JSON structure but does not enforce every
    cross-field/cardinality condition. Harness X therefore owns the semantic control
    invariant in :func:`coding_protocol_violation` and performs one bounded repair
    generation when needed.
    """

    return {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["complete", "continue", "blocked"],
            },
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
                "maxItems": 1,
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
        },
        "required": [
            "status",
            "proposals",
            "actions",
            "observations",
            "requested_additional_steps",
        ],
        "additionalProperties": False,
    }


def coding_protocol_violation(output: RawReasoningOutput) -> str | None:
    """Return the software-owned coding protocol violation, if any."""

    action_count = len(output.actions)
    if output.status == "continue" and action_count != 1:
        return "status=continue requires exactly one tool action"
    if output.status in {"complete", "blocked"} and action_count != 0:
        return f"status={output.status} requires zero tool actions"
    return None


class CodingTransformersReasoningCore(TransformersLocalReasoningCore):
    """Direct local core with one bounded semantic-protocol repair attempt."""

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._info = self._info.model_copy(
            update={
                "name": "transformers_local_coding",
                "version": "transformers-local-coding-v2",
            }
        )

    def generate(self, context: ContextBuildResult) -> RawReasoningOutput:
        self._ensure_loaded()
        violation: str | None = None
        for attempt in range(2):
            repair_instruction = None
            if attempt:
                assert violation is not None
                repair_instruction = (
                    "PROTOCOL REPAIR REQUIRED. Your previous JSON was syntactically valid "
                    f"but violated this Harness X control invariant: {violation}. "
                    "Return a corrected JSON object now. If more work is needed, use "
                    "status=continue with exactly one concrete action. If the task is done, "
                    "use status=complete with zero actions."
                )
            output = self._generate_once(context, repair_instruction=repair_instruction)
            violation = coding_protocol_violation(output)
            if violation is None:
                return output
        raise ReasoningCoreError(
            f"local coding model violated the control protocol after bounded repair: {violation}"
        )

    def _generate_once(
        self,
        context: ContextBuildResult,
        *,
        repair_instruction: str | None,
    ) -> RawReasoningOutput:
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

        user_content = context.serialized
        if repair_instruction is not None:
            user_content = f"{user_content}\n\n{repair_instruction}"
        messages = [
            {"role": "system", "content": _CODING_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
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
