"""Per-case empirical self-model evaluation observability.

Milestone 20.2 records the externally visible prediction boundary, not hidden model
reasoning. Each evaluated held-out case stores its grounded expected decision, raw
model output, parsed decision, parse status, exact/field score and authority-boundary
status. Records are append-only JSONL so a later evaluation failure does not erase the
cases that already ran.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import empirical_experiment as _empirical
from .empirical_experiment import (
    EmpiricalAdapterExperimentReport,
    FileDigest,
    digest_tree,
)
from .empirical_safe import run_isolated_empirical_adapter_experiment
from .evaluation import (
    FORBIDDEN_AUTHORITY_KEYS,
    GeneralRegressionResult,
    SelfModelPrediction,
)
from .formatting import SelfModelContextProfile
from .models import SelfModelExample, canonical_json


class EvaluationCaseRecord(BaseModel):
    """One auditable held-out prediction boundary."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "self-model-evaluation-case-v1"
    evaluation_name: str
    predictor_name: str
    profile: str
    scenario_id: str
    scenario_fingerprint: str = Field(min_length=64, max_length=64)
    architecture_family: str
    curriculum_family: str
    fault_family: str | None = None
    expected_decision: dict[str, Any]
    accepted_alternatives: tuple[dict[str, Any], ...]
    raw_text: str | None = None
    parsed_decision: dict[str, Any]
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    parse_error: str | None = None
    exact_match: bool
    field_matches: int = Field(ge=0)
    field_total: int = Field(ge=0)
    authority_violation: bool


