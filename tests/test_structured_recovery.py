from __future__ import annotations

import json

from harness_x.training.evaluation import SelfModelPrediction
from harness_x.training.evaluation_observability import JsonlEvaluationTraceRecorder
from harness_x.training.formatting import SelfModelContextProfile
from harness_x.training.models import (
    CurriculumFamily,
    DatasetSplit,
    LabelSource,
    ScenarioDefinition,
    build_example,
    canonical_json,
)
from harness_x.training.predictors import (
    REPAIR_ARRAY_ITEM_LIMIT,
    HuggingFaceSelfModelPredictor,
)
from harness_x.training.structured_recovery import (
    BoundedJsonRecoveryPredictor,
    StructuredRecoveryAttempt,
)


def _example():
    return build_example(
        definition=ScenarioDefinition(
            seed_id="structured_recovery_case",
            family=CurriculumFamily.STRUCTURAL,
            split=DatasetSplit.EVAL,
            task="Identify the authoritative owner.",
            architecture_family="architecture_fixture",
        ),
        system_version="fixture-v1",
        source_state_fingerprint="a" * 64,
        input_state={"surface": "task_lifecycle"},
        expected_decision={"owner": "orchestrator"},
        label_source=LabelSource.SYSTEM_RULE,
        generator_version="fixture-generator-v1",
    )


class _FakeRepairablePredictor:
    name = "adapter:fixture"
    context_profile = SelfModelContextProfile.STANDARD
    token_measurement_kind = "fixture"

    def __init__(self, *, primary: SelfModelPrediction, repair: SelfModelPrediction) -> None:
        self.primary = primary
        self.repair = repair
        self.primary_calls = 0
        self.repair_calls = 0
        self.repair_budget = None

    def prompt_measurement(self, example, profile):
        return (100, 25)

    def predict(self, example):
        self.primary_calls += 1
        return self.primary

    def predict_with_profile(self, example, profile):
        self.primary_calls += 1
        return self.primary

    def repair_prediction(self, example, profile, *, max_new_tokens):
        self.repair_calls += 1
        self.repair_budget = max_new_tokens
        return self.repair


def test_bounded_recovery_retries_once_only_after_parse_failure() -> None:
    primary = SelfModelPrediction(
        decision={},
        raw_text='{"owner":',
        parse_error="invalid_json: Expecting value",
    )
    repaired = SelfModelPrediction(
        decision={"owner": "orchestrator"},
        raw_text='{"owner":"orchestrator"}',
    )
    source = _FakeRepairablePredictor(primary=primary, repair=repaired)
    predictor = BoundedJsonRecoveryPredictor(
        source,
        max_attempts=1,
        repair_max_new_tokens=192,
    )

    result = predictor.predict(_example())

    assert result == repaired
    assert source.primary_calls == 1
    assert source.repair_calls == 1
    assert source.repair_budget == 192
    assert predictor.last_recovery is not None
    assert predictor.last_recovery.primary_raw_text == primary.raw_text
    assert predictor.last_recovery.primary_parse_error == primary.parse_error
    assert predictor.last_recovery.repair_raw_text == repaired.raw_text
    assert predictor.last_recovery.succeeded is True


def test_bounded_recovery_does_not_spend_retry_on_valid_primary() -> None:
    primary = SelfModelPrediction(
        decision={"owner": "orchestrator"},
        raw_text='{"owner":"orchestrator"}',
    )
    source = _FakeRepairablePredictor(primary=primary, repair=primary)
    predictor = BoundedJsonRecoveryPredictor(source, max_attempts=1)

    result = predictor.predict_with_profile(
        _example(), SelfModelContextProfile.STANDARD
    )

    assert result == primary
    assert source.primary_calls == 1
    assert source.repair_calls == 0
    assert predictor.last_recovery is None


def test_trace_preserves_primary_and_explicit_repair_boundaries(tmp_path) -> None:
    example = _example()
    recorder = JsonlEvaluationTraceRecorder(
        tmp_path / "trace.jsonl", "adapter-standard-primary"
    )
    final = SelfModelPrediction(
        decision={"owner": "orchestrator"},
        raw_text='{"owner":"orchestrator"}',
    )
    recovery = StructuredRecoveryAttempt(
        primary_raw_text='{"owner":',
        primary_parse_error="invalid_json: Expecting value",
        repair_raw_text=final.raw_text,
        repair_parse_error=None,
    )

    record = recorder.append(
        predictor_name="adapter:fixture",
        profile=SelfModelContextProfile.STANDARD,
        example=example,
        prediction=final,
        recovery=recovery,
    )

    assert record.primary_raw_text == '{"owner":'
    assert record.primary_parse_error is not None
    assert record.repair_attempted is True
    assert record.repair_succeeded is True
    assert record.repair_raw_text == final.raw_text
    assert record.repair_parse_error is None
    assert record.raw_text == final.raw_text
    assert record.parse_error is None
    assert record.exact_match is True
    assert recorder.primary_parse_failure_count == 1
    assert recorder.recovered_parse_failure_count == 1
    assert recorder.parse_failure_count == 0


class _RecordingTokenizer:
    pad_token_id = 0
    eos_token_id = 0

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        assert tokenize is False
        return json.dumps(
            {
                "messages": messages,
                "add_generation_prompt": add_generation_prompt,
            },
            sort_keys=True,
        )


def test_repair_prompt_uses_uniform_bounds_without_target_values() -> None:
    example = _example()
    predictor = object.__new__(HuggingFaceSelfModelPredictor)
    predictor.tokenizer = _RecordingTokenizer()

    prompt = predictor._render_repair_prompt(
        example, SelfModelContextProfile.STANDARD
    )

    assert "previous generation failed strict json validation" in prompt.lower()
    assert "expected_keys" in prompt
    assert "owner" in prompt
    assert f"at most {REPAIR_ARRAY_ITEM_LIMIT} items" in prompt.lower()
    assert "duplicate items are forbidden" in prompt.lower()
    assert "repeatedly appending the same suffix" in prompt.lower()
    assert canonical_json(example.expected_decision) not in prompt
    assert "orchestrator" not in prompt
