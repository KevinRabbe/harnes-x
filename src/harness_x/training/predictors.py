"""Prediction backends for held-out self-model evaluation."""

from __future__ import annotations

import gc
import json
import re
from pathlib import Path
from typing import Any

from .evaluation import SelfModelPrediction
from .formatting import (
    SelfModelContextProfile,
    TrainingMessage,
    format_self_model_example,
    render_messages_with_tokenizer,
)
from .models import SelfModelExample
from .repair_schema import GENERIC_ARRAY_ITEM_LIMIT, repair_json_schema


_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
REPAIR_ARRAY_ITEM_LIMIT = 8
REPAIR_NO_REPEAT_NGRAM_SIZE = 8
REPAIR_CONSTRAINT_MODES = frozenset({"bounded", "schema"})


def top_level_json_object_end(text: str) -> int | None:
    """Return the exclusive end of the first complete top-level JSON object.

    This is a lexical completion detector, not a permissive parser. It deliberately
    does not repair malformed text or infer missing delimiters; strict ``json.loads``
    remains the authority for whether the resulting output is valid JSON.
    """

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


def parse_structured_prediction(text: str) -> SelfModelPrediction:
    raw = text.strip()
    if raw.startswith("```"):
        raw = _JSON_FENCE.sub("", raw).strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return SelfModelPrediction(
            decision={}, raw_text=text, parse_error=f"invalid_json: {exc.msg}"
        )
    if not isinstance(value, dict):
        return SelfModelPrediction(
            decision={}, raw_text=text, parse_error="prediction must be a JSON object"
        )

    if isinstance(value.get("decision"), dict):
        confidence = value.get("confidence")
        if confidence is not None and not isinstance(confidence, (int, float)):
            return SelfModelPrediction(
                decision={}, raw_text=text, parse_error="confidence must be numeric"
            )
        return SelfModelPrediction(
            decision=dict(value["decision"]),
            confidence=float(confidence) if confidence is not None else None,
            raw_text=text,
        )
    return SelfModelPrediction(decision=value, raw_text=text)


