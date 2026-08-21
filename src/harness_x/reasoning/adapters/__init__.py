"""Reasoning-core runtime adapters."""

from .openai_compatible import OpenAICompatibleReasoningCore, OpenAICompatibleSettings
from .transformers_local import TransformersLocalReasoningCore, TransformersLocalSettings

__all__ = [
    "OpenAICompatibleReasoningCore",
    "OpenAICompatibleSettings",
    "TransformersLocalReasoningCore",
    "TransformersLocalSettings",
]
