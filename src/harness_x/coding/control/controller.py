"""Software-owned phases, commitments, progress, and horizon control for coding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

from harness_x.core import CodingPlanId, CommitmentId, EventType, TaskId

from .contracts import (
    ActionClass,
    CodingCommitment,
    CodingControlSnapshot,
    CodingPhase,
    CodingPlan,
    CommitmentStatus,
    ControlIntervention,
    HorizonMode,
    HorizonSnapshot,
    InterventionKind,
    ProgressSnapshot,
)

if TYPE_CHECKING:
    from harness_x.telemetry import TraceRecorder


_INSPECTION_TOOLS = frozenset({"workspace_list", "workspace_read", "workspace_search"})
_MUTATION_TOOLS = frozenset({"workspace_write", "workspace_patch"})
_EXECUTION_TOOLS = frozenset({"process_run"})

_ALLOWED_PHASE_TRANSITIONS: dict[CodingPhase, frozenset[CodingPhase]] = {
    CodingPhase.ORIENT: frozenset(
        {
            CodingPhase.DIAGNOSE,
            CodingPhase.PLAN,
            CodingPhase.IMPLEMENT,
            CodingPhase.VERIFY,
            CodingPhase.BLOCKED,
        }
    ),
    CodingPhase.DIAGNOSE: frozenset(
        {
            CodingPhase.PLAN,
            CodingPhase.IMPLEMENT,
            CodingPhase.VERIFY,
            CodingPhase.BLOCKED,
        }
    ),
    CodingPhase.PLAN: frozenset(
        {
            CodingPhase.DIAGNOSE,
            CodingPhase.IMPLEMENT,
            CodingPhase.VERIFY,
            CodingPhase.BLOCKED,
        }
    ),
    CodingPhase.IMPLEMENT: frozenset(
        {
            CodingPhase.DIAGNOSE,
            CodingPhase.PLAN,
            CodingPhase.VERIFY,
            CodingPhase.BLOCKED,
        }
    ),
    CodingPhase.VERIFY: frozenset(
        {
            CodingPhase.IMPLEMENT,
            CodingPhase.REVIEW,
            CodingPhase.BLOCKED,
        }
    ),
    CodingPhase.REVIEW: frozenset(
        {CodingPhase.IMPLEMENT, CodingPhase.COMPLETE, CodingPhase.BLOCKED}
    ),
    CodingPhase.COMPLETE: frozenset(),
    CodingPhase.BLOCKED: frozenset(
        {CodingPhase.DIAGNOSE, CodingPhase.PLAN, CodingPhase.IMPLEMENT}
    ),
}

_ALLOWED_COMMITMENT_TRANSITIONS: dict[
    CommitmentStatus, frozenset[CommitmentStatus]
] = {
    CommitmentStatus.PROPOSED: frozenset(
        {CommitmentStatus.ACTIVE, CommitmentStatus.INVALIDATED}
    ),
    CommitmentStatus.ACTIVE: frozenset(
        {
            CommitmentStatus.SATISFIED,
            CommitmentStatus.BLOCKED,
            CommitmentStatus.INVALIDATED,
        }
    ),
    CommitmentStatus.BLOCKED: frozenset(
        {CommitmentStatus.ACTIVE, CommitmentStatus.INVALIDATED}
    ),
    CommitmentStatus.SATISFIED: frozenset(),
    CommitmentStatus.INVALIDATED: frozenset(),
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _unique_append(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    normalized = value.strip()
    if not normalized or normalized in values:
        return values
    return (*values, normalized)


class CodingControlController:
    """Authoritative deterministic control plane around a coding reasoner.

    The model may propose tools and report whether it believes work remains. It cannot
    mutate this controller. Phase transitions, durable commitments, progress counters,
    remaining-horizon posture, and interventions are derived and owned by software.
    """

    def __init__(
        self,
        *,
        task_id: TaskId,
        task: str,
        constraints: tuple[str, ...],
        acceptance_requirements: tuple[str, ...],
        reasoning_limit: int,
        tool_action_limit: int,
        plan_path: str | Path | None = None,
        recorder: TraceRecorder | None = None,
        max_inspection_streak: int = 6,
        max_no_progress_streak: int = 4,
        max_same_failure_count: int = 3,
    ) -> None:
        if reasoning_limit < 0 or tool_action_limit < 0:
            raise ValueError("coding control limits cannot be negative")
        if max_inspection_streak < 1:
            raise ValueError("max_inspection_streak must be positive")
        if max_no_progress_streak < 1:
            raise ValueError("max_no_progress_streak must be positive")
        if max_same_failure_count < 1:
            raise ValueError("max_same_failure_count must be positive")

        root = CodingCommitment(
            commitment_id=CommitmentId.new(),
            objective=task,
            status=CommitmentStatus.ACTIVE,
            acceptance_requirements=acceptance_requirements,
            created_step=0,
            updated_step=0,
        )
        self._plan = CodingPlan(
            plan_id=CodingPlanId.new(),
            task_id=task_id,
            task=task,
            constraints=constraints,
            commitments=(root,),
        )
        self._history: list[CodingPlan] = [self._plan]
        self._root_commitment_id = root.commitment_id
        self._recorder = recorder
        self._plan_path = Path(plan_path) if plan_path is not None else None
        self.reasoning_limit = reasoning_limit
        self.tool_action_limit = tool_action_limit
        self.max_inspection_streak = max_inspection_streak
        self.max_no_progress_streak = max_no_progress_streak
        self.max_same_failure_count = max_same_failure_count

        self._total_actions = 0
        self._inspection_actions = 0
        self._mutation_actions = 0
        self._execution_actions = 0
        self._failed_actions = 0
        self._duplicate_actions = 0
        self._repeat_streak = 0
        self._inspection_streak = 0
        self._no_progress_streak = 0
        self._new_evidence_count = 0
        self._verification_attempts = 0
        self._verification_passes = 0
        self._same_failure_count = 0
        self._last_action_fingerprint: str | None = None
        self._last_failure_signature: str | None = None
        self._seen_action_fingerprints: set[str] = set()
        self._seen_evidence_fingerprints: set[str] = set()
        self._last_intervention = ControlIntervention()

        self._persist_plan()
        self._emit_plan("coding_control_initialized")
        self._emit_commitment(root, reason="root_commitment_created")

    @property
    def plan(self) -> CodingPlan:
        return self._plan

    @property
    def plan_history(self) -> tuple[CodingPlan, ...]:
        return tuple(self._history)

    @property
    def root_commitment_id(self) -> CommitmentId:
        return self._root_commitment_id

    @property
    def plan_path(self) -> Path | None:
        return self._plan_path

    def transition_phase(
        self,
        phase: CodingPhase,
        *,
        reason: str,
        step: int,
    ) -> CodingPlan:
        current = self._plan.phase
        if phase == current:
            return self._plan
        if phase not in _ALLOWED_PHASE_TRANSITIONS[current]:
            raise ValueError(
                f"illegal coding phase transition: {current.value} -> {phase.value}"
            )
        previous = current
        self._revise_plan(phase=phase, step=step, reason=reason)
        # A phase transition is meaningful progress and breaks an inspection/no-op streak.
        self._no_progress_streak = 0
        if phase not in {CodingPhase.DIAGNOSE, CodingPhase.IMPLEMENT}:
            self._inspection_streak = 0
        if self._recorder is not None:
            self._recorder.emit(
                EventType.CODING_PHASE_CHANGED,
                "coding.control",
                input_refs=(str(self._plan.plan_id),),
                output_refs=(str(self._plan.plan_id),),
                metadata={
                    "from": previous.value,
                    "to": phase.value,
                    "reason": reason,
                    "plan_revision": self._plan.revision,
                },
            )
        return self._plan

    def set_strategy(
        self,
        strategy: tuple[str, ...],
        *,
        reason: str,
        step: int,
    ) -> CodingPlan:
        if strategy == self._plan.strategy:
            return self._plan
        self._revise_plan(strategy=strategy, step=step, reason=reason)
        return self._plan

    def create_commitment(
        self,
        objective: str,
        *,
        step: int,
        target: str | None = None,
        acceptance_requirements: tuple[str, ...] = (),
        depends_on: tuple[CommitmentId, ...] = (),
        activate: bool = True,
    ) -> CodingCommitment:
        known = {item.commitment_id for item in self._plan.commitments}
        missing = tuple(item for item in depends_on if item not in known)
        if missing:
            raise ValueError(
                "commitment dependencies must already exist: "
                + ", ".join(str(item) for item in missing)
            )
        item = CodingCommitment(
            commitment_id=CommitmentId.new(),
            objective=objective,
            target=target,
            status=(CommitmentStatus.ACTIVE if activate else CommitmentStatus.PROPOSED),
            acceptance_requirements=acceptance_requirements,
            depends_on=depends_on,
            created_step=step,
            updated_step=step,
        )
        self._replace_commitment(item, reason="commitment_created", step=step, append=True)
        self._emit_commitment(item, reason="commitment_created")
        return item

    def transition_commitment(
        self,
        commitment_id: CommitmentId,
        status: CommitmentStatus,
        *,
        reason: str,
        step: int,
        evidence_refs: tuple[str, ...] = (),
        last_failure: str | None = None,
    ) -> CodingCommitment:
        current = self.require_commitment(commitment_id)
        if status == current.status:
            return current
        if status not in _ALLOWED_COMMITMENT_TRANSITIONS[current.status]:
            raise ValueError(
                "illegal commitment transition: "
                f"{current.status.value} -> {status.value}"
            )
        if status == CommitmentStatus.SATISFIED:
            dependencies = {
                item.commitment_id: item.status for item in self._plan.commitments
            }
            unresolved = tuple(
                dependency
                for dependency in current.depends_on
                if dependencies.get(dependency) != CommitmentStatus.SATISFIED
            )
            if unresolved:
                raise ValueError(
                    "cannot satisfy commitment with unresolved dependencies: "
                    + ", ".join(str(item) for item in unresolved)
                )
            if current.acceptance_requirements and not evidence_refs:
                raise ValueError(
                    "satisfying a commitment with acceptance requirements requires evidence"
                )
        next_item = current.model_copy(
            update={
                "status": status,
                "updated_step": step,
                "evidence_refs": tuple(
                    dict.fromkeys((*current.evidence_refs, *evidence_refs))
                ),
                "last_failure": last_failure,
                "attempts": current.attempts
                + (1 if status in {CommitmentStatus.BLOCKED, CommitmentStatus.ACTIVE} else 0),
            }
        )
        self._replace_commitment(
            next_item,
            reason=reason,
            step=step,
            append=False,
        )
        self._emit_commitment(next_item, reason=reason)
        return next_item

    def require_commitment(self, commitment_id: CommitmentId) -> CodingCommitment:
        for item in self._plan.commitments:
            if item.commitment_id == commitment_id:
                return item
        raise KeyError(f"unknown coding commitment {commitment_id}")

    def mark_root_satisfied(
        self,
        *,
        step: int,
        evidence_refs: tuple[str, ...],
    ) -> CodingCommitment:
        return self.transition_commitment(
            self._root_commitment_id,
            CommitmentStatus.SATISFIED,
            reason="task_completion_accepted",
            step=step,
            evidence_refs=evidence_refs,
        )

    def mark_root_blocked(self, *, step: int, reason: str) -> CodingCommitment:
        root = self.require_commitment(self._root_commitment_id)
        if root.status == CommitmentStatus.ACTIVE:
            return self.transition_commitment(
                root.commitment_id,
                CommitmentStatus.BLOCKED,
                reason=reason,
                step=step,
                last_failure=reason,
            )
        return root

    def record_tool_result(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        succeeded: bool,
        output: dict[str, Any] | None,
        step: int,
    ) -> ProgressSnapshot:
        action_class = self.classify_action(tool_name)
        action_fingerprint = _fingerprint(
            {"tool_name": tool_name, "arguments": arguments}
        )
        self._total_actions += 1

        if action_fingerprint in self._seen_action_fingerprints:
            self._duplicate_actions += 1
        self._seen_action_fingerprints.add(action_fingerprint)
        if action_fingerprint == self._last_action_fingerprint:
            self._repeat_streak += 1
        else:
            self._repeat_streak = 1
        self._last_action_fingerprint = action_fingerprint

        if action_class == ActionClass.INSPECTION:
            self._inspection_actions += 1
            self._inspection_streak += 1
        elif action_class == ActionClass.MUTATION:
            self._mutation_actions += 1
            self._inspection_streak = 0
        elif action_class == ActionClass.EXECUTION:
            self._execution_actions += 1
            self._inspection_streak = 0

        made_progress = False
        if not succeeded:
            self._failed_actions += 1
        elif action_class == ActionClass.MUTATION:
            made_progress = True
            path = str(arguments.get("path") or "").strip()
            if path and path not in self._plan.changed_files:
                self._revise_plan(
                    changed_files=(*self._plan.changed_files, path),
                    step=step,
                    reason="workspace_mutated",
                )
        else:
            evidence_fingerprint = _fingerprint(
                {
                    "tool_name": tool_name,
                    "output": output or {},
                }
            )
            if evidence_fingerprint not in self._seen_evidence_fingerprints:
                self._seen_evidence_fingerprints.add(evidence_fingerprint)
                self._new_evidence_count += 1
                made_progress = True

        if made_progress:
            self._no_progress_streak = 0
        else:
            self._no_progress_streak += 1

        if (
            succeeded
            and action_class == ActionClass.INSPECTION
            and self._plan.phase == CodingPhase.ORIENT
        ):
            self.transition_phase(
                CodingPhase.DIAGNOSE,
                reason="first_successful_repository_inspection",
                step=step,
            )

        return self.progress_snapshot()

    def begin_verification(self, *, step: int, reason: str) -> CodingPlan:
        if self._plan.phase in {
            CodingPhase.COMPLETE,
            CodingPhase.BLOCKED,
            CodingPhase.VERIFY,
        }:
            return self._plan
        return self.transition_phase(CodingPhase.VERIFY, reason=reason, step=step)

    def record_verification(
        self,
        *,
        passed: bool,
        failure_signature: str | None,
        step: int,
        baseline: bool = False,
    ) -> ProgressSnapshot:
        self._verification_attempts += 1
        if passed:
            self._verification_passes += 1
            self._same_failure_count = 0
            self._last_failure_signature = None
            self._no_progress_streak = 0
            if not baseline and self._plan.phase == CodingPhase.VERIFY:
                self.transition_phase(
                    CodingPhase.REVIEW,
                    reason="software_verification_passed",
                    step=step,
                )
        else:
            signature = (failure_signature or "verification_failed").strip()
            if signature == self._last_failure_signature:
                self._same_failure_count += 1
            else:
                self._same_failure_count = 1
            self._last_failure_signature = signature
            if signature not in self._plan.known_failures:
                self._revise_plan(
                    known_failures=(*self._plan.known_failures, signature),
                    step=step,
                    reason="verification_failure_recorded",
                )
            if not baseline and self._plan.phase == CodingPhase.VERIFY:
                self.transition_phase(
                    CodingPhase.IMPLEMENT,
                    reason="software_verification_failed",
                    step=step,
                )
        self._inspection_streak = 0
        return self.progress_snapshot()

    def progress_snapshot(self) -> ProgressSnapshot:
        return ProgressSnapshot(
            total_actions=self._total_actions,
            inspection_actions=self._inspection_actions,
            mutation_actions=self._mutation_actions,
            execution_actions=self._execution_actions,
            failed_actions=self._failed_actions,
            duplicate_actions=self._duplicate_actions,
            repeat_streak=self._repeat_streak,
            inspection_streak=self._inspection_streak,
            no_progress_streak=self._no_progress_streak,
            new_evidence_count=self._new_evidence_count,
            verification_attempts=self._verification_attempts,
            verification_passes=self._verification_passes,
            same_failure_count=self._same_failure_count,
            last_action_fingerprint=self._last_action_fingerprint,
            last_failure_signature=self._last_failure_signature,
            changed_files=self._plan.changed_files,
        )

    def horizon_snapshot(
        self,
        *,
        reasoning_used: int,
        tool_actions_used: int,
    ) -> HorizonSnapshot:
        reasoning_pressure = (
            1.0
            if self.reasoning_limit == 0 and reasoning_used > 0
            else (
                0.0
                if self.reasoning_limit == 0
                else min(1.0, reasoning_used / self.reasoning_limit)
            )
        )
        tool_pressure = (
            1.0
            if self.tool_action_limit == 0 and tool_actions_used > 0
            else (
                0.0
                if self.tool_action_limit == 0
                else min(1.0, tool_actions_used / self.tool_action_limit)
            )
        )
        pressure = max(reasoning_pressure, tool_pressure)
        if pressure < 0.25:
            mode = HorizonMode.EXPLORE
        elif pressure < 0.60:
            mode = HorizonMode.NORMAL
        elif pressure < 0.80:
            mode = HorizonMode.CONVERGE
        elif pressure < 0.95:
            mode = HorizonMode.ENDGAME
        else:
            mode = HorizonMode.CLOSEOUT
        return HorizonSnapshot(
            mode=mode,
            pressure=pressure,
            reasoning_used=reasoning_used,
            reasoning_limit=self.reasoning_limit,
            tool_actions_used=tool_actions_used,
            tool_actions_limit=self.tool_action_limit,
        )

    def assess_intervention(
        self,
        *,
        reasoning_used: int,
        tool_actions_used: int,
    ) -> ControlIntervention:
        progress = self.progress_snapshot()
        horizon = self.horizon_snapshot(
            reasoning_used=reasoning_used,
            tool_actions_used=tool_actions_used,
        )
        phase = self._plan.phase

        if phase in {CodingPhase.COMPLETE, CodingPhase.BLOCKED}:
            intervention = ControlIntervention()
        elif horizon.mode == HorizonMode.CLOSEOUT:
            if progress.mutation_actions > 0 and phase not in {
                CodingPhase.VERIFY,
                CodingPhase.REVIEW,
            }:
                intervention = ControlIntervention(
                    kind=InterventionKind.FORCE_VERIFICATION,
                    reason="remaining budget is in closeout with unreviewed implementation work",
                    directive=(
                        "Stop optional exploration. Run software-owned verification now and "
                        "repair only concrete blockers."
                    ),
                    preferred_actions=("process_run",),
                )
            else:
                intervention = ControlIntervention(
                    kind=InterventionKind.CLOSEOUT,
                    reason="remaining budget is in closeout",
                    directive=(
                        "Do not start optional exploration or refactors. Resolve the active "
                        "commitment, verify, or report a concrete blocker."
                    ),
                )
        elif progress.repeat_streak >= 3:
            intervention = ControlIntervention(
                kind=InterventionKind.CHANGE_APPROACH,
                reason="the same tool action has repeated without a state-changing step",
                directive=(
                    "The previous action pattern is repeating. Do not repeat the same call; "
                    "use existing evidence to choose a different concrete action."
                ),
            )
        elif progress.same_failure_count >= self.max_same_failure_count:
            intervention = ControlIntervention(
                kind=InterventionKind.REPLAN,
                reason="the same verification failure survived multiple repair cycles",
                directive=(
                    "The current repair approach is not changing the verifier outcome. Revisit "
                    "the diagnosis and choose a materially different repair strategy."
                ),
            )
        elif progress.inspection_streak >= self.max_inspection_streak:
            intervention = ControlIntervention(
                kind=InterventionKind.FORCE_IMPLEMENTATION,
                reason="inspection streak exceeded the controller limit without implementation",
                directive=(
                    "Repository orientation has consumed enough actions. Use the evidence already "
                    "collected to implement the smallest plausible change, or report a blocker."
                ),
                preferred_actions=("workspace_patch", "workspace_write", "process_run"),
            )
        elif progress.no_progress_streak >= self.max_no_progress_streak:
            intervention = ControlIntervention(
                kind=InterventionKind.CHANGE_APPROACH,
                reason="recent actions produced no new evidence or state change",
                directive=(
                    "Recent actions are not changing the information or workspace state. Change "
                    "approach instead of spending another equivalent action."
                ),
            )
        elif horizon.mode == HorizonMode.ENDGAME and phase in {
            CodingPhase.ORIENT,
            CodingPhase.DIAGNOSE,
            CodingPhase.PLAN,
        }:
            intervention = ControlIntervention(
                kind=InterventionKind.FORCE_IMPLEMENTATION,
                reason="task is in endgame while still in a pre-implementation phase",
                directive=(
                    "The remaining horizon no longer justifies broad exploration. Converge on the "
                    "smallest viable implementation or a concrete blocker."
                ),
                preferred_actions=("workspace_patch", "workspace_write", "process_run"),
            )
        else:
            intervention = ControlIntervention()

        self._last_intervention = intervention
        if intervention.kind != InterventionKind.NONE and self._recorder is not None:
            self._recorder.emit(
                EventType.CODING_PROGRESS_ASSESSED,
                "coding.control",
                input_refs=(str(self._plan.plan_id),),
                output_refs=(str(self._plan.plan_id),),
                metadata={
                    "phase": phase.value,
                    "progress": progress.model_dump(mode="json"),
                    "horizon": horizon.model_dump(mode="json"),
                    "intervention": intervention.model_dump(mode="json"),
                },
            )
        return intervention

    def apply_intervention(
        self,
        intervention: ControlIntervention,
        *,
        step: int,
    ) -> CodingPlan:
        if intervention.kind == InterventionKind.FORCE_IMPLEMENTATION and self._plan.phase in {
            CodingPhase.ORIENT,
            CodingPhase.DIAGNOSE,
            CodingPhase.PLAN,
        }:
            return self.transition_phase(
                CodingPhase.IMPLEMENT,
                reason=intervention.reason,
                step=step,
            )
        if intervention.kind == InterventionKind.FORCE_VERIFICATION and self._plan.phase not in {
            CodingPhase.VERIFY,
            CodingPhase.REVIEW,
            CodingPhase.COMPLETE,
            CodingPhase.BLOCKED,
        }:
            return self.transition_phase(
                CodingPhase.VERIFY,
                reason=intervention.reason,
                step=step,
            )
        if intervention.kind == InterventionKind.REPLAN and self._plan.phase in {
            CodingPhase.DIAGNOSE,
            CodingPhase.IMPLEMENT,
        }:
            return self.transition_phase(
                CodingPhase.PLAN,
                reason=intervention.reason,
                step=step,
            )
        return self._plan

    def snapshot(
        self,
        *,
        reasoning_used: int,
        tool_actions_used: int,
    ) -> CodingControlSnapshot:
        intervention = self.assess_intervention(
            reasoning_used=reasoning_used,
            tool_actions_used=tool_actions_used,
        )
        return CodingControlSnapshot(
            plan=self._plan,
            progress=self.progress_snapshot(),
            horizon=self.horizon_snapshot(
                reasoning_used=reasoning_used,
                tool_actions_used=tool_actions_used,
            ),
            intervention=intervention,
        )

    @staticmethod
    def classify_action(tool_name: str) -> ActionClass:
        if tool_name in _INSPECTION_TOOLS:
            return ActionClass.INSPECTION
        if tool_name in _MUTATION_TOOLS:
            return ActionClass.MUTATION
        if tool_name in _EXECUTION_TOOLS:
            return ActionClass.EXECUTION
        return ActionClass.OTHER

    def _replace_commitment(
        self,
        commitment: CodingCommitment,
        *,
        reason: str,
        step: int,
        append: bool,
    ) -> None:
        if append:
            commitments = (*self._plan.commitments, commitment)
        else:
            found = False
            updated: list[CodingCommitment] = []
            for item in self._plan.commitments:
                if item.commitment_id == commitment.commitment_id:
                    updated.append(commitment)
                    found = True
                else:
                    updated.append(item)
            if not found:
                raise KeyError(f"unknown coding commitment {commitment.commitment_id}")
            commitments = tuple(updated)
        self._revise_plan(commitments=commitments, step=step, reason=reason)

    def _revise_plan(
        self,
        *,
        step: int,
        reason: str,
        phase: CodingPhase | None = None,
        strategy: tuple[str, ...] | None = None,
        commitments: tuple[CodingCommitment, ...] | None = None,
        changed_files: tuple[str, ...] | None = None,
        known_failures: tuple[str, ...] | None = None,
    ) -> None:
        updates: dict[str, Any] = {
            "revision": self._plan.revision + 1,
            "updated_step": step,
        }
        if phase is not None:
            updates["phase"] = phase
        if strategy is not None:
            updates["strategy"] = strategy
        if commitments is not None:
            updates["commitments"] = commitments
        if changed_files is not None:
            updates["changed_files"] = changed_files
        if known_failures is not None:
            updates["known_failures"] = known_failures
        self._plan = self._plan.model_copy(update=updates)
        self._history.append(self._plan)
        self._persist_plan()
        self._emit_plan(reason)

    def _persist_plan(self) -> None:
        if self._plan_path is None:
            return
        self._plan_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._plan_path.with_name(f"{self._plan_path.name}.tmp")
        temporary.write_text(
            self._plan.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temporary.replace(self._plan_path)

    def _emit_plan(self, reason: str) -> None:
        if self._recorder is None:
            return
        self._recorder.emit(
            EventType.CODING_PLAN_UPDATED,
            "coding.control",
            output_refs=(str(self._plan.plan_id),),
            metadata={
                "reason": reason,
                "revision": self._plan.revision,
                "phase": self._plan.phase.value,
                "pending_commitments": len(self._plan.pending_commitments),
                "changed_files": list(self._plan.changed_files),
            },
        )

    def _emit_commitment(self, item: CodingCommitment, *, reason: str) -> None:
        if self._recorder is None:
            return
        self._recorder.emit(
            EventType.CODING_COMMITMENT_CHANGED,
            "coding.control",
            input_refs=(str(self._plan.plan_id),),
            output_refs=(str(item.commitment_id),),
            metadata={
                "reason": reason,
                "status": item.status.value,
                "objective": item.objective,
                "target": item.target,
                "plan_revision": self._plan.revision,
                "evidence_refs": list(item.evidence_refs),
            },
        )
