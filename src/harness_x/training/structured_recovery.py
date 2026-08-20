"""Bounded recovery for invalid structured model output.

The primary model output remains evidence.  Recovery never edits malformed JSON or
uses the held-out target values.  When strict parsing fails, the harness may ask the
same already-loaded predictor for one fresh compact JSON attempt using only the
original grounded prompt and the output keys that were already disclosed to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .evaluation import SelfModelPrediction
from .formatting import SelfModelContextProfile
from .models import SelfModelExample


@dataclass(frozen=True)
class StructuredRecoveryAttempt:
    primary_raw_text: str | None
    primary_parse_error: str
    repair_raw_text: str | None
    repair_parse_error: str | None

    @property
    def succeeded(self) -> bool:
        return self.repair_parse_error is None


class BoundedJsonRecoveryPredictor:
    """Transparent predictor decorator with at most one parse-repair generation."""

    def __init__(
        self,
        source: Any,
        *,
        max_attempts: int = 1,
        repair_max_new_tokens: int = 256,
    ) -> None:
        if max_attempts not in {0, 1}:
            raise ValueError("bounded JSON recovery supports only 0 or 1 repair attempt")
        if repair_max_new_tokens < 1:
            raise ValueError("repair_max_new_tokens must be positive")
        self.source = source
        self.max_attempts = max_attempts
        self.repair_max_new_tokens = repair_max_new_tokens
        self.last_recovery: StructuredRecoveryAttempt | None = None

    @property
    def name(self) -> str:
        return self.source.name

    @property
    def token_measurement_kind(self) -> str:
        return str(getattr(self.source, "token_measurement_kind", "tokenizer"))

    def prompt_measurement(
        self,
        example: SelfModelExample,
        profile: SelfModelContextProfile,
    ) -> tuple[int, int]:
        measure = getattr(self.source, "prompt_measurement", None)
        if not callable(measure):
            raise AttributeError("wrapped predictor does not expose prompt_measurement")
        return measure(example, profile)

    def _recover(
        self,
        example: SelfModelExample,
        profile: SelfModelContextProfile,
        primary: SelfModelPrediction,
    ) -> SelfModelPrediction:
        self.last_recovery = None
        if primary.parse_error is None or self.max_attempts == 0:
            return primary

        repair = getattr(self.source, "repair_prediction", None)
        if not callable(repair):
            return primary

        repaired = repair(
            example,
            SelfModelContextProfile(profile),
            max_new_tokens=self.repair_max_new_tokens,
        )
        self.last_recovery = StructuredRecoveryAttempt(
            primary_raw_text=primary.raw_text,
            primary_parse_error=primary.parse_error,
            repair_raw_text=repaired.raw_text,
            repair_parse_error=repaired.parse_error,
        )
        return repaired

    def predict(self, example: SelfModelExample) -> SelfModelPrediction:
        primary = self.source.predict(example)
        profile = SelfModelContextProfile(
            getattr(self.source, "context_profile", SelfModelContextProfile.STANDARD)
        )
        return self._recover(example, profile, primary)

    def predict_with_profile(
        self,
        example: SelfModelExample,
        profile: SelfModelContextProfile,
    ) -> SelfModelPrediction:
        profile = SelfModelContextProfile(profile)
        primary = self.source.predict_with_profile(example, profile)
        return self._recover(example, profile, primary)
