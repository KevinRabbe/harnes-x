"""Deterministic grounded self-model curriculum generation.

Labels come from current Harness X rules/configuration, known fault injections, and
known metric interventions. No teacher model participates in generation.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from typing import Any

from harness_x.config import HarnessConfig
from harness_x.memory import MemoryClass
from harness_x.orchestrator import LEGAL_TRANSITIONS, OperatingMode
from harness_x.telemetry.self_schema import SystemSelfSchema

from .fault_injection import FaultFamily, inject_fault
from .models import (
    CurriculumDataset,
    CurriculumFamily,
    CurriculumManifest,
    DatasetSplit,
    LabelSource,
    ScenarioDefinition,
    SelfModelExample,
    build_example,
    canonical_json,
)


GENERATOR_VERSION = "self-model-curriculum-v1"
HELD_OUT_FAULT_FAMILIES = frozenset(
    {FaultFamily.VERIFICATION_REJECTION, FaultFamily.BUDGET_EXHAUSTION}
)

# Explicit software-ownership rules are architectural ground truth, not model labels.
AUTHORITY_OWNERS: dict[str, str] = {
    "task_lifecycle": "orchestrator",
    "compute_budget_usage": "orchestrator",
    "goal_status": "memory.goal",
    "working_state": "memory.working",
    "episodic_history": "memory.episodic",
    "error_records": "memory.error",
    "semantic_claims": "memory.semantic",
    "procedural_records": "memory.procedural",
    "tool_execution": "tool_executor",
    "verification_result": "routine.verification",
}


class CurriculumGenerationError(ValueError):
    pass


def architecture_family(schema: SystemSelfSchema) -> str:
    """Fingerprint stable architectural metadata while excluding transient state."""
    payload = {
        "components": [item.model_dump(mode="json") for item in schema.components],
        "memories": [
            {
                "memory_class": item.memory_class,
                "capacity_units": item.capacity_units,
            }
            for item in schema.memories
        ],
        "gates": [
            {
                "gate_id": item.gate_id,
                "policy_version": item.policy_version,
                "configuration": item.configuration,
            }
            for item in schema.gates
        ],
        "tools": [
            {
                "name": item.name,
                "version": item.version,
                "permissions": list(item.permissions),
                "side_effect_level": item.side_effect_level,
            }
            for item in schema.tools
        ],
        "reasoning_core": (
            schema.reasoning_core.model_dump(mode="json")
            if schema.reasoning_core is not None
            else None
        ),
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"architecture_{digest[:16]}"


def _definition(
    *,
    seed_id: str,
    family: CurriculumFamily,
    split: DatasetSplit,
    task: str,
    architecture: str,
    fault_family: str | None = None,
    tags: tuple[str, ...] = (),
) -> ScenarioDefinition:
    return ScenarioDefinition(
        seed_id=seed_id,
        family=family,
        split=split,
        task=task,
        architecture_family=architecture,
        fault_family=fault_family,
        tags=tags,
    )


class CurriculumGenerator:
    """Generate train/eval records from one grounded SystemSelfSchema snapshot."""

    def __init__(self, config: HarnessConfig) -> None:
        self.config = config

    def generate(self, schema: SystemSelfSchema) -> CurriculumDataset:
        architecture = architecture_family(schema)
        examples = [
            *self._structural(schema, architecture),
            *self._operational(schema, architecture),
            *self._diagnostic(schema, architecture),
            *self._causal(schema, architecture),
        ]
        ordered = tuple(
            sorted(
                examples,
                key=lambda item: (
                    item.definition.split.value,
                    item.definition.family.value,
                    item.definition.seed_id,
                ),
            )
        )
        self._validate_examples(ordered)
        manifest = self._manifest(schema, ordered)
        return CurriculumDataset(examples=ordered, manifest=manifest)

    def _build(
        self,
        schema: SystemSelfSchema,
        definition: ScenarioDefinition,
        *,
        input_state: dict[str, Any],
        expected: dict[str, Any],
        label_source: LabelSource,
        accepted_alternatives: tuple[dict[str, Any], ...] = (),
        rationale: dict[str, Any] | None = None,
    ) -> SelfModelExample:
        return build_example(
            definition=definition,
            system_version=schema.system_version,
            source_state_fingerprint=schema.state_fingerprint,
            input_state=input_state,
            expected_decision=expected,
            accepted_alternatives=accepted_alternatives,
            rationale_metadata=rationale,
            label_source=label_source,
            generator_version=GENERATOR_VERSION,
        )

    def _structural(
        self,
        schema: SystemSelfSchema,
        architecture: str,
    ) -> list[SelfModelExample]:
        result: list[SelfModelExample] = []

        ownership_seeds = (
            ("owner_task_lifecycle", "task_lifecycle", DatasetSplit.TRAIN),
            ("owner_goal_status", "goal_status", DatasetSplit.TRAIN),
            ("owner_working_state", "working_state", DatasetSplit.TRAIN),
            ("owner_semantic_claims", "semantic_claims", DatasetSplit.TRAIN),
            ("owner_tool_execution_eval", "tool_execution", DatasetSplit.EVAL),
            ("owner_verification_eval", "verification_result", DatasetSplit.EVAL),
        )
        component_names = sorted(item.component for item in schema.components)
        for seed_id, surface, split in ownership_seeds:
            definition = _definition(
                seed_id=seed_id,
                family=CurriculumFamily.STRUCTURAL,
                split=split,
                task="Identify the software-owned component responsible for this state surface.",
                architecture=architecture,
                tags=("ownership", surface),
            )
            result.append(
                self._build(
                    schema,
                    definition,
                    input_state={
                        "state_surface": surface,
                        "declared_components": component_names,
                    },
                    expected={"owner": AUTHORITY_OWNERS[surface]},
                    label_source=LabelSource.SYSTEM_RULE,
                    rationale={
                        "rule": "software_owned_authority_boundary",
                        "source": "Harness X architecture contract",
                    },
                )
            )

        transition_seeds = (
            ("transition_ready_active", OperatingMode.READY, OperatingMode.TASK_ACTIVE, DatasetSplit.TRAIN),
            ("transition_active_verify", OperatingMode.TASK_ACTIVE, OperatingMode.VERIFY, DatasetSplit.TRAIN),
            ("transition_complete_active", OperatingMode.COMPLETE, OperatingMode.TASK_ACTIVE, DatasetSplit.TRAIN),
            ("transition_recovery_maintenance_eval", OperatingMode.RECOVERY, OperatingMode.MAINTENANCE, DatasetSplit.EVAL),
            ("transition_failed_ready_eval", OperatingMode.FAILED, OperatingMode.READY, DatasetSplit.EVAL),
        )
        for seed_id, source, target, split in transition_seeds:
            legal = target in LEGAL_TRANSITIONS[source]
            definition = _definition(
                seed_id=seed_id,
                family=CurriculumFamily.STRUCTURAL,
                split=split,
                task="Determine whether the requested lifecycle transition is legal.",
                architecture=architecture,
                tags=("state_machine",),
            )
            result.append(
                self._build(
                    schema,
                    definition,
                    input_state={"from": source.value, "to": target.value},
                    expected={
                        "legal": legal,
                        "owner": "orchestrator",
                        "allowed_targets": sorted(
                            item.value for item in LEGAL_TRANSITIONS[source]
                        ),
                    },
                    label_source=LabelSource.SYSTEM_RULE,
                    rationale={
                        "rule": "LEGAL_TRANSITIONS",
                        "source_mode": source.value,
                    },
                )
            )

        routing = self.config.gates.write.memory_class_by_kind
        routing_seeds = (
            ("route_goal", "goal", DatasetSplit.TRAIN),
            ("route_observation", "observation", DatasetSplit.TRAIN),
            ("route_failure", "failure", DatasetSplit.TRAIN),
            ("route_outcome_eval", "outcome", DatasetSplit.EVAL),
            ("route_anomaly_eval", "anomaly", DatasetSplit.EVAL),
        )
        for seed_id, kind, split in routing_seeds:
            target = routing.get(kind, self.config.gates.write.default_memory_class)
            if target not in {item.value for item in MemoryClass}:
                raise CurriculumGenerationError(
                    f"write-gate config maps {kind!r} to unknown memory class {target!r}"
                )
            definition = _definition(
                seed_id=seed_id,
                family=CurriculumFamily.STRUCTURAL,
                split=split,
                task="Choose the authoritative memory class for the accepted information kind.",
                architecture=architecture,
                tags=("memory_routing", kind),
            )
            result.append(
                self._build(
                    schema,
                    definition,
                    input_state={
                        "accepted": True,
                        "information_kind": kind,
                        "available_memory_classes": [item.value for item in MemoryClass],
                    },
                    expected={"memory_class": target},
                    label_source=LabelSource.SYSTEM_RULE,
                    rationale={
                        "policy_version": self.config.gates.write.policy_version,
                        "config_key": f"memory_class_by_kind.{kind}",
                    },
                )
            )

        authority_examples = (
            ("authority_self_schema", "grounded_self_schema", "authoritative", DatasetSplit.TRAIN),
            ("authority_suspected_cause", "suspected_cause", "inferred", DatasetSplit.TRAIN),
            ("authority_reasoning_proposal_eval", "reasoning_proposal", "proposed", DatasetSplit.EVAL),
        )
        for seed_id, state_kind, classification, split in authority_examples:
            definition = _definition(
                seed_id=seed_id,
                family=CurriculumFamily.STRUCTURAL,
                split=split,
                task="Classify whether the state is authoritative, inferred, or proposed.",
                architecture=architecture,
                tags=("epistemic_boundary",),
            )
            result.append(
                self._build(
                    schema,
                    definition,
                    input_state={"state_kind": state_kind},
                    expected={"classification": classification},
                    label_source=LabelSource.SYSTEM_RULE,
                    rationale={"rule": "observed_inferred_proposed_remain_distinct"},
                )
            )
        return result

    def _maintenance_expected(
        self,
        *,
        working_pressure: float,
        unresolved_errors: int,
        repeated_failures: int,
    ) -> dict[str, Any]:
        cfg = self.config.gates.maintenance
        reasons: list[str] = []
        if working_pressure >= cfg.working_pressure_trigger:
            reasons.append("working_pressure")
        if unresolved_errors >= cfg.unresolved_error_trigger:
            reasons.append("unresolved_errors")
        if repeated_failures >= cfg.repeated_failure_trigger:
            reasons.append("repeated_failures")
        return {
            "trigger": bool(reasons),
            "recommended_mode": "maintenance" if reasons else None,
            "reasons": reasons,
        }

    def _operational(
        self,
        schema: SystemSelfSchema,
        architecture: str,
    ) -> list[SelfModelExample]:
        cfg = self.config.gates.maintenance
        cases = (
            (
                "maintenance_pressure_below",
                DatasetSplit.TRAIN,
                max(0.0, cfg.working_pressure_trigger - 0.01),
                0,
                0,
            ),
            (
                "maintenance_pressure_at_threshold",
                DatasetSplit.TRAIN,
                cfg.working_pressure_trigger,
                0,
                0,
            ),
            (
                "maintenance_unresolved_errors",
                DatasetSplit.TRAIN,
                0.1,
                cfg.unresolved_error_trigger,
                0,
            ),
            (
                "maintenance_repeated_failures_eval",
                DatasetSplit.EVAL,
                0.1,
                0,
                cfg.repeated_failure_trigger,
            ),
        )
        result: list[SelfModelExample] = []
        for seed_id, split, pressure, unresolved, repeated in cases:
            expected = self._maintenance_expected(
                working_pressure=pressure,
                unresolved_errors=unresolved,
                repeated_failures=repeated,
            )
            definition = _definition(
                seed_id=seed_id,
                family=CurriculumFamily.OPERATIONAL,
                split=split,
                task="Decide whether deterministic maintenance policy should trigger.",
                architecture=architecture,
                tags=("maintenance_gate",),
            )
            result.append(
                self._build(
                    schema,
                    definition,
                    input_state={
                        "working_pressure": pressure,
                        "unresolved_error_count": unresolved,
                        "repeated_failure_count": repeated,
                        "policy": {
                            "working_pressure_trigger": cfg.working_pressure_trigger,
                            "unresolved_error_trigger": cfg.unresolved_error_trigger,
                            "repeated_failure_trigger": cfg.repeated_failure_trigger,
                        },
                    },
                    expected=expected,
                    label_source=LabelSource.SYSTEM_RULE,
                    rationale={
                        "policy_version": cfg.policy_version,
                        "label_computed_from": "maintenance_gate_thresholds",
                    },
                )
            )
        return result

    def _diagnostic(
        self,
        schema: SystemSelfSchema,
        architecture: str,
    ) -> list[SelfModelExample]:
        result: list[SelfModelExample] = []
        for fault in FaultFamily:
            split = (
                DatasetSplit.EVAL
                if fault in HELD_OUT_FAULT_FAMILIES
                else DatasetSplit.TRAIN
            )
            injected = inject_fault(schema, fault)
            definition = _definition(
                seed_id=f"diagnose_{fault.value}",
                family=CurriculumFamily.DIAGNOSTIC,
                split=split,
                task=(
                    "Diagnose the observable fault. Identify the symptom, likely component, "
                    "evidence, uncertainty, and one safe next experiment."
                ),
                architecture=architecture,
                fault_family=fault.value,
                tags=("fault_injection", fault.value),
            )
            expected = {
                **injected.expected_diagnosis,
                "evidence": list(injected.evidence),
                "uncertainty": injected.uncertainty,
                "safe_next_experiment": injected.safe_next_experiment,
            }
            result.append(
                self._build(
                    schema,
                    definition,
                    input_state={
                        "telemetry": injected.visible_state,
                        "instruction": "Use observed evidence; do not invent hidden state.",
                    },
                    expected=expected,
                    label_source=LabelSource.INJECTED_FAULT,
                    rationale={
                        "fault_family": fault.value,
                        "label_computed_from": "known_fault_injection",
                        "teacher_model_used": False,
                    },
                )
            )
        return result

    def _causal(
        self,
        schema: SystemSelfSchema,
        architecture: str,
    ) -> list[SelfModelExample]:
        working = next(
            item for item in schema.memories if item.memory_class == "working"
        )
        capacity = int(working.capacity_units or 16)
        used = max(1, min(capacity, int(working.used_units or max(1, capacity // 2))))

        cases: tuple[
            tuple[str, DatasetSplit, dict[str, Any], dict[str, Any], dict[str, Any]],
            ...,
        ] = (
            (
                "causal_double_working_capacity",
                DatasetSplit.TRAIN,
                {"working_capacity": capacity, "working_used": used, "working_pressure": used / capacity},
                {"change": "double_working_capacity", "from": capacity, "to": capacity * 2},
                {"working_capacity": capacity * 2, "working_used": used, "working_pressure": used / (capacity * 2)},
            ),
            (
                "causal_resolve_errors",
                DatasetSplit.TRAIN,
                {"unresolved_errors": 4, "maintenance_triggered": True},
                {"change": "resolve_errors_with_evidence", "resolved_count": 3},
                {"unresolved_errors": 1, "maintenance_triggered": False},
            ),
            (
                "causal_raise_pressure_threshold_eval",
                DatasetSplit.EVAL,
                {"working_pressure": 0.90, "maintenance_trigger": 0.85, "maintenance_triggered": True},
                {"change": "raise_working_pressure_trigger", "from": 0.85, "to": 0.95},
                {"working_pressure": 0.90, "maintenance_trigger": 0.95, "maintenance_triggered": False},
            ),
        )
        result: list[SelfModelExample] = []
        for seed_id, split, before, intervention, after in cases:
            if seed_id == "causal_double_working_capacity":
                expected = {
                    "likely_cause": "working_capacity_change",
                    "effect": "pressure_decreased_without_dropping_used_state",
                    "safe_follow_up_test": "compare_evictions_and_task_success_at_both_capacities",
                }
            elif seed_id == "causal_resolve_errors":
                expected = {
                    "likely_cause": "evidence_backed_error_resolution",
                    "effect": "unresolved_error_pressure_decreased",
                    "safe_follow_up_test": "verify_resolved_errors_do_not_recur_on_replay",
                }
            else:
                expected = {
                    "likely_cause": "maintenance_threshold_change",
                    "effect": "same_pressure_no_longer_triggers_maintenance",
                    "safe_follow_up_test": "run_pressure_benchmark_and_check_for_deferred_overflow",
                }
            definition = _definition(
                seed_id=seed_id,
                family=CurriculumFamily.CAUSAL_COUNTERFACTUAL,
                split=split,
                task=(
                    "Explain the observed before/after metric change using the declared "
                    "intervention and propose one safe follow-up test."
                ),
                architecture=architecture,
                tags=("counterfactual",),
            )
            result.append(
                self._build(
                    schema,
                    definition,
                    input_state={
                        "before": before,
                        "intervention": intervention,
                        "after": after,
                    },
                    expected=expected,
                    label_source=LabelSource.KNOWN_INTERVENTION,
                    rationale={
                        "label_computed_from": "declared_intervention_and_deterministic_metric_relation",
                        "teacher_model_used": False,
                    },
                )
            )
        return result

    @staticmethod
    def _validate_examples(examples: tuple[SelfModelExample, ...]) -> None:
        seed_ids = [item.definition.seed_id for item in examples]
        if len(seed_ids) != len(set(seed_ids)):
            duplicates = sorted(
                seed for seed, count in Counter(seed_ids).items() if count > 1
            )
            raise CurriculumGenerationError(f"duplicate curriculum seeds: {duplicates!r}")
        train_faults = {
            item.definition.fault_family
            for item in examples
            if item.definition.split == DatasetSplit.TRAIN
            and item.definition.fault_family is not None
        }
        leaked = train_faults & {item.value for item in HELD_OUT_FAULT_FAMILIES}
        if leaked:
            raise CurriculumGenerationError(
                f"held-out fault families leaked into training: {sorted(leaked)!r}"
            )

    @staticmethod
    def _manifest(
        schema: SystemSelfSchema,
        examples: tuple[SelfModelExample, ...],
    ) -> CurriculumManifest:
        train = tuple(
            item for item in examples if item.definition.split == DatasetSplit.TRAIN
        )
        evaluation = tuple(
            item for item in examples if item.definition.split == DatasetSplit.EVAL
        )
        counts: dict[str, dict[str, int]] = defaultdict(lambda: {"train": 0, "eval": 0})
        for item in examples:
            counts[item.definition.family.value][item.definition.split.value] += 1
        dataset_fingerprint = hashlib.sha256(
            canonical_json([item.scenario_fingerprint for item in examples]).encode("utf-8")
        ).hexdigest()
        return CurriculumManifest(
            generator_version=GENERATOR_VERSION,
            system_version=schema.system_version,
            source_state_fingerprint=schema.state_fingerprint,
            dataset_fingerprint=dataset_fingerprint,
            train_count=len(train),
            eval_count=len(evaluation),
            train_seed_ids=tuple(item.definition.seed_id for item in train),
            eval_seed_ids=tuple(item.definition.seed_id for item in evaluation),
            held_out_fault_families=tuple(
                sorted(item.value for item in HELD_OUT_FAULT_FAMILIES)
            ),
            family_counts={key: counts[key] for key in sorted(counts)},
        )
