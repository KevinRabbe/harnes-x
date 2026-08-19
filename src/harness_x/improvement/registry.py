"""Authoritative in-process registry for bounded improvement candidates."""

from __future__ import annotations

from harness_x.core.events import EventType
from harness_x.core.ids import CandidateId
from harness_x.telemetry import TraceRecorder

from .models import (
    CandidateStatus,
    ImprovementCandidate,
    ImprovementProposal,
)
from .policy import InitialImprovementPolicy


class ImprovementCandidateError(ValueError):
    pass


class ImprovementCandidateRegistry:
    """Own candidate identity and lifecycle before sandbox execution exists.

    The registry may create, statically qualify/reject, or invalidate a candidate.
    It cannot apply patches and intentionally has no promotion method.
    """

    component = "improvement.candidates"

    def __init__(self, recorder: TraceRecorder) -> None:
        self.recorder = recorder
        self._current: dict[str, ImprovementCandidate] = {}
        self._history: dict[str, list[ImprovementCandidate]] = {}

    def create(self, proposal: ImprovementProposal) -> ImprovementCandidate:
        if proposal.supersedes is not None:
            prior = self._current.get(str(proposal.supersedes))
            if prior is None:
                raise ImprovementCandidateError("superseded candidate does not exist")
            if prior.proposal.change_type != proposal.change_type:
                raise ImprovementCandidateError(
                    "a replacement candidate must preserve the change class"
                )

        candidate_id = CandidateId.new()
        candidate = ImprovementCandidate(
            candidate_id=candidate_id,
            revision=1,
            proposal=proposal,
            proposal_fingerprint=proposal.fingerprint,
            status=CandidateStatus.PROPOSED,
        )
        self._record(candidate)
        self.recorder.emit(
            EventType.CANDIDATE_CREATED,
            self.component,
            output_refs=(str(candidate_id),),
            metadata={
                "candidate_kind": "system_improvement",
                "status": candidate.status.value,
                "change_type": proposal.change_type.value,
                "proposal_fingerprint": candidate.proposal_fingerprint,
                "baseline_version": str(proposal.baseline_version),
            },
        )
        return candidate

    def qualify(
        self,
        candidate_id: CandidateId,
        *,
        policy: InitialImprovementPolicy | None = None,
    ) -> ImprovementCandidate:
        current = self.require(candidate_id)
        if current.status != CandidateStatus.PROPOSED:
            raise ImprovementCandidateError(
                f"only proposed candidates can be qualified, got {current.status.value}"
            )
        policy = policy or InitialImprovementPolicy()
        result = policy.qualify(
            current.proposal,
            current_system_version=self.recorder.system_version,
        )
        status = (
            CandidateStatus.SANDBOX_ELIGIBLE
            if result.eligible
            else CandidateStatus.REJECTED
        )
        reason = (
            "static_policy_passed"
            if result.eligible
            else ";".join(result.reasons)
        )
        updated = current.model_copy(
            update={
                "revision": current.revision + 1,
                "status": status,
                "qualification": result,
                "status_reason": reason,
            }
        )
        # Revalidate after model_copy so status/qualification invariants are enforced.
        updated = ImprovementCandidate.model_validate(updated.model_dump(mode="python"))
        self._record(updated)
        self.recorder.emit(
            EventType.CANDIDATE_EVALUATED if result.eligible else EventType.CANDIDATE_REJECTED,
            self.component,
            input_refs=(str(candidate_id), f"policy:{result.policy_version}"),
            output_refs=(str(candidate_id),),
            metadata={
                "candidate_kind": "system_improvement",
                "status": status.value,
                "eligible_for_sandbox": result.eligible,
                "policy_version": result.policy_version,
                "reasons": list(result.reasons),
                "revision": updated.revision,
            },
        )
        return updated

    def invalidate(
        self,
        candidate_id: CandidateId,
        *,
        reason: str,
        evidence_refs: tuple[str, ...],
    ) -> ImprovementCandidate:
        current = self.require(candidate_id)
        if current.status not in {
            CandidateStatus.PROPOSED,
            CandidateStatus.SANDBOX_ELIGIBLE,
        }:
            raise ImprovementCandidateError(
                f"candidate in {current.status.value} cannot be invalidated"
            )
        reason = reason.strip()
        evidence_refs = tuple(ref.strip() for ref in evidence_refs if ref.strip())
        if not reason:
            raise ImprovementCandidateError("invalidation reason cannot be blank")
        if not evidence_refs:
            raise ImprovementCandidateError("invalidation requires evidence references")

        updated = current.model_copy(
            update={
                "revision": current.revision + 1,
                "status": CandidateStatus.INVALIDATED,
                "status_reason": reason,
                "evidence_refs": evidence_refs,
            }
        )
        updated = ImprovementCandidate.model_validate(updated.model_dump(mode="python"))
        self._record(updated)
        self.recorder.emit(
            EventType.CANDIDATE_INVALIDATED,
            self.component,
            input_refs=(str(candidate_id), *evidence_refs),
            output_refs=(str(candidate_id),),
            metadata={
                "candidate_kind": "system_improvement",
                "status": CandidateStatus.INVALIDATED.value,
                "reason": reason,
                "revision": updated.revision,
            },
        )
        return updated

    def get(self, candidate_id: CandidateId) -> ImprovementCandidate | None:
        return self._current.get(str(candidate_id))

    def require(self, candidate_id: CandidateId) -> ImprovementCandidate:
        candidate = self.get(candidate_id)
        if candidate is None:
            raise ImprovementCandidateError(f"unknown candidate {candidate_id}")
        return candidate

    def history(self, candidate_id: CandidateId) -> tuple[ImprovementCandidate, ...]:
        values = self._history.get(str(candidate_id))
        if values is None:
            raise ImprovementCandidateError(f"unknown candidate {candidate_id}")
        return tuple(values)

    def all(self) -> tuple[ImprovementCandidate, ...]:
        return tuple(
            self._current[key]
            for key in sorted(self._current)
        )

    def _record(self, candidate: ImprovementCandidate) -> None:
        key = str(candidate.candidate_id)
        history = self._history.setdefault(key, [])
        if history and candidate.revision != history[-1].revision + 1:
            raise ImprovementCandidateError("candidate revisions must be contiguous")
        if history and candidate.proposal_fingerprint != history[0].proposal_fingerprint:
            raise ImprovementCandidateError("candidate proposal cannot mutate across revisions")
        history.append(candidate)
        self._current[key] = candidate
