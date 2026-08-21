"""In-process Hugging Face Transformers reasoning core with constrained JSON output."""

from __future__ import annotations

import gc
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..base import RawReasoningOutput, ReasoningCoreError, ReasoningCoreInfo
from ..context_builder import ContextBuildResult


_SYSTEM_PROMPT = """You are a replaceable reasoning core inside Harness X.
You do not own system state, memory, tools, permissions, budgets, verification, or filesystem/process authority.
Return ONLY one JSON object matching the declared constrained schema.
The JSON fields are:
- status: complete | continue | blocked
- proposals: optional non-authoritative suggestions
- actions: tool actions proposed for software validation/execution
- observations: short observations for the software-owned working state
- requested_additional_steps: non-negative integer

Prefer at most one action per response. Harness X executes one authoritative action and returns its observation before the next reasoning step.
Do not invent candidate IDs, provenance, permissions, verification state, or state mutations.
Do not emit markdown fences, XML/tool_call tags, prose outside the JSON object, or private chain-of-thought.
"""


def reasoning_output_json_schema() -> dict[str, Any]:
    """Finite target-independent schema for one Harness X reasoning turn."""

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
                "maxItems": 2,
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


class TransformersLocalSettings(BaseModel):
    """Settings for direct local Transformers inference."""

    model_config = ConfigDict(frozen=True)

    model: str = Field(default="Qwen/Qwen3-4B-Instruct-2507", min_length=1)
    revision: str | None = None
    cache_dir: Path | None = None
    max_new_tokens: int = Field(default=4096, ge=64, le=16384)
    load_in_4bit: bool = True
    local_files_only: bool = False

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model cannot be blank")
        return value

    @field_validator("revision")
    @classmethod
    def normalize_revision(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class TransformersLocalReasoningCore:
    """Direct local generation with LMFE-constrained Harness X action JSON.

    Heavy runtime dependencies are imported lazily so the base Harness X package and
    CI remain usable without installing local model inference dependencies.
    """

    def __init__(self, settings: TransformersLocalSettings) -> None:
        self.settings = settings
        self._info = ReasoningCoreInfo(
            name="transformers_local",
            version="transformers-local-v1",
            model=settings.model,
            transport="in_process_transformers",
            model_inference=True,
        )
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._tokenizer_data: Any | None = None

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def _ensure_loaded(self) -> None:
        if self._model is not None and self._tokenizer is not None:
            return
        try:
            import torch
            from transformers import (
                AutoModelForCausalLM,
                AutoTokenizer,
                BitsAndBytesConfig,
            )
        except ImportError as exc:
            raise ReasoningCoreError(
                "in-process Transformers coding requires the local-coding extra: "
                'python -m pip install -e ".[dev,local-coding]"'
            ) from exc

        model_kwargs: dict[str, Any] = {
            "device_map": "auto",
            "local_files_only": self.settings.local_files_only,
        }
        if self.settings.revision is not None:
            model_kwargs["revision"] = self.settings.revision
        if self.settings.cache_dir is not None:
            model_kwargs["cache_dir"] = str(self.settings.cache_dir)

        if self.settings.load_in_4bit:
            if not torch.cuda.is_available():
                raise ReasoningCoreError(
                    "4-bit local coding inference requires a CUDA device; "
                    "use load_in_4bit=False for non-CUDA execution"
                )
            compute_dtype = (
                torch.bfloat16
                if getattr(torch.cuda, "is_bf16_supported", lambda: False)()
                else torch.float16
            )
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=compute_dtype,
            )
        else:
            model_kwargs["torch_dtype"] = "auto"

        tokenizer_kwargs: dict[str, Any] = {
            "local_files_only": self.settings.local_files_only,
        }
        if self.settings.revision is not None:
            tokenizer_kwargs["revision"] = self.settings.revision
        if self.settings.cache_dir is not None:
            tokenizer_kwargs["cache_dir"] = str(self.settings.cache_dir)

        try:
            tokenizer = AutoTokenizer.from_pretrained(
                self.settings.model,
                **tokenizer_kwargs,
            )
            model = AutoModelForCausalLM.from_pretrained(
                self.settings.model,
                **model_kwargs,
            )
        except Exception as exc:
            raise ReasoningCoreError(
                f"failed to load local model {self.settings.model!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        model.eval()
        self._tokenizer = tokenizer
        self._model = model

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
                "constrained local reasoning requires lm-format-enforcer==0.11.3"
            ) from exc

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
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
                f"failed to build local model chat input: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            device = self._model.device
            inputs = {key: value.to(device) for key, value in inputs.items()}
            prompt_tokens = int(inputs["input_ids"].shape[-1])
            parser = JsonSchemaParser(
                reasoning_output_json_schema(),
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
                f"local constrained generation failed: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ReasoningCoreError(
                f"local model violated constrained JSON output: {exc}"
            ) from exc
        try:
            return RawReasoningOutput.model_validate(payload)
        except Exception as exc:
            raise ReasoningCoreError(
                f"local model output violated the reasoning contract: {exc}"
            ) from exc

    def close(self) -> None:
        self._model = None
        self._tokenizer = None
        self._tokenizer_data = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
