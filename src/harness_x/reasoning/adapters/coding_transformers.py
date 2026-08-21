"""Coding-specific local Transformers core with an actionable turn protocol."""

from __future__ import annotations

import json
from typing import Any

from harness_x.tools.coding import (
    ProcessRunInput,
    WorkspaceListInput,
    WorkspacePatchInput,
    WorkspaceReadInput,
    WorkspaceSearchInput,
    WorkspaceWriteInput,
)

from ..base import RawReasoningOutput, ReasoningCoreError
from ..context_builder import ContextBuildResult
from .transformers_local import TransformersLocalReasoningCore


_CODING_SYSTEM_PROMPT = """You are a replaceable coding reasoning core inside Harness X.
You do not own system state, memory, tools, permissions, budgets, verification, or filesystem/process authority.
Return ONLY one compact JSON object matching the declared constrained schema.

Control protocol:
- status=continue MUST propose exactly one concrete tool action.
- status=complete MUST propose zero actions and means the user task is semantically complete from your perspective.
- status=blocked MUST propose zero actions and means no safe/progress-making action is available.
- Harness X independently schedules and owns verification; passing verification evidence may appear in working state.

Coding behavior:
- Inspect before editing.
- Prefer workspace_patch for an existing file and use the smallest unique old_text/new_text snippets that make the change.
- Use workspace_write mainly for new files; do not rewrite a whole existing file when a small patch is sufficient.
- Keep observations short and omit them unless they add important state.
- Do not narrate analysis inside tool arguments.

Do not invent candidate IDs, provenance, permissions, verification state, or state mutations.
Do not emit markdown fences, XML/tool_call tags, prose outside the JSON object, or private chain-of-thought.
"""

_CODING_TOOL_INPUT_MODELS = (
    ("workspace_list", WorkspaceListInput),
    ("workspace_read", WorkspaceReadInput),
    ("workspace_search", WorkspaceSearchInput),
    ("workspace_write", WorkspaceWriteInput),
    ("workspace_patch", WorkspacePatchInput),
    ("process_run", ProcessRunInput),
)


class _CodingOutputTruncated(RuntimeError):
    """One constrained generation exhausted its token bound before JSON completion."""


def _closed_input_schema(model: type[Any]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    schema["additionalProperties"] = False
    return schema


def _coding_action_schema() -> dict[str, Any]:
    branches: list[dict[str, Any]] = []
    for tool_name, input_model in _CODING_TOOL_INPUT_MODELS:
        branches.append(
            {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "enum": [tool_name]},
                    "arguments": _closed_input_schema(input_model),
                },
                "required": ["tool_name", "arguments"],
                "additionalProperties": False,
            }
        )
    return {"anyOf": branches}


def coding_reasoning_output_json_schema() -> dict[str, Any]:
    """Finite syntactic schema for one compact coding turn.

    The action branch is generated from the exact Pydantic input contracts used by the
    coding tools. LMFE owns syntactic structure; Harness X still owns the semantic
    cross-field invariant in :func:`coding_protocol_violation` because LMFE 0.11.3 does
    not enforce every array-cardinality relationship.
    """

    return {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["complete", "continue", "blocked"],
            },
            "actions": {
                "type": "array",
                "maxItems": 1,
                "items": _coding_action_schema(),
            },
            "observations": {
                "type": "array",
                "maxItems": 1,
                "items": {"type": "string", "maxLength": 320},
            },
            "requested_additional_steps": {
                "type": "integer",
                "minimum": 0,
                "maximum": 8,
            },
        },
        "required": ["status", "actions"],
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


def top_level_json_object_end(text: str) -> int | None:
    """Return the exclusive end of the first complete top-level JSON object."""

    start = 0
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text) or text[start] != "{":
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
            if depth < 0:
                return None
    return None


