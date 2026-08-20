from __future__ import annotations

from harness_x.orchestrator import OperatingMode
from harness_x.training.models import (
    CurriculumFamily,
    DatasetSplit,
    LabelSource,
    ScenarioDefinition,
    build_example,
)
from harness_x.training.repair_schema import (
    DIAGNOSTIC_EVIDENCE_ITEM_LIMIT,
    repair_json_schema,
)


def _example(*, family, seed_id, task, tags=(), input_state=None, expected=None):
    return build_example(
        definition=ScenarioDefinition(
            seed_id=seed_id,
            family=family,
            split=DatasetSplit.EVAL,
            task=task,
            architecture_family="architecture_fixture",
            tags=tags,
        ),
        system_version="fixture-v1",
        source_state_fingerprint="a" * 64,
        input_state=input_state or {},
        expected_decision=expected or {},
        label_source=LabelSource.SYSTEM_RULE,
        generator_version="fixture-generator-v1",
    )


def test_state_machine_schema_uses_stable_mode_domain_not_target_values() -> None:
    common = dict(
        family=CurriculumFamily.STRUCTURAL,
        seed_id="transition_fixture",
        task="Determine whether the requested lifecycle transition is legal.",
        tags=("state_machine",),
        input_state={"from": "recovery", "to": "maintenance"},
    )
    first = _example(
        **common,
        expected={
            "legal": True,
            "owner": "orchestrator",
            "allowed_targets": ["maintenance"],
        },
    )
    second = _example(
        **common,
        expected={
            "legal": False,
            "owner": "different-held-out-value",
            "allowed_targets": ["invented_target"],
        },
    )

    first_schema = repair_json_schema(first)
    second_schema = repair_json_schema(second)

    assert first_schema == second_schema
    allowed = first_schema["properties"]["allowed_targets"]["items"]["enum"]
    assert set(allowed) == {item.value for item in OperatingMode}
    assert "maintenance_recovered_recovered" not in allowed
    assert "invented_target" not in allowed


def test_diagnostic_schema_bounds_evidence_without_fault_target_values() -> None:
    example = _example(
        family=CurriculumFamily.DIAGNOSTIC,
        seed_id="diagnostic_fixture",
        task="Diagnose the observable fault.",
        input_state={"telemetry": {"budget_usage": {"reasoning_steps": 8}}},
        expected={
            "observed_symptom": "held-out symptom",
            "likely_component": "held-out component",
            "recommended_control": "held-out control",
            "evidence": [{"path": "held-out.path", "equals_path": "held-out.other"}],
            "uncertainty": "low",
            "safe_next_experiment": {"action": "held-out action"},
        },
    )

    schema = repair_json_schema(example)
    properties = schema["properties"]
    evidence = properties["evidence"]

    assert evidence["maxItems"] == DIAGNOSTIC_EVIDENCE_ITEM_LIMIT
    assert evidence["items"]["required"] == ["path"]
    assert evidence["items"]["additionalProperties"] is False
    assert set(evidence["items"]["properties"]) == {
        "path",
        "value",
        "minimum",
        "relationship",
        "repeated_tool_failures",
        "equals_path",
    }
    rendered = repr(schema)
    assert "held-out symptom" not in rendered
    assert "held-out component" not in rendered
    assert "held-out.path" not in rendered
    assert "held-out action" not in rendered
