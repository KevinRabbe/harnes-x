"""Milestone 19B self-model context-compression benchmark.

The experiment asks a narrow question: after self-model training, can stable Harness X
operating knowledge move out of repeated prompt text without losing grounded behavior?
Live state is never compressed away.  Every profile retains the task, current system
version, source-state fingerprint, live input state, and requested output shape.
"""

from __future__ import annotations

from math import ceil
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .evaluation import SelfModelEvaluationReport, SelfModelPrediction, evaluate_self_model
from .formatting import SelfModelContextProfile, format_self_model_example
from .models import CurriculumFamily, SelfModelExample


class ProfileAwareSelfModelPredictor(Protocol):
    @property
    def name(self) -> str: ...

    def predict_with_profile(
        self,
        example: SelfModelExample,
        profile: SelfModelContextProfile,
    ) -> SelfModelPrediction: ...


class ContextProfileEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "self-model-context-profile-evaluation-v1"
    profile: SelfModelContextProfile
    predictor_name: str
    evaluation: SelfModelEvaluationReport
    mean_prompt_chars: float = Field(gt=0.0)
    mean_prompt_tokens: float | None = Field(default=None, gt=0.0)
    token_measurement_kind: str
    exact_accuracy_per_1k_chars: float = Field(ge=0.0)
    exact_accuracy_per_1k_tokens: float | None = Field(default=None, ge=0.0)


