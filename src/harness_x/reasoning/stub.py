"""Deterministic reasoning core used as the baseline for fake-to-real swaps."""

from __future__ import annotations

from .base import RawReasoningOutput, ReasoningCoreInfo
from .context_builder import ContextBuildResult


class StubReasoningCore:
    """Return one predeclared structured response with zero model inference."""

    def __init__(self, output: RawReasoningOutput) -> None:
        self._output = output
        self._info = ReasoningCoreInfo(
            name="stub",
            version="stub-v1",
            model="deterministic-script",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context: ContextBuildResult) -> RawReasoningOutput:
        del context
        return self._output
