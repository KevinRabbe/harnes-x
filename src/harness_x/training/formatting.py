"""Deterministic prompt/target formatting for self-model adapter training."""

from __future__ import annotations

from enum import StrEnum
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

SELF_MODEL_MINIMAL_INSTRUCTION = (
    "Harness X reasoning core. Use only supplied live state. Return the requested JSON "
    "decision. Do not claim authority, execution, mutation, or verification you do not have."
)

# This block is intentionally static.  It represents the kind of architecture explanation
# a generic base model may need repeated in context, but which a self-model adapter should
# eventually be able to internalize as a stable prior.  It never contains live task state,
# permissions, current budgets, current system versions, or current observations.
STATIC_ARCHITECTURE_REFERENCE: dict[str, tuple[str, ...]] = {
    "authority": (
        "The orchestrator owns lifecycle state and externally enforced compute budgets.",
        "Memory repositories own durable memory mutation; a model or gate may only propose writes.",
        "Gates and learned controllers recommend flow decisions; owning software performs mutation.",
        "A tool proposal is not tool execution; execution passes through registry, permission, budget, and verifier boundaries.",
        "System-improvement experiments and promotion are separate authorities with evidence and rollback requirements.",
    ),
    "epistemic": (
        "Observation is not automatically fact, episode is not automatically knowledge, and model output is unverified by default.",
        "Verification is separate from generation, and semantic/procedural promotion requires explicit evidence.",
        "The self-schema is generated from authoritative runtime owners and telemetry rather than model autobiography.",
    ),
    "control": (
        "Reasoning depth, retrieval, verification, and model use are externally budgeted resources.",
        "Hard permissions, governing constraints, and live state cannot be overridden by a recommendation.",
    ),
}


class SelfModelContextProfile(StrEnum):
    """How much stable architecture explanation is repeated in the model prompt.

    ``STANDARD`` is the exact Milestone 13 training/evaluation format and remains the
    default for backwards compatibility. ``RICH`` gives a generic base model additional
    static architecture help. ``MINIMAL`` removes repeatable descriptive metadata but
    preserves all live state required to answer safely.
    """

    RICH = "rich"
    STANDARD = "standard"
    MINIMAL = "minimal"


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


def _user_payload(
    example: SelfModelExample,
    profile: SelfModelContextProfile,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task": example.definition.task,
        "system_version": example.system_version,
        "source_state_fingerprint": example.source_state_fingerprint,
        "input_state": example.input_state,
        "output_requirement": {
            "format": "json_object_only",
            "expected_keys": sorted(example.expected_decision),
        },
    }
    if profile != SelfModelContextProfile.MINIMAL:
        payload["curriculum_family"] = example.definition.family.value
        payload["architecture_family"] = example.definition.architecture_family
    if profile == SelfModelContextProfile.RICH:
        payload["static_architecture_reference"] = STATIC_ARCHITECTURE_REFERENCE
    return payload


def format_self_model_example(
    example: SelfModelExample,
    *,
    context_profile: SelfModelContextProfile = SelfModelContextProfile.STANDARD,
) -> FormattedSelfModelRecord:
    """Format one example without leaking its target into the prompt.

    Training continues to use ``STANDARD``. Context-compression experiments may evaluate
    the exact same held-out example under richer or smaller prompt profiles.
    """

    profile = SelfModelContextProfile(context_profile)
    target = canonical_json(example.expected_decision)
    instruction = (
        SELF_MODEL_MINIMAL_INSTRUCTION
        if profile == SelfModelContextProfile.MINIMAL
        else SELF_MODEL_SYSTEM_INSTRUCTION
    )
    messages = (
        TrainingMessage(role="system", content=instruction),
        TrainingMessage(role="user", content=canonical_json(_user_payload(example, profile))),
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
