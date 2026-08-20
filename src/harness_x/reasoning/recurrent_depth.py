"""Milestone 19A recurrent-depth research surfaces.

Recurrent depth is treated as externally allocated test-time compute.  The model/backend
can *use* an authorized depth but cannot select, increase, or promote its own depth.
Fixed-depth curves are measured before any learned depth selector is allowed to compete.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from .base import RawProposal, RawReasoningOutput, ReasoningCoreError, ReasoningCoreInfo
from .context_builder import ContextBuildResult


_STRICT = ConfigDict(frozen=True, extra="forbid")
RECURRENT_DEPTH_RESEARCH_VERSION = "recurrent-depth-research-v1"
DEPTH_SELECTOR_VERSION = "recurrent-depth-selector-v1"
DEFAULT_DEPTHS = (4, 8, 16, 32, 64, 128)


class RecurrentDepthResearchError(ReasoningCoreError):
    """Fail-closed error for recurrent-depth research boundaries."""


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _context(payload: Mapping[str, JsonValue]) -> ContextBuildResult:
    material = json.loads(_canonical(dict(payload)))
    serialized = _canonical(material)
    return ContextBuildResult(
        fingerprint=_fingerprint(material),
        serialized=serialized,
        payload=material,
        char_count=len(serialized),
        dropped_working_items=0,
        dropped_retrieved_items=0,
        dropped_actions=0,
        self_schema_reduced=False,
    )


class RecurrentDepthAuthorization(BaseModel):
    """Software-owned authorization for one fixed recurrent-depth invocation."""

    model_config = _STRICT

    policy_version: str = "recurrent-depth-authority-v1"
    depth: int = Field(ge=1, le=4096)
    max_recurrent_steps: int = Field(ge=1, le=4096)
    allowed_depths: tuple[int, ...]
    authorization_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_authorization(self) -> "RecurrentDepthAuthorization":
        if self.depth not in self.allowed_depths:
            raise ValueError("authorized recurrent depth is not in allowed_depths")
        if self.depth > self.max_recurrent_steps:
            raise ValueError("authorized recurrent depth exceeds external maximum")
        expected = _fingerprint(
            self.model_dump(mode="json", exclude={"authorization_fingerprint"})
        )
        if expected != self.authorization_fingerprint:
            raise ValueError("recurrent-depth authorization fingerprint mismatch")
        return self


class RecurrentDepthAuthority:
    """External research envelope for recurrent depth.

    This is intentionally separate from the model.  It does not mutate task budgets; it
    limits the fixed-depth experiment surface until recurrent-step accounting becomes a
    first-class runtime budget dimension.
    """

    def __init__(
        self,
        *,
        allowed_depths: Sequence[int] = DEFAULT_DEPTHS,
        max_recurrent_steps: int = 128,
    ) -> None:
        normalized = tuple(sorted(set(int(value) for value in allowed_depths)))
        if not normalized or normalized[0] < 1:
            raise ValueError("allowed_depths must contain positive integers")
        if max_recurrent_steps < 1:
            raise ValueError("max_recurrent_steps must be positive")
        self.allowed_depths = normalized
        self.max_recurrent_steps = max_recurrent_steps

    def authorize(self, depth: int) -> RecurrentDepthAuthorization:
        depth = int(depth)
        if depth not in self.allowed_depths:
            raise RecurrentDepthResearchError(
                f"recurrent depth {depth} is not in the authorized experiment set "
                f"{self.allowed_depths}"
            )
        if depth > self.max_recurrent_steps:
            raise RecurrentDepthResearchError(
                f"recurrent depth {depth} exceeds external maximum "
                f"{self.max_recurrent_steps}"
            )
        payload = {
            "policy_version": "recurrent-depth-authority-v1",
            "depth": depth,
            "max_recurrent_steps": self.max_recurrent_steps,
            "allowed_depths": list(self.allowed_depths),
        }
        return RecurrentDepthAuthorization(
            **payload,
            authorization_fingerprint=_fingerprint(payload),
        )


@runtime_checkable
class RecurrentDepthBackend(Protocol):
    """Backend capable of executing one bounded context at an explicit fixed depth."""

    @property
    def info(self) -> ReasoningCoreInfo: ...

    def generate_at_depth(
        self,
        context: ContextBuildResult,
        depth: int,
    ) -> RawReasoningOutput: ...


class FixedDepthRecurrentCore:
    """ReasoningCore-compatible wrapper around an externally authorized fixed depth."""

    def __init__(
        self,
        backend: RecurrentDepthBackend,
        authorization: RecurrentDepthAuthorization,
    ) -> None:
        self.backend = backend
        self.authorization = authorization
        backend_info = backend.info
        self._info = ReasoningCoreInfo(
            name="fixed_depth_recurrent",
            version=f"fixed-depth-v1-d{authorization.depth}",
            model=backend_info.model,
            transport=f"{backend_info.transport}:recurrent_depth",
            model_inference=backend_info.model_inference,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    @property
    def depth(self) -> int:
        return self.authorization.depth

    def generate(self, context: ContextBuildResult) -> RawReasoningOutput:
        return self.backend.generate_at_depth(context, self.authorization.depth)


_HUGINN_SYSTEM_PROMPT = """You are a replaceable recurrent-depth reasoning core inside Harness X.
You do not own state, memory, tools, permissions, budgets, verification, or recurrence depth.
Return ONLY one JSON object with this exact top-level schema:
{
  "status": "complete|continue|blocked",
  "proposals": [{"summary": "...", "payload": {}}],
  "actions": [{"tool_name": "...", "arguments": {}}],
  "observations": ["short observation"],
  "requested_additional_steps": 0
}
Do not emit chain-of-thought. Do not invent candidate IDs, provenance, permissions,
verification state, state mutations, or additional recurrence depth.
"""


class HuginnTransformersSettings(BaseModel):
    """Optional local Transformers settings for the official Huginn recurrent model."""

    model_config = _STRICT

    model: str = Field(default="tomg-group-umd/huginn-0125", min_length=1)
    device: str = Field(default="auto", min_length=1)
    dtype: Literal["bfloat16", "float16", "float32"] = "bfloat16"
    max_new_tokens: int = Field(default=512, ge=1, le=8192)
    local_files_only: bool = False
    allow_remote_code: bool = False

    @field_validator("model", "device")
    @classmethod
    def normalized_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Huginn adapter text settings cannot be blank")
        return value


class HuginnTransformersBackend:
    """Lazy optional adapter for Huginn's Transformers `num_steps` API.

    Loading arbitrary Hugging Face custom model code is executable-code trust.  The
    operator must opt in with `allow_remote_code=True`; CI never downloads model weights
    or imports this optional runtime.
    """

    def __init__(self, settings: HuginnTransformersSettings | None = None) -> None:
        self.settings = settings or HuginnTransformersSettings()
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._torch: Any | None = None
        self._device: str | None = None
        self._info = ReasoningCoreInfo(
            name="huginn_transformers",
            version="huginn-transformers-v1",
            model=self.settings.model,
            transport="transformers_local",
            model_inference=True,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if not self.settings.allow_remote_code:
            raise RecurrentDepthResearchError(
                "Huginn uses custom Transformers model code. Re-run with explicit "
                "allow_remote_code=True only after trusting the selected model source."
            )
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise RecurrentDepthResearchError(
                'Huginn recurrent-depth inference requires optional dependencies; '
                'install Harness X with `pip install -e ".[recurrent]"`.'
            ) from exc

        dtype = {
            "bfloat16": torch.bfloat16,
            "float16": torch.float16,
            "float32": torch.float32,
        }[self.settings.dtype]
        device = self.settings.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"

        try:
            tokenizer = AutoTokenizer.from_pretrained(
                self.settings.model,
                trust_remote_code=True,
                local_files_only=self.settings.local_files_only,
            )
            model = AutoModelForCausalLM.from_pretrained(
                self.settings.model,
                trust_remote_code=True,
                local_files_only=self.settings.local_files_only,
                torch_dtype=dtype,
            )
            model.eval()
            model.to(device)
        except Exception as exc:
            raise RecurrentDepthResearchError(
                f"failed to load recurrent-depth model {self.settings.model!r}: {exc}"
            ) from exc

        self._torch = torch
        self._tokenizer = tokenizer
        self._model = model
        self._device = device

    def generate_at_depth(
        self,
        context: ContextBuildResult,
        depth: int,
    ) -> RawReasoningOutput:
        self._ensure_loaded()
        assert self._torch is not None
        assert self._tokenizer is not None
        assert self._model is not None
        assert self._device is not None

        messages = [
            {"role": "system", "content": _HUGINN_SYSTEM_PROMPT},
            {"role": "user", "content": context.serialized},
        ]
        try:
            rendered = self._tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        except Exception as exc:
            raise RecurrentDepthResearchError(
                f"Huginn tokenizer chat-template construction failed: {exc}"
            ) from exc

        encoded = self._tokenizer(rendered, return_tensors="pt")
        input_ids = encoded["input_ids"].to(self._device)
        attention_mask = encoded.get("attention_mask")
        if attention_mask is not None:
            attention_mask = attention_mask.to(self._device)

        kwargs: dict[str, Any] = {
            "input_ids": input_ids,
            "max_new_tokens": self.settings.max_new_tokens,
            "do_sample": False,
            "num_steps": int(depth),
            "tokenizer": self._tokenizer,
        }
        if attention_mask is not None:
            kwargs["attention_mask"] = attention_mask

        try:
            with self._torch.inference_mode():
                generated = self._model.generate(**kwargs)
        except Exception as exc:
            raise RecurrentDepthResearchError(
                f"Huginn generation failed at recurrent depth {depth}: {exc}"
            ) from exc

        sequences = generated.sequences if hasattr(generated, "sequences") else generated
        new_tokens = sequences[0][input_ids.shape[-1] :]
        text = self._tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
        if not text:
            raise RecurrentDepthResearchError(
                f"Huginn returned empty completion at recurrent depth {depth}"
            )
        try:
            decoded = json.loads(text)
            return RawReasoningOutput.model_validate(decoded)
        except Exception as exc:
            raise RecurrentDepthResearchError(
                f"Huginn output at recurrent depth {depth} violated the structured "
                f"ReasoningCore schema: {exc}"
            ) from exc

    def close(self) -> None:
        self._model = None
        self._tokenizer = None
        if self._torch is not None:
            try:
                if self._torch.cuda.is_available():
                    self._torch.cuda.empty_cache()
            except Exception:
                pass
        self._torch = None
        self._device = None


class RecurrentDepthBenchmarkCase(BaseModel):
    """Exact-output research case used to measure quality as recurrence increases."""

    model_config = _STRICT

    case_id: str = Field(min_length=1)
    split: Literal["train", "eval"] = "eval"
    instruction: str = Field(min_length=1)
    expected_output: RawReasoningOutput
    difficulty: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)
    context_pressure: float = Field(default=0.0, ge=0.0, le=1.0)
    verifier_rejection_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    remaining_budget_ratio: float = Field(default=1.0, ge=0.0, le=1.0)

    def context(self) -> ContextBuildResult:
        return _context(
            {
                "schema_version": "recurrent-depth-benchmark-context-v1",
                "case_id": self.case_id,
                "instruction": self.instruction,
            }
        )


class RecurrentDepthCaseResult(BaseModel):
    model_config = _STRICT

    case_id: str
    depth: int = Field(ge=1)
    quality: float = Field(ge=0.0, le=1.0)
    exact_match: bool
    normalized_cost: float = Field(ge=0.0)
    error: str | None = None


class RecurrentDepthPoint(BaseModel):
    model_config = _STRICT

    depth: int = Field(ge=1)
    mean_quality: float = Field(ge=0.0, le=1.0)
    exact_accuracy: float = Field(ge=0.0, le=1.0)
    mean_normalized_cost: float = Field(ge=0.0)
    mean_net_value: float
    failure_count: int = Field(ge=0)
    pareto_frontier: bool
    dominated: bool


class FixedDepthCurveReport(BaseModel):
    model_config = _STRICT

    schema_version: str = "fixed-depth-curve-report-v1"
    evidence_kind: Literal["reference_simulator", "model_benchmark"]
    model: str
    depths: tuple[int, ...]
    cases: tuple[RecurrentDepthCaseResult, ...]
    points: tuple[RecurrentDepthPoint, ...]
    best_depth_by_net_value: int
    frontier_depths: tuple[int, ...]
    min_depth_quality: float
    best_quality: float
    quality_gain_over_min_depth: float
    cost_weight: float
    report_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_fingerprint(self) -> "FixedDepthCurveReport":
        expected = _fingerprint(
            self.model_dump(mode="json", exclude={"report_fingerprint"})
        )
        if expected != self.report_fingerprint:
            raise ValueError("fixed-depth curve report fingerprint mismatch")
        return self


def _score_output(expected: RawReasoningOutput, actual: RawReasoningOutput) -> tuple[float, bool]:
    expected_json = expected.model_dump(mode="json")
    actual_json = actual.model_dump(mode="json")
    exact = expected_json == actual_json
    if exact:
        return 1.0, True
    score = 0.0
    if expected.status == actual.status:
        score += 0.25
    if expected.proposals == actual.proposals:
        score += 0.30
    if expected.actions == actual.actions:
        score += 0.20
    if expected.observations == actual.observations:
        score += 0.15
    if expected.requested_additional_steps == actual.requested_additional_steps:
        score += 0.10
    return min(1.0, score), False


def benchmark_fixed_depth_curve(
    cases: Sequence[RecurrentDepthBenchmarkCase],
    backend: RecurrentDepthBackend,
    *,
    depths: Sequence[int] = DEFAULT_DEPTHS,
    authority: RecurrentDepthAuthority | None = None,
    evidence_kind: Literal["reference_simulator", "model_benchmark"] = "model_benchmark",
    cost_weight: float = 0.15,
) -> FixedDepthCurveReport:
    """Measure the same cases at several externally fixed recurrent depths."""

    if not cases:
        raise RecurrentDepthResearchError("fixed-depth benchmark requires at least one case")
    normalized_depths = tuple(sorted(set(int(value) for value in depths)))
    if not normalized_depths or normalized_depths[0] < 1:
        raise RecurrentDepthResearchError("benchmark depths must be positive")
    if cost_weight < 0:
        raise RecurrentDepthResearchError("cost_weight cannot be negative")
    authority = authority or RecurrentDepthAuthority(
        allowed_depths=normalized_depths,
        max_recurrent_steps=max(normalized_depths),
    )

    results: list[RecurrentDepthCaseResult] = []
    max_depth = max(normalized_depths)
    for depth in normalized_depths:
        authorization = authority.authorize(depth)
        core = FixedDepthRecurrentCore(backend, authorization)
        for case in cases:
            quality = 0.0
            exact = False
            error: str | None = None
            try:
                output = core.generate(case.context())
                quality, exact = _score_output(case.expected_output, output)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            results.append(
                RecurrentDepthCaseResult(
                    case_id=case.case_id,
                    depth=depth,
                    quality=quality,
                    exact_match=exact,
                    normalized_cost=depth / max_depth,
                    error=error,
                )
            )

    raw_points: list[dict[str, Any]] = []
    for depth in normalized_depths:
        subset = [item for item in results if item.depth == depth]
        mean_quality = sum(item.quality for item in subset) / len(subset)
        exact_accuracy = sum(item.exact_match for item in subset) / len(subset)
        mean_cost = sum(item.normalized_cost for item in subset) / len(subset)
        raw_points.append(
            {
                "depth": depth,
                "mean_quality": mean_quality,
                "exact_accuracy": exact_accuracy,
                "mean_normalized_cost": mean_cost,
                "mean_net_value": mean_quality - cost_weight * mean_cost,
                "failure_count": sum(item.error is not None for item in subset),
            }
        )

    points: list[RecurrentDepthPoint] = []
    for point in raw_points:
        dominated = any(
            other["mean_quality"] >= point["mean_quality"]
            and other["mean_normalized_cost"] <= point["mean_normalized_cost"]
            and (
                other["mean_quality"] > point["mean_quality"]
                or other["mean_normalized_cost"] < point["mean_normalized_cost"]
            )
            for other in raw_points
        )
        points.append(
            RecurrentDepthPoint(
                **point,
                pareto_frontier=not dominated,
                dominated=dominated,
            )
        )

    best_point = max(
        points,
        key=lambda item: (item.mean_net_value, item.mean_quality, -item.depth),
    )
    min_depth_quality = points[0].mean_quality
    best_quality = max(point.mean_quality for point in points)
    payload = {
        "schema_version": "fixed-depth-curve-report-v1",
        "evidence_kind": evidence_kind,
        "model": backend.info.model,
        "depths": list(normalized_depths),
        "cases": [item.model_dump(mode="json") for item in results],
        "points": [item.model_dump(mode="json") for item in points],
        "best_depth_by_net_value": best_point.depth,
        "frontier_depths": [item.depth for item in points if item.pareto_frontier],
        "min_depth_quality": min_depth_quality,
        "best_quality": best_quality,
        "quality_gain_over_min_depth": best_quality - min_depth_quality,
        "cost_weight": cost_weight,
    }
    return FixedDepthCurveReport(
        **payload,
        report_fingerprint=_fingerprint(payload),
    )


class DepthSelectionState(BaseModel):
    """Pre-decision features available to an external recurrent-depth selector."""

    model_config = _STRICT

    difficulty: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0, le=1.0)
    context_pressure: float = Field(ge=0.0, le=1.0)
    verifier_rejection_rate: float = Field(ge=0.0, le=1.0)
    remaining_budget_ratio: float = Field(ge=0.0, le=1.0)

    def vector(self) -> tuple[float, ...]:
        return (
            self.difficulty,
            self.uncertainty,
            self.context_pressure,
            self.verifier_rejection_rate,
            self.remaining_budget_ratio,
        )


class DepthSelectionExample(BaseModel):
    model_config = _STRICT

    case_id: str
    state: DepthSelectionState
    target_depth: int = Field(ge=1)


class DepthSelectorArtifact(BaseModel):
    model_config = _STRICT

    schema_version: str = "depth-selector-artifact-v1"
    selector_version: str = DEPTH_SELECTOR_VERSION
    allowed_depths: tuple[int, ...]
    feature_names: tuple[str, ...] = (
        "difficulty",
        "uncertainty",
        "context_pressure",
        "verifier_rejection_rate",
        "remaining_budget_ratio",
    )
    centroids: dict[str, tuple[float, ...]]
    training_counts: dict[str, int]
    training_fingerprint: str = Field(min_length=64, max_length=64)
    artifact_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_artifact(self) -> "DepthSelectorArtifact":
        expected = _fingerprint(
            self.model_dump(mode="json", exclude={"artifact_fingerprint"})
        )
        if expected != self.artifact_fingerprint:
            raise ValueError("depth-selector artifact fingerprint mismatch")
        for key, centroid in self.centroids.items():
            if int(key) not in self.allowed_depths:
                raise ValueError("selector centroid references unauthorized depth")
            if len(centroid) != len(self.feature_names):
                raise ValueError("selector centroid feature width mismatch")
        return self


class DeterministicDepthSelector:
    """Conservative hand-authored baseline for external recurrence allocation."""

    def __init__(self, allowed_depths: Sequence[int] = DEFAULT_DEPTHS) -> None:
        self.allowed_depths = tuple(sorted(set(int(value) for value in allowed_depths)))

    def select(self, state: DepthSelectionState) -> int:
        # Keep completion/very-low-budget states shallow; scale by the hardest signal.
        hardness = max(
            state.difficulty,
            state.uncertainty * 0.9,
            state.context_pressure * 0.7,
            state.verifier_rejection_rate,
        )
        if state.remaining_budget_ratio < 0.20:
            desired = 4
        elif hardness < 0.25:
            desired = 4
        elif hardness < 0.50:
            desired = 16
        elif hardness < 0.75:
            desired = 32
        else:
            desired = 64
        return min(self.allowed_depths, key=lambda item: (abs(item - desired), item))


class LearnedDepthSelector:
    """Dependency-free nearest-centroid selector trained only from fixed-depth evidence."""

    def __init__(self, artifact: DepthSelectorArtifact) -> None:
        self.artifact = artifact

    @classmethod
    def train(
        cls,
        examples: Sequence[DepthSelectionExample],
        *,
        allowed_depths: Sequence[int],
    ) -> "LearnedDepthSelector":
        if not examples:
            raise RecurrentDepthResearchError("depth-selector training requires examples")
        allowed = tuple(sorted(set(int(value) for value in allowed_depths)))
        grouped: dict[int, list[tuple[float, ...]]] = defaultdict(list)
        for example in examples:
            if example.target_depth not in allowed:
                raise RecurrentDepthResearchError(
                    f"training target depth {example.target_depth} is not authorized"
                )
            grouped[example.target_depth].append(example.state.vector())
        centroids: dict[str, tuple[float, ...]] = {}
        counts: dict[str, int] = {}
        for depth, vectors in sorted(grouped.items()):
            width = len(vectors[0])
            centroid = tuple(
                sum(vector[index] for vector in vectors) / len(vectors)
                for index in range(width)
            )
            centroids[str(depth)] = centroid
            counts[str(depth)] = len(vectors)
        training_payload = [item.model_dump(mode="json") for item in examples]
        payload = {
            "schema_version": "depth-selector-artifact-v1",
            "selector_version": DEPTH_SELECTOR_VERSION,
            "allowed_depths": list(allowed),
            "feature_names": [
                "difficulty",
                "uncertainty",
                "context_pressure",
                "verifier_rejection_rate",
                "remaining_budget_ratio",
            ],
            "centroids": {key: list(value) for key, value in centroids.items()},
            "training_counts": counts,
            "training_fingerprint": _fingerprint(training_payload),
        }
        artifact = DepthSelectorArtifact(
            **payload,
            artifact_fingerprint=_fingerprint(payload),
        )
        return cls(artifact)

    def select(self, state: DepthSelectionState) -> int:
        vector = state.vector()
        candidates: list[tuple[float, int]] = []
        for key, centroid in self.artifact.centroids.items():
            distance = math.sqrt(
                sum((left - right) ** 2 for left, right in zip(vector, centroid))
            )
            candidates.append((distance, int(key)))
        if not candidates:
            raise RecurrentDepthResearchError("learned depth selector has no centroids")
        return min(candidates)[1]

    def write(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            self.artifact.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "LearnedDepthSelector":
        try:
            artifact = DepthSelectorArtifact.model_validate_json(
                Path(path).read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise RecurrentDepthResearchError(
                f"invalid recurrent-depth selector artifact: {exc}"
            ) from exc
        return cls(artifact)


class DepthSelectionCaseResult(BaseModel):
    model_config = _STRICT

    case_id: str
    oracle_depth: int
    selected_depth: int
    quality: float = Field(ge=0.0, le=1.0)
    normalized_cost: float = Field(ge=0.0)
    net_value: float
    exact_depth_match: bool


class DepthSelectorMetrics(BaseModel):
    model_config = _STRICT

    mean_quality: float = Field(ge=0.0, le=1.0)
    mean_normalized_cost: float = Field(ge=0.0)
    mean_net_value: float
    exact_depth_accuracy: float = Field(ge=0.0, le=1.0)


class DepthSelectorComparison(BaseModel):
    model_config = _STRICT

    schema_version: str = "depth-selector-comparison-v1"
    deterministic: DepthSelectorMetrics
    learned: DepthSelectorMetrics
    deterministic_cases: tuple[DepthSelectionCaseResult, ...]
    learned_cases: tuple[DepthSelectionCaseResult, ...]
    net_value_gain: float
    quality_delta: float
    cost_delta: float
    learned_frontier_improved: bool


def _state_from_case(case: RecurrentDepthBenchmarkCase) -> DepthSelectionState:
    return DepthSelectionState(
        difficulty=case.difficulty,
        uncertainty=case.uncertainty,
        context_pressure=case.context_pressure,
        verifier_rejection_rate=case.verifier_rejection_rate,
        remaining_budget_ratio=case.remaining_budget_ratio,
    )


def prepare_depth_selection_examples(
    cases: Sequence[RecurrentDepthBenchmarkCase],
    curve: FixedDepthCurveReport,
    *,
    min_quality: float = 0.999,
) -> tuple[DepthSelectionExample, ...]:
    """Choose the shallowest fixed depth that actually solved each training case."""

    by_case: dict[str, list[RecurrentDepthCaseResult]] = defaultdict(list)
    for result in curve.cases:
        by_case[result.case_id].append(result)
    examples: list[DepthSelectionExample] = []
    for case in cases:
        solved = sorted(
            (
                result
                for result in by_case.get(case.case_id, ())
                if result.error is None and result.quality >= min_quality
            ),
            key=lambda item: item.depth,
        )
        if not solved:
            continue
        examples.append(
            DepthSelectionExample(
                case_id=case.case_id,
                state=_state_from_case(case),
                target_depth=solved[0].depth,
            )
        )
    return tuple(examples)


def _case_quality_lookup(
    curve: FixedDepthCurveReport,
) -> dict[tuple[str, int], RecurrentDepthCaseResult]:
    return {(item.case_id, item.depth): item for item in curve.cases}


def compare_depth_selectors(
    cases: Sequence[RecurrentDepthBenchmarkCase],
    eval_curve: FixedDepthCurveReport,
    deterministic: DeterministicDepthSelector,
    learned: LearnedDepthSelector,
    *,
    cost_weight: float = 0.15,
    min_net_gain: float = 0.01,
) -> DepthSelectorComparison:
    lookup = _case_quality_lookup(eval_curve)
    max_depth = max(eval_curve.depths)

    def run(selector: Any) -> tuple[DepthSelectorMetrics, tuple[DepthSelectionCaseResult, ...]]:
        outputs: list[DepthSelectionCaseResult] = []
        for case in cases:
            state = _state_from_case(case)
            selected = selector.select(state)
            available = sorted(
                depth for depth in eval_curve.depths if (case.case_id, depth) in lookup
            )
            if selected not in available:
                # Selector recommendation is outside measured evidence: fail closed to
                # the shallowest measured point and score it as a mismatch.
                selected = available[0]
            measured = lookup[(case.case_id, selected)]
            solved = [
                item
                for item in (lookup[(case.case_id, depth)] for depth in available)
                if item.error is None and item.quality >= 0.999
            ]
            oracle = min((item.depth for item in solved), default=max(available))
            cost = selected / max_depth
            outputs.append(
                DepthSelectionCaseResult(
                    case_id=case.case_id,
                    oracle_depth=oracle,
                    selected_depth=selected,
                    quality=measured.quality,
                    normalized_cost=cost,
                    net_value=measured.quality - cost_weight * cost,
                    exact_depth_match=selected == oracle,
                )
            )
        metrics = DepthSelectorMetrics(
            mean_quality=sum(item.quality for item in outputs) / len(outputs),
            mean_normalized_cost=sum(item.normalized_cost for item in outputs) / len(outputs),
            mean_net_value=sum(item.net_value for item in outputs) / len(outputs),
            exact_depth_accuracy=sum(item.exact_depth_match for item in outputs) / len(outputs),
        )
        return metrics, tuple(outputs)

    det_metrics, det_cases = run(deterministic)
    learned_metrics, learned_cases = run(learned)
    gain = learned_metrics.mean_net_value - det_metrics.mean_net_value
    quality_delta = learned_metrics.mean_quality - det_metrics.mean_quality
    cost_delta = learned_metrics.mean_normalized_cost - det_metrics.mean_normalized_cost
    improved = (
        gain >= min_net_gain
        and quality_delta >= -1e-9
        and learned_metrics.mean_quality >= det_metrics.mean_quality
    )
    return DepthSelectorComparison(
        deterministic=det_metrics,
        learned=learned_metrics,
        deterministic_cases=det_cases,
        learned_cases=learned_cases,
        net_value_gain=gain,
        quality_delta=quality_delta,
        cost_delta=cost_delta,
        learned_frontier_improved=improved,
    )


class _ReferenceSpec(BaseModel):
    model_config = _STRICT
    required_depth: int
    expected_output: RawReasoningOutput


class ReferenceRecurrentDepthBackend:
    """Deterministic recurrent-depth simulator used only to qualify research mechanics."""

    def __init__(self, specs: Mapping[str, _ReferenceSpec]) -> None:
        self.specs = dict(specs)
        self._info = ReasoningCoreInfo(
            name="reference_recurrent_depth",
            version="reference-recurrent-v1",
            model="deterministic-reference-recurrent-core",
            transport="in_process_reference",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate_at_depth(
        self,
        context: ContextBuildResult,
        depth: int,
    ) -> RawReasoningOutput:
        case_id = context.payload.get("case_id")
        if not isinstance(case_id, str) or case_id not in self.specs:
            raise RecurrentDepthResearchError("reference recurrent case is unknown")
        spec = self.specs[case_id]
        if depth >= spec.required_depth:
            return spec.expected_output
        return RawReasoningOutput(
            status="continue",
            proposals=(
                RawProposal(
                    summary="insufficient recurrent depth",
                    payload={
                        "case_id": case_id,
                        "observed_depth": depth,
                    },
                ),
            ),
            requested_additional_steps=1,
        )


def _expected(tag: str) -> RawReasoningOutput:
    return RawReasoningOutput(
        status="complete",
        proposals=(
            RawProposal(
                summary=f"resolved {tag}",
                payload={"answer": tag, "verified_shape": True},
            ),
        ),
        observations=(f"solution:{tag}",),
        requested_additional_steps=0,
    )


def build_reference_recurrent_depth_fixture(
) -> tuple[
    tuple[RecurrentDepthBenchmarkCase, ...],
    tuple[RecurrentDepthBenchmarkCase, ...],
    ReferenceRecurrentDepthBackend,
]:
    """Create disjoint train/eval cases with known minimal recurrent depths.

    The fixture is explicitly simulator evidence; it is not a claim about Huginn or any
    production reasoning workload.
    """

    train_specs = [
        ("train_easy_a", 4, 0.08, 0.05, 0.05, 0.00),
        ("train_easy_b", 4, 0.14, 0.10, 0.08, 0.00),
        ("train_mid_a", 8, 0.32, 0.24, 0.12, 0.00),
        ("train_mid_b", 8, 0.38, 0.28, 0.18, 0.05),
        ("train_reason_a", 16, 0.52, 0.40, 0.25, 0.10),
        ("train_reason_b", 16, 0.58, 0.48, 0.28, 0.10),
        ("train_hard_a", 32, 0.72, 0.58, 0.42, 0.22),
        ("train_hard_b", 32, 0.76, 0.64, 0.46, 0.28),
        ("train_deep_a", 64, 0.91, 0.78, 0.55, 0.35),
        ("train_deep_b", 64, 0.96, 0.84, 0.62, 0.42),
    ]
    eval_specs = [
        ("eval_easy", 4, 0.10, 0.06, 0.06, 0.00),
        ("eval_mid", 8, 0.35, 0.25, 0.14, 0.02),
        ("eval_reason", 16, 0.55, 0.44, 0.26, 0.08),
        ("eval_hard", 32, 0.74, 0.61, 0.44, 0.24),
        ("eval_deep", 64, 0.93, 0.81, 0.58, 0.38),
    ]
    all_specs = [*train_specs, *eval_specs]
    private_specs: dict[str, _ReferenceSpec] = {}
    train: list[RecurrentDepthBenchmarkCase] = []
    eval_cases: list[RecurrentDepthBenchmarkCase] = []
    for case_id, required, difficulty, uncertainty, pressure, rejection in all_specs:
        expected = _expected(case_id)
        private_specs[case_id] = _ReferenceSpec(
            required_depth=required,
            expected_output=expected,
        )
        case = RecurrentDepthBenchmarkCase(
            case_id=case_id,
            split="train" if case_id.startswith("train_") else "eval",
            instruction=f"Resolve deterministic recurrent research case {case_id}.",
            expected_output=expected,
            difficulty=difficulty,
            uncertainty=uncertainty,
            context_pressure=pressure,
            verifier_rejection_rate=rejection,
            remaining_budget_ratio=max(0.25, 1.0 - difficulty * 0.45),
        )
        (train if case.split == "train" else eval_cases).append(case)
    return tuple(train), tuple(eval_cases), ReferenceRecurrentDepthBackend(private_specs)


class RecurrentDepthResearchReport(BaseModel):
    model_config = _STRICT

    schema_version: str = "recurrent-depth-research-report-v1"
    research_version: str = RECURRENT_DEPTH_RESEARCH_VERSION
    evidence_kind: Literal["reference_simulator", "model_benchmark"]
    fixed_depth_curve: FixedDepthCurveReport
    selector_comparison: DepthSelectorComparison
    learned_selector: DepthSelectorArtifact
    fixed_depth_improved: bool
    selector_improved: bool
    passed: bool
    limitations: tuple[str, ...]
    report_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_fingerprint(self) -> "RecurrentDepthResearchReport":
        expected = _fingerprint(
            self.model_dump(mode="json", exclude={"report_fingerprint"})
        )
        if expected != self.report_fingerprint:
            raise ValueError("recurrent-depth research report fingerprint mismatch")
        return self


def run_recurrent_depth_research(
    train_cases: Sequence[RecurrentDepthBenchmarkCase],
    eval_cases: Sequence[RecurrentDepthBenchmarkCase],
    backend: RecurrentDepthBackend,
    *,
    depths: Sequence[int] = DEFAULT_DEPTHS,
    evidence_kind: Literal["reference_simulator", "model_benchmark"],
    cost_weight: float = 0.15,
    min_fixed_quality_gain: float = 0.10,
    min_selector_net_gain: float = 0.005,
) -> RecurrentDepthResearchReport:
    if not train_cases or not eval_cases:
        raise RecurrentDepthResearchError(
            "recurrent-depth research requires disjoint non-empty train and eval cases"
        )
    train_ids = {case.case_id for case in train_cases}
    eval_ids = {case.case_id for case in eval_cases}
    if train_ids & eval_ids:
        raise RecurrentDepthResearchError("recurrent-depth train/eval case IDs overlap")

    normalized_depths = tuple(sorted(set(int(value) for value in depths)))
    authority = RecurrentDepthAuthority(
        allowed_depths=normalized_depths,
        max_recurrent_steps=max(normalized_depths),
    )
    train_curve = benchmark_fixed_depth_curve(
        train_cases,
        backend,
        depths=normalized_depths,
        authority=authority,
        evidence_kind=evidence_kind,
        cost_weight=cost_weight,
    )
    eval_curve = benchmark_fixed_depth_curve(
        eval_cases,
        backend,
        depths=normalized_depths,
        authority=authority,
        evidence_kind=evidence_kind,
        cost_weight=cost_weight,
    )
    examples = prepare_depth_selection_examples(train_cases, train_curve)
    if not examples:
        raise RecurrentDepthResearchError(
            "no training case reached the selector's quality threshold at any measured depth"
        )
    learned = LearnedDepthSelector.train(
        examples,
        allowed_depths=normalized_depths,
    )
    deterministic = DeterministicDepthSelector(normalized_depths)
    selector = compare_depth_selectors(
        eval_cases,
        eval_curve,
        deterministic,
        learned,
        cost_weight=cost_weight,
        min_net_gain=min_selector_net_gain,
    )
    fixed_improved = (
        eval_curve.quality_gain_over_min_depth >= min_fixed_quality_gain
        and eval_curve.best_quality >= 0.80
    )
    selector_improved = selector.learned_frontier_improved
    limitations = (
        "recurrent-step cost is a normalized research proxy, not wall-clock/GPU telemetry",
        "recurrent depth is not yet a first-class TaskOrchestrator budget dimension",
        "learned selector is external and recommendation-only; no core-level halting is trained",
        "reference-simulator success is not production-model evidence",
    )
    payload = {
        "schema_version": "recurrent-depth-research-report-v1",
        "research_version": RECURRENT_DEPTH_RESEARCH_VERSION,
        "evidence_kind": evidence_kind,
        "fixed_depth_curve": eval_curve.model_dump(mode="json"),
        "selector_comparison": selector.model_dump(mode="json"),
        "learned_selector": learned.artifact.model_dump(mode="json"),
        "fixed_depth_improved": fixed_improved,
        "selector_improved": selector_improved,
        "passed": fixed_improved and selector_improved,
        "limitations": list(limitations),
    }
    return RecurrentDepthResearchReport(
        **payload,
        report_fingerprint=_fingerprint(payload),
    )


def run_reference_recurrent_depth_research(
    *,
    depths: Sequence[int] = DEFAULT_DEPTHS,
) -> RecurrentDepthResearchReport:
    train, eval_cases, backend = build_reference_recurrent_depth_fixture()
    return run_recurrent_depth_research(
        train,
        eval_cases,
        backend,
        depths=depths,
        evidence_kind="reference_simulator",
    )


def load_recurrent_depth_cases(
    path: str | Path,
) -> tuple[tuple[RecurrentDepthBenchmarkCase, ...], tuple[RecurrentDepthBenchmarkCase, ...]]:
    source = Path(path)
    if not source.is_file():
        raise RecurrentDepthResearchError(f"recurrent-depth case JSONL does not exist: {source}")
    train: list[RecurrentDepthBenchmarkCase] = []
    eval_cases: list[RecurrentDepthBenchmarkCase] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = RecurrentDepthBenchmarkCase.model_validate_json(line)
        except Exception as exc:
            raise RecurrentDepthResearchError(
                f"invalid recurrent-depth case at line {line_number}: {exc}"
            ) from exc
        (train if case.split == "train" else eval_cases).append(case)
    if not train or not eval_cases:
        raise RecurrentDepthResearchError(
            "recurrent-depth case file must contain both train and eval splits"
        )
    train_ids = {case.case_id for case in train}
    eval_ids = {case.case_id for case in eval_cases}
    if train_ids & eval_ids:
        raise RecurrentDepthResearchError("recurrent-depth case IDs overlap across splits")
    return tuple(train), tuple(eval_cases)
