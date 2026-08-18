"""Replaceable reasoning cores behind a software-owned normalization boundary."""

from .base import (
    RawActionProposal,
    RawProposal,
    RawReasoningOutput,
    ReasoningCore,
    ReasoningCoreError,
    ReasoningCoreInfo,
)
from .context_builder import BoundedContextBuilder, ContextBudget, ContextBuildResult
from .service import ReasoningService
from .stub import StubReasoningCore
from .adapters import OpenAICompatibleReasoningCore, OpenAICompatibleSettings

__all__ = [
    "BoundedContextBuilder",
    "ContextBudget",
    "ContextBuildResult",
    "OpenAICompatibleReasoningCore",
    "OpenAICompatibleSettings",
    "RawActionProposal",
    "RawProposal",
    "RawReasoningOutput",
    "ReasoningCore",
    "ReasoningCoreError",
    "ReasoningCoreInfo",
    "ReasoningService",
    "StubReasoningCore",
]
