from __future__ import annotations

from harness_x.training.predictors import top_level_json_object_end


def test_top_level_json_completion_ignores_nested_objects_and_braces_in_strings() -> None:
    text = (
        '  {"evidence":[{"path":"metrics.x","value":{"nested":1}}],'
        '"message":"literal } brace","ok":true} trailing garbage'
    )

    end = top_level_json_object_end(text)

    assert end is not None
    assert text[:end].strip() == (
        '{"evidence":[{"path":"metrics.x","value":{"nested":1}}],'
        '"message":"literal } brace","ok":true}'
    )
    assert text[end:] == " trailing garbage"


def test_top_level_json_completion_handles_escaped_quotes() -> None:
    text = '{"message":"quoted \\\"}\\\" text","value":1} after'

    end = top_level_json_object_end(text)

    assert end == text.index("} after") + 1


def test_incomplete_runaway_json_is_never_reported_complete() -> None:
    evidence_loop = (
        '{"evidence":[{"path":"metrics.recovery_successes","value":0},'
        '{"path":"metrics.recovery_successes","value":0},'
    )
    suffix_loop = (
        '{"allowed_targets":["maintenance_recovered",'
        '"maintenance_recovered_recovered",'
        '"maintenance_recovered_recovered_recovered"'
    )

    assert top_level_json_object_end(evidence_loop) is None
    assert top_level_json_object_end(suffix_loop) is None


def test_non_object_output_is_not_a_completed_json_object() -> None:
    assert top_level_json_object_end('[{"value":1}]') is None
    assert top_level_json_object_end('plain text') is None
