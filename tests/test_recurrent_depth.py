import json

import pytest
from pydantic import ValidationError

from harness_x.reasoning import (
    DEFAULT_DEPTHS,
    DepthSelectorArtifact,
    FixedDepthRecurrentCore,
    HuginnTransformersBackend,
    HuginnTransformersSettings,
    LearnedDepthSelector,
    ReasoningCore,
    RecurrentDepthAuthority,
    RecurrentDepthResearchError,
    build_reference_recurrent_depth_fixture,
    run_reference_recurrent_depth_research,
)


def test_fixed_depth_core_uses_only_software_authorized_depth() -> None:
    train, _, backend = build_reference_recurrent_depth_fixture()
    authority = RecurrentDepthAuthority(
        allowed_depths=(4, 8, 16, 32, 64),
        max_recurrent_steps=64,
    )
    authorization = authority.authorize(16)
    core = FixedDepthRecurrentCore(backend, authorization)

    assert isinstance(core, ReasoningCore)
    assert core.depth == 16
    assert core.info.version == "fixed-depth-v1-d16"
    assert core.info.model == "deterministic-reference-recurrent-core"

    # A training case whose known minimum is 16 resolves at the authorized depth.
    case = next(item for item in train if item.case_id == "train_reason_a")
    assert core.generate(case.context()) == case.expected_output


def test_recurrent_depth_authority_fails_closed_on_unapproved_or_excess_depth() -> None:
    authority = RecurrentDepthAuthority(
        allowed_depths=(4, 8, 16, 32, 64, 128),
        max_recurrent_steps=64,
    )

    with pytest.raises(RecurrentDepthResearchError, match="not in the authorized"):
        authority.authorize(12)
    with pytest.raises(RecurrentDepthResearchError, match="exceeds external maximum"):
        authority.authorize(128)


def test_authorization_fingerprint_detects_tampering() -> None:
    authorization = RecurrentDepthAuthority(
        allowed_depths=(4, 8, 16), max_recurrent_steps=16
    ).authorize(8)
    payload = authorization.model_dump(mode="json")
    payload["depth"] = 16

    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        type(authorization).model_validate(payload)


def test_reference_fixed_depth_curve_improves_then_saturates() -> None:
    report = run_reference_recurrent_depth_research()
    curve = report.fixed_depth_curve
    points = {point.depth: point for point in curve.points}

    assert report.evidence_kind == "reference_simulator"
    assert report.fixed_depth_improved is True
    assert curve.depths == DEFAULT_DEPTHS
    assert points[4].mean_quality < points[16].mean_quality < points[64].mean_quality
    assert points[64].mean_quality == pytest.approx(1.0)
    assert points[128].mean_quality == pytest.approx(points[64].mean_quality)
    assert points[128].dominated is True
    assert 128 not in curve.frontier_depths
    assert curve.quality_gain_over_min_depth >= 0.70


def test_learned_external_depth_selector_beats_deterministic_reference_policy() -> None:
    report = run_reference_recurrent_depth_research()
    comparison = report.selector_comparison

    assert report.selector_improved is True
    assert report.passed is True
    assert comparison.learned_frontier_improved is True
    assert comparison.learned.mean_quality >= comparison.deterministic.mean_quality
    assert comparison.learned.mean_normalized_cost < comparison.deterministic.mean_normalized_cost
    assert comparison.learned.mean_net_value > comparison.deterministic.mean_net_value
    assert comparison.learned.exact_depth_accuracy == pytest.approx(1.0)
    assert comparison.deterministic.exact_depth_accuracy < 1.0

    learned_by_case = {item.case_id: item for item in comparison.learned_cases}
    assert learned_by_case["eval_mid"].selected_depth == 8
    assert learned_by_case["eval_reason"].selected_depth == 16


def test_learned_depth_selector_artifact_round_trip_and_tamper_detection(tmp_path) -> None:
    report = run_reference_recurrent_depth_research()
    selector = LearnedDepthSelector(report.learned_selector)
    path = tmp_path / "selector.json"
    selector.write(path)

    loaded = LearnedDepthSelector.load(path)
    assert loaded.artifact == selector.artifact

    payload = json.loads(path.read_text(encoding="utf-8"))
    first_key = sorted(payload["centroids"], key=int)[0]
    payload["centroids"][first_key][0] = 0.999
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RecurrentDepthResearchError, match="artifact fingerprint mismatch"):
        LearnedDepthSelector.load(path)


def test_reference_train_eval_cases_are_disjoint_and_targets_are_not_in_context() -> None:
    train, eval_cases, _ = build_reference_recurrent_depth_fixture()
    assert {case.case_id for case in train}.isdisjoint(
        {case.case_id for case in eval_cases}
    )

    context = train[0].context()
    assert "expected_output" not in context.payload
    assert "required_depth" not in context.payload
    assert set(context.payload) == {"schema_version", "case_id", "instruction"}


def test_huginn_adapter_requires_explicit_remote_code_trust_before_optional_imports() -> None:
    train, _, _ = build_reference_recurrent_depth_fixture()
    backend = HuginnTransformersBackend(HuginnTransformersSettings())

    with pytest.raises(RecurrentDepthResearchError, match="allow_remote_code=True"):
        backend.generate_at_depth(train[0].context(), 4)


def test_recurrent_report_and_artifact_are_strict_models() -> None:
    report = run_reference_recurrent_depth_research()
    payload = report.model_dump(mode="json")
    payload["unexpected_live_promotion"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        type(report).model_validate(payload)

    artifact = report.learned_selector.model_dump(mode="json")
    artifact["allowed_depths"] = [4, 8]
    with pytest.raises(ValidationError):
        DepthSelectorArtifact.model_validate(artifact)
