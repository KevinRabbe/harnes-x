"""Static qualification policy for bounded improvement candidates."""

from __future__ import annotations

from dataclasses import dataclass

from harness_x.core.ids import SystemVersion

from .models import (
    CandidateQualification,
    CandidateRiskLevel,
    ChangeOperation,
    ImprovementChangeType,
    ImprovementProposal,
)


POLICY_VERSION = "improvement-candidate-policy-v1"


@dataclass(frozen=True)
class _Rule:
    prefixes: tuple[str, ...]
    operations: frozenset[ChangeOperation]


_INITIAL_RULES: dict[ImprovementChangeType, _Rule] = {
    ImprovementChangeType.CONFIG_THRESHOLD: _Rule(
        prefixes=("gates.", "config.gates."),
        operations=frozenset({ChangeOperation.SET}),
    ),
    ImprovementChangeType.RETRIEVAL_SCORING_POLICY: _Rule(
        prefixes=("gates.retrieval.",),
        operations=frozenset({ChangeOperation.SET, ChangeOperation.REPLACE_POLICY}),
    ),
    ImprovementChangeType.ROUTINE_ORDERING: _Rule(
        prefixes=("routines.",),
        operations=frozenset({ChangeOperation.REORDER}),
    ),
    ImprovementChangeType.CONTEXT_BUILDER_POLICY: _Rule(
        prefixes=("reasoning.context.",),
        operations=frozenset({ChangeOperation.SET, ChangeOperation.REPLACE_POLICY}),
    ),
    ImprovementChangeType.VERIFICATION_FREQUENCY: _Rule(
        prefixes=("verification.", "routines.verification."),
        operations=frozenset({ChangeOperation.SET}),
    ),
    ImprovementChangeType.MEMORY_RETENTION_COMPACTION: _Rule(
        prefixes=("memory.",),
        operations=frozenset({ChangeOperation.SET, ChangeOperation.REPLACE_POLICY}),
    ),
}


class InitialImprovementPolicy:
    """Conservative Milestone 14 policy.

    Passing this policy means only that a candidate may be handed to Milestone 15's
    isolated sandbox. It is not evidence that the change improves the live system.
    """

    version = POLICY_VERSION

    def qualify(
        self,
        proposal: ImprovementProposal,
        *,
        current_system_version: SystemVersion,
    ) -> CandidateQualification:
        reasons: list[str] = []

        rule = _INITIAL_RULES.get(proposal.change_type)
        if rule is None:
            reasons.append("change_type_not_permitted_in_initial_policy")

        if proposal.baseline_version != current_system_version:
            reasons.append("baseline_version_is_stale")
        if proposal.rollback.restore_baseline_version != proposal.baseline_version:
            reasons.append("rollback_does_not_restore_declared_baseline")
        if not proposal.rollback.automatic:
            reasons.append("automatic_rollback_required")
        if proposal.risk_level in {CandidateRiskLevel.HIGH, CandidateRiskLevel.CRITICAL}:
            reasons.append("risk_level_exceeds_initial_policy")

        budget = proposal.resource_budget
        if budget.benchmark_runs > 20:
            reasons.append("benchmark_run_budget_exceeded")
        if budget.max_wall_time_seconds > 3600:
            reasons.append("wall_time_budget_exceeded")
        if budget.max_reasoning_steps > 10000:
            reasons.append("reasoning_step_budget_exceeded")
        if budget.max_tool_actions > 1000:
            reasons.append("tool_action_budget_exceeded")

        if not any(item.expected_delta != 0.0 for item in proposal.predicted_metrics):
            reasons.append("no_measurable_metric_change_predicted")

        if rule is not None:
            for scope in proposal.scope:
                if not any(scope.startswith(prefix) for prefix in rule.prefixes):
                    reasons.append("scope_outside_change_type_namespace")
                    break
            for patch in proposal.patches:
                if not any(patch.path.startswith(prefix) for prefix in rule.prefixes):
                    reasons.append("patch_outside_change_type_namespace")
                    break
                if patch.operation not in rule.operations:
                    reasons.append("operation_not_permitted_for_change_type")
                    break

        if proposal.change_type == ImprovementChangeType.CONFIG_THRESHOLD:
            for patch in proposal.patches:
                if (
                    isinstance(patch.before, bool)
                    or isinstance(patch.after, bool)
                    or not isinstance(patch.before, (int, float))
                    or not isinstance(patch.after, (int, float))
                ):
                    reasons.append("config_threshold_requires_numeric_values")
                    break

        if proposal.change_type == ImprovementChangeType.VERIFICATION_FREQUENCY:
            for patch in proposal.patches:
                if (
                    isinstance(patch.after, bool)
                    or not isinstance(patch.after, int)
                    or patch.after < 1
                ):
                    reasons.append("verification_frequency_must_be_positive_integer")
                    break

        unique_reasons = tuple(dict.fromkeys(reasons))
        return CandidateQualification(
            eligible=not unique_reasons,
            reasons=unique_reasons,
            policy_version=self.version,
        )
