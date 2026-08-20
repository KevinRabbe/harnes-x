"""Target-independent JSON schemas for bounded self-model repair decoding.

The repair schema is part of the output protocol, not a label oracle.  It may use
stable Harness X vocabularies, scenario-family/task metadata, visible input shape,
and the top-level key names that the normal prompt already discloses.  It must not
inspect held-out target values or accepted-alternative values.
"""

from __future__ import annotations

from typing import Any

from harness_x.memory import MemoryClass
from harness_x.orchestrator import OperatingMode

from .models import CurriculumFamily, SelfModelExample


DIAGNOSTIC_EVIDENCE_ITEM_LIMIT = 4
GENERIC_ARRAY_ITEM_LIMIT = 8
TEXT_LIMIT = 160


def _string(*, max_length: int = TEXT_LIMIT, enum: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "string", "maxLength": max_length}
    if enum is not None:
        result["enum"] = enum
    return result


def _base_object(example: SelfModelExample, properties: dict[str, Any]) -> dict[str, Any]:
    # Top-level expected key names are already included in every normal evaluation
    # prompt by formatting._user_payload.  Values are deliberately never read here.
    required = sorted(example.expected_decision.keys())
    missing = [key for key in required if key not in properties]
    for key in missing:
        properties[key] = {}
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _diagnostic_schema(example: SelfModelExample) -> dict[str, Any]:
    evidence_item = {
        "type": "object",
        "properties": {
            "path": _string(max_length=160),
            "value": {},
            "minimum": {"type": "number"},
            "relationship": _string(max_length=80),
            "repeated_tool_failures": {"type": "integer"},
            "equals_path": _string(max_length=160),
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    safe_experiment = {
        "type": "object",
        "properties": {
            "action": _string(max_length=96),
            "measure": _string(max_length=120),
            "preserve_both_claims": {"type": "boolean"},
            "do_not_repeat_identical_side_effect": {"type": "boolean"},
            "reuse_rejected_result_as_truth": {"type": "boolean"},
            "request_more_compute_only_external_to_core": {"type": "boolean"},
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    return _base_object(
        example,
        {
            "observed_symptom": _string(max_length=140),
            "likely_component": _string(max_length=80),
            "recommended_control": _string(max_length=80),
            "evidence": {
                "type": "array",
                "items": evidence_item,
                "maxItems": DIAGNOSTIC_EVIDENCE_ITEM_LIMIT,
            },
            "uncertainty": _string(enum=["low", "medium", "high"]),
            "safe_next_experiment": safe_experiment,
        },
    )


def _structural_schema(example: SelfModelExample) -> dict[str, Any]:
    tags = set(example.definition.tags)
    if "state_machine" in tags:
        return _base_object(
            example,
            {
                "legal": {"type": "boolean"},
                "owner": _string(max_length=64),
                "allowed_targets": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [item.value for item in OperatingMode],
                    },
                    "maxItems": len(OperatingMode),
                    "uniqueItems": True,
                },
            },
        )
    if "memory_routing" in tags:
        return _base_object(
            example,
            {
                "memory_class": _string(
                    max_length=64,
                    enum=[item.value for item in MemoryClass],
                )
            },
        )
    if "epistemic_boundary" in tags:
        return _base_object(
            example,
            {
                "classification": _string(
                    max_length=32,
                    enum=["authoritative", "inferred", "proposed"],
                )
            },
        )
    return _base_object(example, {"owner": _string(max_length=96)})


def _operational_schema(example: SelfModelExample) -> dict[str, Any]:
    return _base_object(
        example,
        {
            "trigger": {"type": "boolean"},
            "recommended_mode": {
                "type": ["string", "null"],
                "maxLength": 48,
            },
            "reasons": {
                "type": "array",
                "items": _string(max_length=64),
                "maxItems": 3,
                "uniqueItems": True,
            },
        },
    )


def repair_json_schema(example: SelfModelExample) -> dict[str, Any]:
    """Return a finite JSON output contract without reading held-out target values."""

    family = example.definition.family
    if family == CurriculumFamily.DIAGNOSTIC:
        return _diagnostic_schema(example)
    if family == CurriculumFamily.STRUCTURAL:
        return _structural_schema(example)
    if family == CurriculumFamily.OPERATIONAL:
        return _operational_schema(example)

    # Causal/counterfactual result shapes vary by intervention.  Preserve only the
    # already-disclosed top-level keys here; the parser-level array bound still keeps
    # nested free-form JSON finite without supplying target values.
    return _base_object(
        example,
        {key: {} for key in sorted(example.expected_decision.keys())},
    )
