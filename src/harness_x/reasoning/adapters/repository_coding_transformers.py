"""M23 constrained local coding core for repository-aware ACI tools."""

from __future__ import annotations

import json
from typing import Any

from harness_x.tools.coding import (
    ProcessRunInput,
    WorkspaceListInput,
    WorkspaceReadInput,
    WorkspaceSearchInput,
    WorkspaceWriteInput,
)
from harness_x.tools.patch_v2 import WorkspacePatchV2Input
from harness_x.tools.repository import (
    FileOutlineInput,
    GitDiffInput,
    GitStatusInput,
    RepositoryMapInput,
    SymbolDefinitionInput,
    SymbolReferencesInput,
    SymbolSearchInput,
)

from ..base import RawReasoningOutput, ReasoningCoreError
from ..context_builder import ContextBuildResult
from .coding_transformers import (
    CodingTransformersReasoningCore,
    _CodingOutputTruncated,
    _closed_input_schema,
    top_level_json_object_end,
)

_REPOSITORY_CODING_SYSTEM_PROMPT = """You are a replaceable coding reasoning core inside Harness X.
You do not own system state, memory, tools, permissions, budgets, verification, coding phases, commitments, repository state, or filesystem/process authority.
Return ONLY one compact JSON object matching the declared constrained schema.

Control protocol:
- status=continue MUST propose exactly one concrete tool action.
- status=complete MUST propose zero actions and means the user task is semantically complete from your perspective.
- status=blocked MUST propose zero actions and means no safe/progress-making action is available.
- Harness X independently owns tool execution, verification, phase transitions, commitments, progress accounting, and completion authority.

Authoritative control and repository orientation:
- sections.active_state.data.coding_control, when present, is software-owned authoritative control state.
- sections.repository_intelligence, when present, is bounded software-derived repository orientation available before your first action.
- Respect pending commitments, horizon posture, and controller intervention directives.
- The repository orientation is intentionally bounded and may become stale after edits. Use repository_map(refresh=true), git_status, file_outline, or symbol tools when exact current state matters.
- Every symbol result declares precision. exact_ast and lsp are stronger evidence than heuristic; never treat heuristic navigation as compiler proof.

Repository/navigation behavior:
- Do not spend early turns listing or rereading the repository root when the repository map already answers the structural question.
- Prefer symbol_search and symbol_definition for locating named code; prefer file_outline for one file's structure and current SHA-256.
- Prefer symbol_references for a bounded impact check before changing a public or widely used symbol.
- Prefer git_status and git_diff for structured change review instead of generic process calls.
- Use workspace_read/workspace_search when semantic navigation cannot answer the question or exact text is required.

Editing behavior:
- Prefer the smallest verifiable edit.
- workspace_patch mode=exact is appropriate when you know a small unique old_text snippet.
- workspace_patch mode=range is appropriate for line-bounded edits after file_outline or symbol_definition supplied the current full-file SHA-256. Range mode refuses stale hashes.
- Use workspace_write mainly for new files; do not rewrite a whole existing file when a bounded patch is sufficient.
- After structural edits, refresh repository intelligence only when needed; do not rescan reflexively after every small edit.

Do not invent candidate IDs, provenance, permissions, verification state, commitments, phase transitions, precision, hashes, or state mutations.
Do not emit markdown fences, XML/tool_call tags, prose outside the JSON object, or private chain-of-thought.
"""

_REPOSITORY_CODING_TOOL_INPUT_MODELS = (
    ("repository_map", RepositoryMapInput),
    ("file_outline", FileOutlineInput),
    ("symbol_search", SymbolSearchInput),
    ("symbol_definition", SymbolDefinitionInput),
    ("symbol_references", SymbolReferencesInput),
    ("git_status", GitStatusInput),
    ("git_diff", GitDiffInput),
    ("workspace_list", WorkspaceListInput),
    ("workspace_read", WorkspaceReadInput),
    ("workspace_search", WorkspaceSearchInput),
    ("workspace_write", WorkspaceWriteInput),
    ("workspace_patch", WorkspacePatchV2Input),
    ("process_run", ProcessRunInput),
)


def _repository_coding_action_schema() -> dict[str, Any]:
    branches: list[dict[str, Any]] = []
    for tool_name, input_model in _REPOSITORY_CODING_TOOL_INPUT_MODELS:
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


def repository_coding_reasoning_output_json_schema() -> dict[str, Any]:
    """Finite LMFE schema for one M23 repository-aware coding turn."""

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
                "items": _repository_coding_action_schema(),
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


class RepositoryCodingTransformersReasoningCore(CodingTransformersReasoningCore):
    """M23 local Qwen core constrained to the 13-tool repository-aware protocol."""

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._info = self._info.model_copy(
            update={
                "name": "transformers_local_repository_coding",
                "version": "transformers-local-repository-coding-v1",
            }
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
                "constrained local repository coding requires lm-format-enforcer==0.11.3"
            ) from exc

        user_content = context.serialized
        if repair_instruction is not None:
            user_content = f"{user_content}\n\n{repair_instruction}"
        messages = [
            {"role": "system", "content": _REPOSITORY_CODING_SYSTEM_PROMPT},
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
                f"failed to build local repository coding input: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            device = self._model.device
            inputs = {key: value.to(device) for key, value in inputs.items()}
            prompt_tokens = int(inputs["input_ids"].shape[-1])
            parser = JsonSchemaParser(
                repository_coding_reasoning_output_json_schema(),
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
                f"local constrained repository coding generation failed: {type(exc).__name__}: {exc}"
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
                "local repository coding model produced malformed constrained JSON before "
                f"the token limit: {exc}"
            ) from exc
        try:
            return RawReasoningOutput.model_validate(payload)
        except Exception as exc:
            raise ReasoningCoreError(
                f"local repository coding output violated the reasoning contract: {exc}"
            ) from exc