class HuggingFaceSelfModelPredictor:
    """Optional deterministic-generation predictor for base or PEFT adapter."""

    def __init__(
        self,
        *,
        base_model: str,
        base_model_revision: str | None = None,
        tokenizer_revision: str | None = None,
        adapter_path: str | Path | None = None,
        load_in_4bit: bool = True,
        max_new_tokens: int = 512,
        context_profile: SelfModelContextProfile = SelfModelContextProfile.STANDARD,
    ) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        except ImportError as exc:  # pragma: no cover - environment-specific backend
            raise RuntimeError(
                "self-model evaluation dependencies are missing; install harness-x[training]"
            ) from exc

        self._torch = torch
        self._name = (
            f"adapter:{Path(adapter_path).name}" if adapter_path is not None else f"base:{base_model}"
        )
        self.max_new_tokens = max_new_tokens
        self.context_profile = SelfModelContextProfile(context_profile)
        self.tokenizer = AutoTokenizer.from_pretrained(
            base_model,
            revision=tokenizer_revision or base_model_revision,
            use_fast=True,
        )
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        model_kwargs: dict[str, Any] = {
            "device_map": "auto",
            "revision": base_model_revision,
        }
        if load_in_4bit:
            dtype = (
                torch.bfloat16
                if torch.cuda.is_available() and torch.cuda.is_bf16_supported()
                else torch.float16
            )
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=dtype,
            )
        model = AutoModelForCausalLM.from_pretrained(base_model, **model_kwargs)
        if adapter_path is not None:
            try:
                from peft import PeftModel
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("PEFT is required to evaluate an adapter") from exc
            model = PeftModel.from_pretrained(model, str(adapter_path))
        model.eval()
        self.model = model

    @property
    def name(self) -> str:
        return self._name

    def _render_prompt(
        self,
        example: SelfModelExample,
        profile: SelfModelContextProfile,
    ) -> str:
        record = format_self_model_example(example, context_profile=profile)
        return render_messages_with_tokenizer(
            self.tokenizer, record.prompt_messages, add_generation_prompt=True
        )

    def _render_repair_prompt(
        self,
        example: SelfModelExample,
        profile: SelfModelContextProfile,
        *,
        constraint_mode: str,
    ) -> str:
        """Build a fresh strict-format retry without exposing held-out target values."""

        record = format_self_model_example(example, context_profile=profile)
        system = record.prompt_messages[0]
        schema_note = (
            " The decoder enforces the task's target-independent JSON contract. Choose "
            "the semantic values from grounded input; do not fight the schema."
            if constraint_mode == "schema"
            else ""
        )
        repair_instruction = (
            system.content
            + " A previous generation failed strict JSON validation. Retry from the "
            "same grounded input. Return exactly one compact JSON object and nothing "
            "else. Use each requested top-level key at most once. Every array must "
            f"contain at most {REPAIR_ARRAY_ITEM_LIMIT} items and duplicate items are "
            "forbidden. Never create identifiers by repeatedly appending the same "
            "suffix. If uncertain, prefer a shorter answer over repetition. Stop "
            "immediately after the top-level closing brace."
            + schema_note
        )
        messages = (
            TrainingMessage(role="system", content=repair_instruction),
            record.prompt_messages[1],
        )
        return render_messages_with_tokenizer(
            self.tokenizer, messages, add_generation_prompt=True
        )

    def _json_stopping_criteria(self, prompt_length: int) -> Any:
        """Build a transformers stopping criterion for one complete JSON object."""

        from transformers import StoppingCriteria, StoppingCriteriaList

        tokenizer = self.tokenizer
        torch = self._torch

        class _CompleteJsonObject(StoppingCriteria):
            def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> Any:
                completed: list[bool] = []
                for row in input_ids:
                    generated = row[prompt_length:]
                    text = tokenizer.decode(generated, skip_special_tokens=True)
                    completed.append(top_level_json_object_end(text) is not None)
                return torch.tensor(
                    completed,
                    dtype=torch.bool,
                    device=input_ids.device,
                )

        return StoppingCriteriaList([_CompleteJsonObject()])

    def _schema_prefix_allowed_tokens_fn(self, schema: dict[str, Any]) -> Any:
        """Build lazy optional JSON-schema constrained decoding for repair only."""

        try:
            from lmformatenforcer import CharacterLevelParserConfig, JsonSchemaParser
            from lmformatenforcer.integrations.transformers import (
                build_transformers_prefix_allowed_tokens_fn,
            )
        except ImportError as exc:  # pragma: no cover - optional operator dependency
            raise RuntimeError(
                "schema-constrained repair requires lm-format-enforcer; install "
                "lm-format-enforcer==0.11.3"
            ) from exc

        config = CharacterLevelParserConfig(
            max_json_array_length=GENERIC_ARRAY_ITEM_LIMIT,
        )
        parser = JsonSchemaParser(schema, config=config)
        return build_transformers_prefix_allowed_tokens_fn(self.tokenizer, parser)

    def _generate_text(
        self,
        prompt: str,
        *,
        max_new_tokens: int,
        no_repeat_ngram_size: int = 0,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        encoded = self.tokenizer(prompt, return_tensors="pt")
        try:
            device = next(self.model.parameters()).device
            encoded = {key: value.to(device) for key, value in encoded.items()}
        except (StopIteration, AttributeError):
            pass
        input_length = encoded["input_ids"].shape[-1]
        generation_kwargs: dict[str, Any] = {
            **encoded,
            "do_sample": False,
            "max_new_tokens": max_new_tokens,
            "pad_token_id": self.tokenizer.pad_token_id,
            "eos_token_id": self.tokenizer.eos_token_id,
            "stopping_criteria": self._json_stopping_criteria(input_length),
        }
        if no_repeat_ngram_size > 0:
            generation_kwargs["no_repeat_ngram_size"] = no_repeat_ngram_size
        if json_schema is not None:
            generation_kwargs["prefix_allowed_tokens_fn"] = (
                self._schema_prefix_allowed_tokens_fn(json_schema)
            )

        with self._torch.no_grad():
            output = self.model.generate(**generation_kwargs)
        generated = output[0][input_length:]
        text = self.tokenizer.decode(generated, skip_special_tokens=True)
        complete_end = top_level_json_object_end(text)
        if complete_end is not None:
            text = text[:complete_end]
        return text

    @property
    def token_measurement_kind(self) -> str:
        return "tokenizer"

    def prompt_measurement(
        self,
        example: SelfModelExample,
        profile: SelfModelContextProfile,
    ) -> tuple[int, int]:
        prompt = self._render_prompt(example, SelfModelContextProfile(profile))
        tokenized = self.tokenizer(prompt, add_special_tokens=False)
        return len(prompt), len(tokenized["input_ids"])

    def predict(self, example: SelfModelExample) -> SelfModelPrediction:
        return self.predict_with_profile(example, self.context_profile)

    def predict_with_profile(
        self,
        example: SelfModelExample,
        profile: SelfModelContextProfile,
    ) -> SelfModelPrediction:
        profile = SelfModelContextProfile(profile)
        prompt = self._render_prompt(example, profile)
        text = self._generate_text(prompt, max_new_tokens=self.max_new_tokens)
        return parse_structured_prediction(text)

    def repair_prediction(
        self,
        example: SelfModelExample,
        profile: SelfModelContextProfile,
        *,
        max_new_tokens: int = 256,
        constraint_mode: str = "bounded",
    ) -> SelfModelPrediction:
        """Run one fresh strict-format retry after a parse failure.

        The malformed primary output is deliberately not fed back into the model. The
        optional schema mode constrains syntax/types/domains from the task protocol and
        stable Harness X vocabularies only; it never reads target values.
        """

        if max_new_tokens < 1:
            raise ValueError("repair max_new_tokens must be positive")
        if constraint_mode not in REPAIR_CONSTRAINT_MODES:
            raise ValueError(
                f"unknown repair constraint mode {constraint_mode!r}; expected one of "
                f"{sorted(REPAIR_CONSTRAINT_MODES)!r}"
            )
        profile = SelfModelContextProfile(profile)
        prompt = self._render_repair_prompt(
            example,
            profile,
            constraint_mode=constraint_mode,
        )
        schema = repair_json_schema(example) if constraint_mode == "schema" else None
        text = self._generate_text(
            prompt,
            max_new_tokens=max_new_tokens,
            no_repeat_ngram_size=(
                0 if constraint_mode == "schema" else REPAIR_NO_REPEAT_NGRAM_SIZE
            ),
            json_schema=schema,
        )
        return parse_structured_prediction(text)

    def close(self) -> None:
        model = getattr(self, "model", None)
        if model is not None:
            try:
                model.to("cpu")
            except (AttributeError, RuntimeError, ValueError):
                pass
        self.model = None
        self.tokenizer = None
        gc.collect()
        if self._torch.cuda.is_available():
            self._torch.cuda.empty_cache()
