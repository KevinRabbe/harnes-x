"""Reasoning-core runtime adapters."""

from .openai_compatible import (
    OpenAICompatibleConnectionResult,
    OpenAICompatibleReasoningCore,
    OpenAICompatibleSettings,
)
from .transformers_local import TransformersLocalReasoningCore, TransformersLocalSettings

__all__ = [
    "OpenAICompatibleConnectionResult",
    "OpenAICompatibleReasoningCore",
    "OpenAICompatibleSettings",
    "TransformersLocalReasoningCore",
    "TransformersLocalSettings",
]
