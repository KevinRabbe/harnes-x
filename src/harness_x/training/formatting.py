"""Deterministic prompt/target formatting for self-model adapter training."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import SelfModelExample, canonical_json


SELF_MODEL_SYSTEM_INSTRUCTION = (
    "You are the reasoning core operating inside Harness X. Answer only from the "
    "provided grounded system state. Preserve the distinction between observed, "
    "inferred, proposed, and authoritative state. Never invent permissions, state "
    "mutations, verification results, memory writes, or hidden causes. Return one "
    "JSON object matching the requested decision shape."
)


class TrainingMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1)


class FormattedSelfModelRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "self-model-sft-record-v1"
    scenario_id: str
    scenario_fingerprint: str
    architecture_family: str
    curriculum_family: str
    messages: tuple[TrainingMessage, ...]
    target_json: str

    @property
    def prompt_messages(self) -> tuple[TrainingMessage, ...]:
        return self.messages[:-1]


def _user_payload(example: SelfModelExample) -> dict[str, Any]:
    return {
        "task": example.definition.task,
        "curriculum_family": example.definition.family.value,
        "system_version": example.system_version,
        "architecture_family": example.definition.architecture_family,
        "source_state_fingerprint": example.source_state_fingerprint,
        "input_state": example.input_state,
        "output_requirement": {
            "format": "json_object_only",
            "expected_keys": sorted(example.expected_decision),
        },
    }


def format_self_model_example(example: SelfModelExample) -> FormattedSelfModelRecord:
    target = canonical_json(example.expected_decision)
    messages = (
        TrainingMessage(role="system", content=SELF_MODEL_SYSTEM_INSTRUCTION),
        TrainingMessage(role="user", content=canonical_json(_user_payload(example))),
        TrainingMessage(role="assistant", content=target),
    )
    return FormattedSelfModelRecord(
        scenario_id=example.scenario_id,
        scenario_fingerprint=example.scenario_fingerprint,
        architecture_family=example.definition.architecture_family,
        curriculum_family=example.definition.family.value,
        messages=messages,
        target_json=target,
    )


def render_messages_with_tokenizer(
    tokenizer: Any,
    messages: tuple[TrainingMessage, ...],
    *,
    add_generation_prompt: bool,
) -> str:
    raw_messages = [item.model_dump(mode="json") for item in messages]
    if hasattr(tokenizer, "apply_chat_template"):
        return tokenizer.apply_chat_template(
            raw_messages,
            tokenize=False,
            add_generation_prompt=add_generation_prompt,
        )
    # Deterministic fallback for tokenizers without a chat template.
    rendered = "\n".join(f"[{item.role}]\n{item.content}" for item in messages)
    if add_generation_prompt:
        rendered += "\n[assistant]\n"
    return rendered
