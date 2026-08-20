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
    format_self_model_example,
    render_messages_with_tokenizer,
)
from .models import SelfModelExample


_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


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
        prompt = self._render_prompt(example, SelfModelContextProfile(profile))
        encoded = self.tokenizer(prompt, return_tensors="pt")
        try:
            device = next(self.model.parameters()).device
            encoded = {key: value.to(device) for key, value in encoded.items()}
        except (StopIteration, AttributeError):
            pass
        with self._torch.no_grad():
            output = self.model.generate(
                **encoded,
                do_sample=False,
                max_new_tokens=self.max_new_tokens,
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )
        input_length = encoded["input_ids"].shape[-1]
        generated = output[0][input_length:]
        text = self.tokenizer.decode(generated, skip_special_tokens=True)
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