class EvaluationObservabilityReport(BaseModel):
    """Signed index tying per-case traces to one signed M20 experiment report."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "self-model-evaluation-observability-v1"
    experiment_report_fingerprint: str = Field(min_length=64, max_length=64)
    evaluation_fingerprint: str = Field(min_length=64, max_length=64)
    trace_files: tuple[FileDigest, ...]
    trace_record_count: int = Field(gt=0)
    parse_failure_record_count: int = Field(ge=0)
    report_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_fingerprint(self) -> "EvaluationObservabilityReport":
        payload = self.model_dump(mode="json", exclude={"report_fingerprint"})
        expected = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        if self.report_fingerprint != expected:
            raise ValueError("evaluation observability fingerprint does not match content")
        return self

    def write(self, output_directory: str | Path) -> Path:
        path = Path(output_directory) / "evaluation-observability.json"
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, tuple):
        return [_canonical(item) for item in value]
    return value


def _matches_expected(example: SelfModelExample, decision: dict[str, Any]) -> bool:
    canonical = _canonical(decision)
    candidates = (example.expected_decision, *example.accepted_alternatives)
    return any(canonical == _canonical(candidate) for candidate in candidates)


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(value[key], child))
        return result
    if isinstance(value, list):
        result: dict[str, Any] = {}
        for index, item in enumerate(value):
            child = f"{prefix}[{index}]"
            result.update(_flatten(item, child))
        return result
    return {prefix: value}


def _field_score(expected: dict[str, Any], actual: dict[str, Any]) -> tuple[int, int]:
    expected_flat = _flatten(expected)
    actual_flat = _flatten(actual)
    if not expected_flat:
        return (1 if not actual_flat else 0, 1)
    matches = sum(actual_flat.get(key) == value for key, value in expected_flat.items())
    return matches, len(expected_flat)


def _contains_authority_violation(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in FORBIDDEN_AUTHORITY_KEYS:
                return True
            if _contains_authority_violation(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_authority_violation(item) for item in value)
    return False


class JsonlEvaluationTraceRecorder:
    """Append one record immediately after every model prediction."""

    def __init__(self, path: str | Path, evaluation_name: str) -> None:
        self.path = Path(path)
        self.evaluation_name = evaluation_name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.record_count = 0
        self.parse_failure_count = 0

    def append(
        self,
        *,
        predictor_name: str,
        profile: SelfModelContextProfile,
        example: SelfModelExample,
        prediction: SelfModelPrediction,
    ) -> EvaluationCaseRecord:
        matches, total = _field_score(example.expected_decision, prediction.decision)
        exact = prediction.parse_error is None and _matches_expected(
            example, prediction.decision
        )
        record = EvaluationCaseRecord(
            evaluation_name=self.evaluation_name,
            predictor_name=predictor_name,
            profile=SelfModelContextProfile(profile).value,
            scenario_id=example.scenario_id,
            scenario_fingerprint=example.scenario_fingerprint,
            architecture_family=example.definition.architecture_family,
            curriculum_family=example.definition.family.value,
            fault_family=example.definition.fault_family,
            expected_decision=dict(example.expected_decision),
            accepted_alternatives=tuple(dict(item) for item in example.accepted_alternatives),
            raw_text=prediction.raw_text,
            parsed_decision=dict(prediction.decision),
            confidence=prediction.confidence,
            parse_error=prediction.parse_error,
            exact_match=exact,
            field_matches=matches,
            field_total=total,
            authority_violation=_contains_authority_violation(prediction.decision),
        )
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(record.model_dump_json() + "\n")
        self.record_count += 1
        self.parse_failure_count += int(prediction.parse_error is not None)
        return record


class RecordingSelfModelPredictor:
    """Transparent predictor decorator that records only observable I/O."""

    def __init__(
        self,
        source: Any,
        recorder: JsonlEvaluationTraceRecorder,
        *,
        default_profile: SelfModelContextProfile = SelfModelContextProfile.STANDARD,
    ) -> None:
        self.source = source
        self.recorder = recorder
        self.default_profile = SelfModelContextProfile(default_profile)

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

    def predict(self, example: SelfModelExample) -> SelfModelPrediction:
        prediction = self.source.predict(example)
        self.recorder.append(
            predictor_name=self.name,
            profile=self.default_profile,
            example=example,
            prediction=prediction,
        )
        return prediction

    def predict_with_profile(
        self,
        example: SelfModelExample,
        profile: SelfModelContextProfile,
    ) -> SelfModelPrediction:
        profile = SelfModelContextProfile(profile)
        prediction = self.source.predict_with_profile(example, profile)
        self.recorder.append(
            predictor_name=self.name,
            profile=profile,
            example=example,
            prediction=prediction,
        )
        return prediction


def _role(predictor_name: str) -> str:
    lowered = predictor_name.lower()
    if "adapter" in lowered:
        return "adapter"
    if "base" in lowered:
        return "base"
    return "predictor"


def _staging_directory_for(output: Path) -> Path:
    return output.with_name(f"{output.name}.evaluation-staging")


@contextmanager
def _install_observability(staging: Path) -> Iterator[list[JsonlEvaluationTraceRecorder]]:
    original_evaluate = _empirical.evaluate_self_model
    original_context = _empirical.evaluate_context_profile
    recorders: list[JsonlEvaluationTraceRecorder] = []

    def recorder(name: str) -> JsonlEvaluationTraceRecorder:
        item = JsonlEvaluationTraceRecorder(staging / f"{name}.jsonl", name)
        recorders.append(item)
        return item

    def observed_evaluate(examples: Any, predictor: Any) -> Any:
        role = _role(predictor.name)
        wrapped = RecordingSelfModelPredictor(
            predictor,
            recorder(f"{role}-standard-primary"),
            default_profile=SelfModelContextProfile.STANDARD,
        )
        return original_evaluate(examples, wrapped)

    def observed_context(examples: Any, predictor: Any, profile: Any) -> Any:
        profile = SelfModelContextProfile(profile)
        role = _role(predictor.name)
        wrapped = RecordingSelfModelPredictor(
            predictor,
            recorder(f"{role}-{profile.value}-context"),
            default_profile=profile,
        )
        return original_context(examples, wrapped, profile)

    _empirical.evaluate_self_model = observed_evaluate
    _empirical.evaluate_context_profile = observed_context
    try:
        yield recorders
    finally:
        _empirical.evaluate_self_model = original_evaluate
        _empirical.evaluate_context_profile = original_context


def _count_trace_records(trace_root: Path) -> tuple[int, int]:
    records = 0
    parse_failures = 0
    for path in sorted(trace_root.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = EvaluationCaseRecord.model_validate_json(line)
            records += 1
            parse_failures += int(record.parse_error is not None)
    return records, parse_failures


def run_observed_empirical_adapter_experiment(
    prepared_directory: str | Path,
    *,
    backend: Any,
    output_directory: str | Path,
    load_in_4bit: bool = True,
    max_new_tokens: int = 512,
    general_regression: GeneralRegressionResult | None = None,
    reference: bool = False,
    resume_training_directory: str | Path | None = None,
) -> EmpiricalAdapterExperimentReport:
    """Run M20.1 and attach a separately signed, append-only prediction ledger."""

    output = Path(output_directory)
    staging = _staging_directory_for(output)
    if staging.exists() and any(staging.iterdir()):
        raise ValueError(
            f"evaluation staging directory is not empty: {staging}; preserve or remove "
            "it explicitly before starting another observed run"
        )
    if staging.exists():
        staging.rmdir()
    staging.mkdir(parents=True)

    completed = False
    try:
        with _install_observability(staging):
            report = run_isolated_empirical_adapter_experiment(
                prepared_directory,
                backend=backend,
                output_directory=output,
                load_in_4bit=load_in_4bit,
                max_new_tokens=max_new_tokens,
                general_regression=general_regression,
                reference=reference,
                resume_training_directory=resume_training_directory,
            )

        final_traces = output / "evaluation-traces"
        if final_traces.exists():
            raise ValueError("final evaluation trace directory already exists")
        shutil.move(str(staging), str(final_traces))
        trace_files = digest_tree(final_traces)
        record_count, parse_failure_count = _count_trace_records(final_traces)
        if record_count == 0:
            raise ValueError("observed empirical run produced no evaluation trace records")

        payload = {
            "schema_version": "self-model-evaluation-observability-v1",
            "experiment_report_fingerprint": report.report_fingerprint,
            "evaluation_fingerprint": report.base_standard.evaluation_fingerprint,
            "trace_files": [item.model_dump(mode="json") for item in trace_files],
            "trace_record_count": record_count,
            "parse_failure_record_count": parse_failure_count,
        }
        fingerprint = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        observability = EvaluationObservabilityReport.model_validate(
            {**payload, "report_fingerprint": fingerprint}
        )
        observability.write(output)
        completed = True
        return report
    finally:
        # Failed evaluations keep their append-only sibling staging tree for diagnosis.
        # Successful runs move that tree into the final evidence directory above.
        if completed and staging.exists():
            shutil.rmtree(staging)