class ContextProfileQualification(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile: SelfModelContextProfile
    qualified: bool
    reasons: tuple[str, ...]
    context_reduction_ratio: float
    token_reduction_ratio: float | None
    exact_accuracy_delta_vs_adapter_rich: float
    exact_accuracy_delta_vs_base_rich: float
    structural_accuracy_delta_vs_adapter_rich: float
    diagnostic_accuracy_delta_vs_adapter_rich: float
    efficiency_gain_ratio_vs_adapter_rich: float


class ContextCompressionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_context_reduction_ratio: float = Field(default=0.15, ge=0.0, lt=1.0)
    max_exact_accuracy_drop_vs_adapter_rich: float = Field(default=0.02, ge=0.0, le=1.0)
    max_structural_accuracy_drop: float = Field(default=0.0, ge=0.0, le=1.0)
    max_diagnostic_accuracy_drop: float = Field(default=0.02, ge=0.0, le=1.0)
    max_authority_violation_delta: float = Field(default=0.0, ge=0.0, le=1.0)
    max_parse_failure_delta: float = Field(default=0.0, ge=0.0, le=1.0)
    require_at_least_base_rich_accuracy: bool = True


class ContextCompressionReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "self-model-context-compression-report-v1"
    evidence_kind: str
    evaluation_fingerprint: str = Field(min_length=64, max_length=64)
    base_rich: ContextProfileEvaluation
    adapter_rich: ContextProfileEvaluation
    adapter_standard: ContextProfileEvaluation
    adapter_minimal: ContextProfileEvaluation
    standard_qualification: ContextProfileQualification
    minimal_qualification: ContextProfileQualification
    selected_profile: SelfModelContextProfile
    compression_qualified: bool
    selected_context_reduction_ratio: float
    selected_token_reduction_ratio: float | None
    selected_exact_accuracy_delta_vs_base_rich: float
    selected_efficiency_gain_ratio_vs_adapter_rich: float


def _family_accuracy(report: SelfModelEvaluationReport, family: CurriculumFamily) -> float:
    for item in report.per_family:
        if item.family == family.value:
            return item.exact_accuracy
    return 1.0


class _ProfilePredictor:
    def __init__(
        self,
        source: ProfileAwareSelfModelPredictor,
        profile: SelfModelContextProfile,
    ) -> None:
        self.source = source
        self.profile = profile

    @property
    def name(self) -> str:
        return f"{self.source.name}:{self.profile.value}"

    def predict(self, example: SelfModelExample) -> SelfModelPrediction:
        return self.source.predict_with_profile(example, self.profile)


def _fallback_prompt_chars(example: SelfModelExample, profile: SelfModelContextProfile) -> int:
    record = format_self_model_example(example, context_profile=profile)
    # Stable model-agnostic measurement. Tokenizer-aware predictors can override this
    # through ``prompt_measurement`` below.
    return sum(len(item.role) + len(item.content) + 2 for item in record.prompt_messages)


def _evaluate_profile(
    examples: tuple[SelfModelExample, ...],
    predictor: ProfileAwareSelfModelPredictor,
    profile: SelfModelContextProfile,
) -> ContextProfileEvaluation:
    evaluation = evaluate_self_model(examples, _ProfilePredictor(predictor, profile))
    measure = getattr(predictor, "prompt_measurement", None)
    chars: list[int] = []
    tokens: list[int] = []
    if callable(measure):
        for example in examples:
            char_count, token_count = measure(example, profile)
            chars.append(int(char_count))
            tokens.append(int(token_count))
        token_kind = str(getattr(predictor, "token_measurement_kind", "tokenizer"))
    else:
        chars = [_fallback_prompt_chars(example, profile) for example in examples]
        token_kind = "unavailable"

    mean_chars = sum(chars) / len(chars)
    mean_tokens = (sum(tokens) / len(tokens)) if tokens else None
    return ContextProfileEvaluation(
        profile=profile,
        predictor_name=predictor.name,
        evaluation=evaluation,
        mean_prompt_chars=mean_chars,
        mean_prompt_tokens=mean_tokens,
        token_measurement_kind=token_kind,
        exact_accuracy_per_1k_chars=evaluation.exact_accuracy * 1000.0 / mean_chars,
        exact_accuracy_per_1k_tokens=(
            evaluation.exact_accuracy * 1000.0 / mean_tokens
            if mean_tokens is not None
            else None
        ),
    )


def _reduction(candidate: float, rich: float) -> float:
    return 1.0 - (candidate / rich)


def _qualify(
    *,
    base_rich: ContextProfileEvaluation,
    adapter_rich: ContextProfileEvaluation,
    candidate: ContextProfileEvaluation,
    policy: ContextCompressionPolicy,
) -> ContextProfileQualification:
    rich_eval = adapter_rich.evaluation
    candidate_eval = candidate.evaluation
    context_reduction = _reduction(candidate.mean_prompt_chars, adapter_rich.mean_prompt_chars)
    token_reduction = None
    if (
        candidate.mean_prompt_tokens is not None
        and adapter_rich.mean_prompt_tokens is not None
    ):
        token_reduction = _reduction(
            candidate.mean_prompt_tokens, adapter_rich.mean_prompt_tokens
        )
    effective_reduction = token_reduction if token_reduction is not None else context_reduction

    exact_delta_rich = candidate_eval.exact_accuracy - rich_eval.exact_accuracy
    exact_delta_base = candidate_eval.exact_accuracy - base_rich.evaluation.exact_accuracy
    structural_delta = _family_accuracy(
        candidate_eval, CurriculumFamily.STRUCTURAL
    ) - _family_accuracy(rich_eval, CurriculumFamily.STRUCTURAL)
    diagnostic_delta = _family_accuracy(
        candidate_eval, CurriculumFamily.DIAGNOSTIC
    ) - _family_accuracy(rich_eval, CurriculumFamily.DIAGNOSTIC)
    authority_delta = (
        candidate_eval.authority_violation_rate - rich_eval.authority_violation_rate
    )
    parse_delta = candidate_eval.parse_failure_rate - rich_eval.parse_failure_rate

    reasons: list[str] = []
    if effective_reduction < policy.min_context_reduction_ratio:
        reasons.append("insufficient_context_reduction")
    if exact_delta_rich < -policy.max_exact_accuracy_drop_vs_adapter_rich:
        reasons.append("exact_accuracy_regression")
    if structural_delta < -policy.max_structural_accuracy_drop:
        reasons.append("structural_accuracy_regression")
    if diagnostic_delta < -policy.max_diagnostic_accuracy_drop:
        reasons.append("diagnostic_accuracy_regression")
    if authority_delta > policy.max_authority_violation_delta:
        reasons.append("authority_violation_regression")
    if parse_delta > policy.max_parse_failure_delta:
        reasons.append("parse_failure_regression")
    if policy.require_at_least_base_rich_accuracy and exact_delta_base < 0.0:
        reasons.append("worse_than_base_with_rich_context")

    efficiency_gain = (
        candidate.exact_accuracy_per_1k_chars
        / adapter_rich.exact_accuracy_per_1k_chars
        - 1.0
        if adapter_rich.exact_accuracy_per_1k_chars > 0.0
        else 0.0
    )
    return ContextProfileQualification(
        profile=candidate.profile,
        qualified=not reasons,
        reasons=tuple(reasons),
        context_reduction_ratio=context_reduction,
        token_reduction_ratio=token_reduction,
        exact_accuracy_delta_vs_adapter_rich=exact_delta_rich,
        exact_accuracy_delta_vs_base_rich=exact_delta_base,
        structural_accuracy_delta_vs_adapter_rich=structural_delta,
        diagnostic_accuracy_delta_vs_adapter_rich=diagnostic_delta,
        efficiency_gain_ratio_vs_adapter_rich=efficiency_gain,
    )


def evaluate_context_compression(
    examples: tuple[SelfModelExample, ...],
    *,
    base_predictor: ProfileAwareSelfModelPredictor,
    adapter_predictor: ProfileAwareSelfModelPredictor,
    policy: ContextCompressionPolicy | None = None,
    evidence_kind: str = "empirical_model",
) -> ContextCompressionReport:
    if not examples:
        raise ValueError("context compression evaluation requires at least one example")
    policy = policy or ContextCompressionPolicy()

    base_rich = _evaluate_profile(examples, base_predictor, SelfModelContextProfile.RICH)
    adapter_rich = _evaluate_profile(examples, adapter_predictor, SelfModelContextProfile.RICH)
    standard = _evaluate_profile(
        examples, adapter_predictor, SelfModelContextProfile.STANDARD
    )
    minimal = _evaluate_profile(examples, adapter_predictor, SelfModelContextProfile.MINIMAL)

    fingerprints = {
        point.evaluation.evaluation_fingerprint
        for point in (base_rich, adapter_rich, standard, minimal)
    }
    if len(fingerprints) != 1:
        raise ValueError("all context profiles must evaluate the exact same held-out cases")

    standard_q = _qualify(
        base_rich=base_rich,
        adapter_rich=adapter_rich,
        candidate=standard,
        policy=policy,
    )
    minimal_q = _qualify(
        base_rich=base_rich,
        adapter_rich=adapter_rich,
        candidate=minimal,
        policy=policy,
    )
    qualified = [
        (standard, standard_q),
        (minimal, minimal_q),
    ]
    qualified = [item for item in qualified if item[1].qualified]
    if qualified:
        selected, selected_q = min(qualified, key=lambda item: item[0].mean_prompt_chars)
        compression_qualified = True
    else:
        selected = adapter_rich
        selected_q = ContextProfileQualification(
            profile=SelfModelContextProfile.RICH,
            qualified=True,
            reasons=(),
            context_reduction_ratio=0.0,
            token_reduction_ratio=0.0 if adapter_rich.mean_prompt_tokens is not None else None,
            exact_accuracy_delta_vs_adapter_rich=0.0,
            exact_accuracy_delta_vs_base_rich=(
                adapter_rich.evaluation.exact_accuracy - base_rich.evaluation.exact_accuracy
            ),
            structural_accuracy_delta_vs_adapter_rich=0.0,
            diagnostic_accuracy_delta_vs_adapter_rich=0.0,
            efficiency_gain_ratio_vs_adapter_rich=0.0,
        )
        compression_qualified = False

    return ContextCompressionReport(
        evidence_kind=evidence_kind,
        evaluation_fingerprint=fingerprints.pop(),
        base_rich=base_rich,
        adapter_rich=adapter_rich,
        adapter_standard=standard,
        adapter_minimal=minimal,
        standard_qualification=standard_q,
        minimal_qualification=minimal_q,
        selected_profile=selected.profile,
        compression_qualified=compression_qualified,
        selected_context_reduction_ratio=selected_q.context_reduction_ratio,
        selected_token_reduction_ratio=selected_q.token_reduction_ratio,
        selected_exact_accuracy_delta_vs_base_rich=selected_q.exact_accuracy_delta_vs_base_rich,
        selected_efficiency_gain_ratio_vs_adapter_rich=selected_q.efficiency_gain_ratio_vs_adapter_rich,
    )


class ReferenceContextCompressionPredictor:
    """Deterministic CI fixture, explicitly not evidence about a trained real model."""

    token_measurement_kind = "estimated_char4"

    def __init__(self, role: str) -> None:
        if role not in {"base", "adapter"}:
            raise ValueError("reference role must be base or adapter")
        self.role = role

    @property
    def name(self) -> str:
        return f"reference-{self.role}"

    def prompt_measurement(
        self,
        example: SelfModelExample,
        profile: SelfModelContextProfile,
    ) -> tuple[int, int]:
        chars = _fallback_prompt_chars(example, profile)
        return chars, max(1, ceil(chars / 4.0))

    def predict_with_profile(
        self,
        example: SelfModelExample,
        profile: SelfModelContextProfile,
    ) -> SelfModelPrediction:
        profile = SelfModelContextProfile(profile)
        family = example.definition.family
        if self.role == "base":
            # The generic base receives the most architecture help but still misses
            # reasoning-heavy diagnostic/causal cases in this mechanics fixture.
            if family in {CurriculumFamily.STRUCTURAL, CurriculumFamily.OPERATIONAL}:
                return SelfModelPrediction(decision=dict(example.expected_decision), confidence=0.85)
            return SelfModelPrediction(decision={"unknown": True}, confidence=0.45)

        # The simulated trained adapter retains the task under STANDARD context.  The
        # MINIMAL profile is intentionally too aggressive for diagnostics, proving that
        # the benchmark can reject compression instead of automatically rewarding it.
        if profile in {SelfModelContextProfile.RICH, SelfModelContextProfile.STANDARD}:
            return SelfModelPrediction(decision=dict(example.expected_decision), confidence=0.95)
        if family == CurriculumFamily.DIAGNOSTIC:
            return SelfModelPrediction(decision={"uncertainty": "insufficient"}, confidence=0.55)
        return SelfModelPrediction(decision=dict(example.expected_decision), confidence=0.90)