class CodingTransformersReasoningCore(TransformersLocalReasoningCore):
    """Direct local core with bounded syntax/control repair and JSON completion stop."""

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._info = self._info.model_copy(
            update={
                "name": "transformers_local_coding",
                "version": "transformers-local-coding-v3",
            }
        )

    def generate(self, context: ContextBuildResult) -> RawReasoningOutput:
        self._ensure_loaded()
        repair_instruction: str | None = None
        last_violation: str | None = None
        last_truncation: str | None = None

        for attempt in range(2):
            try:
                output = self._generate_once(
                    context,
                    repair_instruction=repair_instruction,
                )
            except _CodingOutputTruncated as exc:
                last_truncation = str(exc)
                if attempt == 0:
                    repair_instruction = (
                        "OUTPUT-LIMIT REPAIR REQUIRED. Your previous response reached the "
                        "generation token limit before the JSON object closed. Return a much "
                        "smaller object. Use observations=[] and requested_additional_steps=0. "
                        "If work remains, choose exactly one concise tool action. For an "
                        "existing file prefer workspace_patch with only the smallest unique "
                        "old_text/new_text snippets needed for this step."
                    )
                    continue
                raise ReasoningCoreError(
                    "local coding generation exhausted max_new_tokens before completing "
                    f"JSON after bounded repair: {last_truncation}"
                ) from exc

            violation = coding_protocol_violation(output)
            if violation is None:
                return output
            last_violation = violation
            if attempt == 0:
                repair_instruction = (
                    "PROTOCOL REPAIR REQUIRED. Your previous JSON was syntactically valid "
                    f"but violated this Harness X control invariant: {violation}. "
                    "Return a corrected compact JSON object now. If more work is needed, use "
                    "status=continue with exactly one concrete action. If the task is done, "
                    "use status=complete with zero actions."
                )

        raise ReasoningCoreError(
            "local coding model violated the control protocol after bounded repair: "
            f"{last_violation}"
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
            from transformers import StoppingCriteria, StoppingCriteriaList
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

            tokenizer = self._tokenizer

            class _JsonObjectComplete(StoppingCriteria):
                def __call__(self, input_ids, scores, **kwargs):  # type: ignore[override]
                    generated_ids = input_ids[0, prompt_tokens:]
                    if generated_ids.numel() == 0:
                        done = False
                    else:
                        partial = tokenizer.decode(
                            generated_ids,
                            skip_special_tokens=True,
                            clean_up_tokenization_spaces=False,
                        )
                        done = top_level_json_object_end(partial) is not None
                    return torch.tensor(
                        [done],
                        dtype=torch.bool,
                        device=input_ids.device,
                    )

            with torch.inference_mode():
                outputs = self._model.generate(
                    **inputs,
                    max_new_tokens=self.settings.max_new_tokens,
                    do_sample=False,
                    use_cache=True,
                    prefix_allowed_tokens_fn=prefix_allowed_tokens_fn,
                    stopping_criteria=StoppingCriteriaList([_JsonObjectComplete()]),
                    pad_token_id=self._tokenizer.eos_token_id,
                )
            generated = outputs[0][prompt_tokens:]
            generated_token_count = int(generated.shape[-1])
            text = self._tokenizer.decode(
                generated,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            ).strip()
        except Exception as exc:
            raise ReasoningCoreError(
                f"local constrained coding generation failed: {type(exc).__name__}: {exc}"
            ) from exc

        object_end = top_level_json_object_end(text)
        if object_end is not None:
            text = text[:object_end]
        elif generated_token_count >= self.settings.max_new_tokens:
            raise _CodingOutputTruncated(
                f"generated_tokens={generated_token_count}, limit={self.settings.max_new_tokens}"
            )

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ReasoningCoreError(
                "local coding model produced malformed constrained JSON before the token "
                f"limit: {exc}"
            ) from exc
        try:
            return RawReasoningOutput.model_validate(payload)
        except Exception as exc:
            raise ReasoningCoreError(
                f"local coding output violated the reasoning contract: {exc}"
            ) from exc
