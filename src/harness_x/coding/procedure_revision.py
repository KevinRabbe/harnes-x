"""Failure-driven procedure revision candidates with verified validation lineage.

M29 can suspend a historically supported procedure. M30 does not mutate that procedure in
place. It records bounded revision candidates linked to the suspended parent, validates them
through distinct software-verified task episodes, and promotes only a replacement procedure
that independently becomes active in M28 project memory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .procedure_reliability import ProcedureReliabilityRecord, ProcedureReliabilityStatus
from .project_memory import (
    ProjectMemoryEntry,
    ProjectMemoryEntryKind,
    ProjectMemoryEntryState,
    ProjectMemoryTaskEpisode,
)


_WS_RE = re.compile(r"\s+")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalized_text(value: str) -> str:
    return _WS_RE.sub(" ", value.strip()).casefold()


def _normalized_unique(values: tuple[str, ...], *, max_chars: int) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = raw.strip()[:max_chars]
        if not item:
            continue
        key = _normalized_text(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


def _revision_content_fingerprint(statement: str, steps: tuple[str, ...]) -> str:
    return _sha256(
        _canonical(
            {
                "statement": _normalized_text(statement),
                "steps": tuple(_normalized_text(item) for item in steps),
            }
        )
    )


def _parent_content_fingerprint(parent: ProjectMemoryEntry) -> str:
    return _revision_content_fingerprint(parent.statement, parent.steps)


class ProcedureRevisionState(StrEnum):
    CANDIDATE = "candidate"
    READY = "ready"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ProcedureRevisionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["procedure-revision-policy-v1"] = "procedure-revision-policy-v1"
    successful_validations_to_promote: int = Field(default=2, ge=1, le=10)
    max_failed_validations: int = Field(default=2, ge=1, le=10)
    max_open_candidates_per_parent: int = Field(default=4, ge=1, le=16)


class ProcedureRevisionProposal(BaseModel):
    """Model advisory proposal for a bounded replacement of one suspended procedure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    parent_procedure_id: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=3000)
    steps: tuple[str, ...] = Field(min_length=1, max_length=24)
    task_categories: tuple[str, ...] = Field(default=(), max_length=24)
    rationale: str = Field(min_length=1, max_length=3000)

    @field_validator("parent_procedure_id", "statement", "rationale")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("procedure revision text cannot be blank")
        return value

    @field_validator("steps")
    @classmethod
    def normalize_steps(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        value = _normalized_unique(value, max_chars=1600)
        if not value:
            raise ValueError("procedure revision requires at least one non-blank step")
        return value

    @field_validator("task_categories")
    @classmethod
    def normalize_categories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalized_unique(value, max_chars=800)


class ProcedureRevisionUpdateProposal(BaseModel):
    """Model advisory M30 protocol; software owns candidate admission and validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["procedure_revision_update"] = "procedure_revision_update"
    candidates: tuple[ProcedureRevisionProposal, ...] = Field(default=(), max_length=4)
    used_revision_candidate_ids: tuple[str, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def require_change(self) -> "ProcedureRevisionUpdateProposal":
        if not self.candidates and not self.used_revision_candidate_ids:
            raise ValueError("procedure revision update must contain candidates or trial IDs")
        return self


class ProcedureRevisionCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    parent_procedure_id: str
    parent_content_fingerprint: str = Field(min_length=64, max_length=64)
    statement: str = Field(min_length=1, max_length=3000)
    steps: tuple[str, ...] = Field(min_length=1, max_length=24)
    task_categories: tuple[str, ...] = Field(default=(), max_length=24)
    rationale: str = Field(min_length=1, max_length=3000)
    content_fingerprint: str = Field(min_length=64, max_length=64)
    replacement_memory_key: str = Field(min_length=1, max_length=180)
    state: ProcedureRevisionState = ProcedureRevisionState.CANDIDATE
    origin_episode_id: str
    origin_reliability_revision: int = Field(ge=1)
    origin_suspension_reason: str | None = Field(default=None, max_length=800)
    success_episode_ids: tuple[str, ...] = Field(default=(), max_length=32)
    failure_episode_ids: tuple[str, ...] = Field(default=(), max_length=32)
    known_failure_modes: tuple[str, ...] = Field(default=(), max_length=32)
    last_validation_episode_id: str | None = None
    replacement_entry_id: str | None = None
    terminal_reason: str | None = Field(default=None, max_length=1200)
    created_at: datetime
    created_revision: int = Field(ge=1)
    updated_revision: int = Field(ge=1)

    @property
    def success_count(self) -> int:
        return len(self.success_episode_ids)

    @property
    def failure_count(self) -> int:
        return len(self.failure_episode_ids)


class ProcedureRevisionValidationEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["procedure-revision-validation-v1"] = (
        "procedure-revision-validation-v1"
    )
    validation_id: str
    project_id: str
    candidate_id: str
    parent_procedure_id: str
    episode_id: str
    success: bool
    failure_mode: str | None = Field(default=None, max_length=1600)
    created_at: datetime
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ProcedureRevisionValidationEvidence":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", _sha256(_canonical(material)))
        return self


class ProcedureRevisionStoreState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["procedure-revision-state-v1"] = "procedure-revision-state-v1"
    project_id: str
    revision: int = Field(ge=1)
    policy: ProcedureRevisionPolicy
    candidates: tuple[ProcedureRevisionCandidate, ...] = ()
    validation_total: int = Field(default=0, ge=0)
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ProcedureRevisionStoreState":
        ids = [item.candidate_id for item in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("procedure revision candidate IDs must be unique")
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", _sha256(_canonical(material)))
        return self


class ProcedureRevisionStore:
    """Persistent M30 candidate lineage and append-only validation evidence."""

    def __init__(
        self,
        project_memory_root: str | Path,
        *,
        project_id: str,
        policy: ProcedureRevisionPolicy | None = None,
    ) -> None:
        self.root = Path(project_memory_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "procedure-revisions.json"
        self.validation_path = self.root / "procedure-revision-validations.jsonl"
        requested = policy or ProcedureRevisionPolicy()
        if self.state_path.exists():
            self.state = self._load_state()
            if self.state.project_id != project_id:
                raise ValueError("procedure revision state belongs to a different project")
            if policy is not None and self.state.policy != requested:
                raise ValueError("procedure revision policy does not match persisted state")
            self._reconcile_validation_total()
        else:
            self.state = ProcedureRevisionStoreState(
                project_id=project_id,
                revision=1,
                policy=requested,
            )
            self._write_state()

    def propose(
        self,
        *,
        proposal: ProcedureRevisionProposal,
        parent: ProjectMemoryEntry,
        origin_episode: ProjectMemoryTaskEpisode,
        reliability: ProcedureReliabilityRecord,
    ) -> ProcedureRevisionCandidate:
        if parent.kind != ProjectMemoryEntryKind.PROCEDURE:
            raise ValueError("procedure revision parent must be a procedure")
        if parent.entry_id != proposal.parent_procedure_id:
            raise ValueError("procedure revision parent ID does not match supplied parent")
        if parent.state != ProjectMemoryEntryState.ACTIVE or parent.conflicts_with:
            raise ValueError("procedure revision parent must remain M28 active and conflict-free")
        if reliability.procedure_id != parent.entry_id:
            raise ValueError("procedure revision reliability record belongs to another procedure")
        if reliability.status != ProcedureReliabilityStatus.SUSPENDED:
            raise ValueError("procedure revision requires an M29-suspended parent")

        statement = proposal.statement.strip()
        steps = _normalized_unique(proposal.steps, max_chars=1600)
        categories = _normalized_unique(proposal.task_categories, max_chars=800)
        content_fingerprint = _revision_content_fingerprint(statement, steps)
        parent_fingerprint = _parent_content_fingerprint(parent)
        if content_fingerprint == parent_fingerprint:
            raise ValueError("procedure revision candidate must differ from its parent")

        existing = next(
            (
                item
                for item in self.state.candidates
                if item.parent_procedure_id == parent.entry_id
                and item.content_fingerprint == content_fingerprint
                and item.state
                not in (ProcedureRevisionState.REJECTED, ProcedureRevisionState.SUPERSEDED)
            ),
            None,
        )
        if existing is not None:
            return existing

        open_count = sum(
            1
            for item in self.state.candidates
            if item.parent_procedure_id == parent.entry_id
            and item.state in (ProcedureRevisionState.CANDIDATE, ProcedureRevisionState.READY)
        )
        if open_count >= self.state.policy.max_open_candidates_per_parent:
            raise RuntimeError("procedure revision open-candidate capacity reached for parent")

        candidate_id = f"prev_{uuid.uuid4().hex}"
        next_revision = self.state.revision + 1
        candidate = ProcedureRevisionCandidate(
            candidate_id=candidate_id,
            parent_procedure_id=parent.entry_id,
            parent_content_fingerprint=parent_fingerprint,
            statement=statement,
            steps=steps,
            task_categories=categories,
            rationale=proposal.rationale.strip(),
            content_fingerprint=content_fingerprint,
            replacement_memory_key=f"hx-revision/{candidate_id}",
            origin_episode_id=origin_episode.episode_id,
            origin_reliability_revision=reliability.updated_revision,
            origin_suspension_reason=reliability.suspension_reason,
            created_at=datetime.now(timezone.utc),
            created_revision=next_revision,
            updated_revision=next_revision,
        )
        self._replace(
            [*self.state.candidates, candidate],
            revision=next_revision,
            validation_total=self.state.validation_total,
        )
        return candidate

    def record_validation(
        self,
        *,
        candidate_id: str,
        episode: ProjectMemoryTaskEpisode,
        success: bool,
        failure_mode: str | None = None,
    ) -> ProcedureRevisionCandidate:
        index = self._index(candidate_id)
        current = self.state.candidates[index]
        existing = self._find_validation(candidate_id, episode.episode_id)
        if existing is not None and existing.success != success:
            raise ValueError("existing procedure revision validation disagrees with retry outcome")
        if current.last_validation_episode_id == episode.episode_id:
            return current
        if existing is None and current.state != ProcedureRevisionState.CANDIDATE:
            raise ValueError("only open procedure revision candidates may receive new trials")

        failure = failure_mode.strip()[:1600] if failure_mode else None
        if not success and not failure:
            failure = "verified_task_failed_while_trialing_procedure_revision"
        if existing is None:
            existing = ProcedureRevisionValidationEvidence(
                validation_id=f"prval_{uuid.uuid4().hex}",
                project_id=self.state.project_id,
                candidate_id=current.candidate_id,
                parent_procedure_id=current.parent_procedure_id,
                episode_id=episode.episode_id,
                success=success,
                failure_mode=failure,
                created_at=datetime.now(timezone.utc),
            )
            self._append_validation(existing)
        else:
            failure = existing.failure_mode

        successes = current.success_episode_ids
        failures = current.failure_episode_ids
        failure_modes = current.known_failure_modes
        if existing.success:
            if episode.episode_id not in successes:
                successes = (*successes, episode.episode_id)
        else:
            if episode.episode_id not in failures:
                failures = (*failures, episode.episode_id)
            if failure:
                failure_modes = _normalized_unique(
                    (*failure_modes, failure),
                    max_chars=1600,
                )

        if len(failures) >= self.state.policy.max_failed_validations:
            state = ProcedureRevisionState.REJECTED
            terminal_reason = f"verified_revision_failures:{len(failures)}"
        elif len(successes) >= self.state.policy.successful_validations_to_promote:
            state = ProcedureRevisionState.READY
            terminal_reason = None
        else:
            state = ProcedureRevisionState.CANDIDATE
            terminal_reason = None

        next_revision = self.state.revision + 1
        updated = current.model_copy(
            update={
                "state": state,
                "success_episode_ids": successes,
                "failure_episode_ids": failures,
                "known_failure_modes": failure_modes,
                "last_validation_episode_id": episode.episode_id,
                "terminal_reason": terminal_reason,
                "updated_revision": next_revision,
            }
        )
        candidates = list(self.state.candidates)
        candidates[index] = updated
        self._replace(
            candidates,
            revision=next_revision,
            validation_total=max(self.state.validation_total, self._validation_ledger_count()),
        )
        return updated

    def promote(
        self,
        *,
        candidate_id: str,
        replacement: ProjectMemoryEntry,
        parent_reliability: ProcedureReliabilityRecord,
    ) -> ProcedureRevisionCandidate:
        index = self._index(candidate_id)
        current = self.state.candidates[index]
        if current.state != ProcedureRevisionState.READY:
            raise ValueError("procedure revision candidate is not ready for promotion")
        if parent_reliability.procedure_id != current.parent_procedure_id:
            raise ValueError("procedure revision parent reliability record mismatch")
        if parent_reliability.status != ProcedureReliabilityStatus.SUSPENDED:
            raise ValueError("procedure revision parent must still be suspended at promotion")
        if replacement.kind != ProjectMemoryEntryKind.PROCEDURE:
            raise ValueError("procedure revision replacement must be a procedure")
        if replacement.key != current.replacement_memory_key:
            raise ValueError("procedure revision replacement memory key mismatch")
        if replacement.state != ProjectMemoryEntryState.ACTIVE or replacement.conflicts_with:
            raise ValueError("procedure revision replacement must be M28 active and conflict-free")
        if _revision_content_fingerprint(replacement.statement, replacement.steps) != current.content_fingerprint:
            raise ValueError("procedure revision replacement content mismatch")

        next_revision = self.state.revision + 1
        candidates = list(self.state.candidates)
        candidates[index] = current.model_copy(
            update={
                "state": ProcedureRevisionState.PROMOTED,
                "replacement_entry_id": replacement.entry_id,
                "terminal_reason": "software_verified_revision_promoted",
                "updated_revision": next_revision,
            }
        )
        for other_index, other in enumerate(candidates):
            if other_index == index:
                continue
            if (
                other.parent_procedure_id == current.parent_procedure_id
                and other.state in (ProcedureRevisionState.CANDIDATE, ProcedureRevisionState.READY)
            ):
                candidates[other_index] = other.model_copy(
                    update={
                        "state": ProcedureRevisionState.SUPERSEDED,
                        "terminal_reason": f"another_revision_promoted:{current.candidate_id}",
                        "updated_revision": next_revision,
                    }
                )
        self._replace(
            candidates,
            revision=next_revision,
            validation_total=self.state.validation_total,
        )
        return self.state.candidates[index]

    def supersede_open_for_parent(self, parent_procedure_id: str, *, reason: str) -> None:
        candidates = list(self.state.candidates)
        indexes = [
            index
            for index, item in enumerate(candidates)
            if item.parent_procedure_id == parent_procedure_id
            and item.state in (ProcedureRevisionState.CANDIDATE, ProcedureRevisionState.READY)
        ]
        if not indexes:
            return
        next_revision = self.state.revision + 1
        for index in indexes:
            candidates[index] = candidates[index].model_copy(
                update={
                    "state": ProcedureRevisionState.SUPERSEDED,
                    "terminal_reason": reason[:1200],
                    "updated_revision": next_revision,
                }
            )
        self._replace(
            candidates,
            revision=next_revision,
            validation_total=self.state.validation_total,
        )

    def candidate(self, candidate_id: str) -> ProcedureRevisionCandidate:
        return self.state.candidates[self._index(candidate_id)]

    def open_candidates(self) -> tuple[ProcedureRevisionCandidate, ...]:
        return tuple(
            item
            for item in self.state.candidates
            if item.state in (ProcedureRevisionState.CANDIDATE, ProcedureRevisionState.READY)
        )

    def promoted_candidates(self) -> tuple[ProcedureRevisionCandidate, ...]:
        return tuple(
            item for item in self.state.candidates if item.state == ProcedureRevisionState.PROMOTED
        )

    def promoted_parent_ids(self) -> frozenset[str]:
        return frozenset(item.parent_procedure_id for item in self.promoted_candidates())

    def promoted_replacement_ids(self) -> frozenset[str]:
        return frozenset(
            item.replacement_entry_id
            for item in self.promoted_candidates()
            if item.replacement_entry_id is not None
        )

    def projection(self) -> dict[str, object]:
        counts = {
            state.value: sum(1 for item in self.state.candidates if item.state == state)
            for state in ProcedureRevisionState
        }
        open_rows = [
            {
                "candidate_id": item.candidate_id,
                "parent_procedure_id": item.parent_procedure_id,
                "statement": item.statement,
                "steps": item.steps,
                "task_categories": item.task_categories,
                "rationale": item.rationale,
                "state": item.state,
                "success_count": item.success_count,
                "failure_count": item.failure_count,
                "successes_required": self.state.policy.successful_validations_to_promote,
                "failures_before_reject": self.state.policy.max_failed_validations,
            }
            for item in self.open_candidates()[:12]
        ]
        promoted = [
            {
                "candidate_id": item.candidate_id,
                "parent_procedure_id": item.parent_procedure_id,
                "replacement_entry_id": item.replacement_entry_id,
                "statement": item.statement,
            }
            for item in self.promoted_candidates()[:12]
        ]
        return {
            "schema_version": self.state.schema_version,
            "revision": self.state.revision,
            "fingerprint": self.state.fingerprint,
            "validation_total": self.state.validation_total,
            "policy": self.state.policy.model_dump(mode="json"),
            "candidate_counts": counts,
            "open_candidates": open_rows,
            "promoted_lineage": promoted,
        }

    def _index(self, candidate_id: str) -> int:
        for index, item in enumerate(self.state.candidates):
            if item.candidate_id == candidate_id:
                return index
        raise KeyError(f"unknown procedure revision candidate {candidate_id}")

    def _append_validation(self, evidence: ProcedureRevisionValidationEvidence) -> None:
        with self.validation_path.open("ab") as handle:
            handle.write(_canonical(evidence.model_dump(mode="json")) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _validation_rows(self) -> tuple[ProcedureRevisionValidationEvidence, ...]:
        if not self.validation_path.exists():
            return ()
        rows: list[ProcedureRevisionValidationEvidence] = []
        seen: set[tuple[str, str]] = set()
        with self.validation_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                raw = json.loads(line)
                stored = str(raw.get("fingerprint", ""))
                evidence = ProcedureRevisionValidationEvidence.model_validate(raw)
                if stored != evidence.fingerprint:
                    raise ValueError("procedure revision validation fingerprint mismatch")
                if evidence.project_id != self.state.project_id:
                    raise ValueError("procedure revision validation belongs to a different project")
                key = (evidence.candidate_id, evidence.episode_id)
                if key in seen:
                    raise ValueError("duplicate procedure revision validation for one task episode")
                seen.add(key)
                rows.append(evidence)
        return tuple(rows)

    def _find_validation(
        self, candidate_id: str, episode_id: str
    ) -> ProcedureRevisionValidationEvidence | None:
        for item in self._validation_rows():
            if item.candidate_id == candidate_id and item.episode_id == episode_id:
                return item
        return None

    def _validation_ledger_count(self) -> int:
        return len(self._validation_rows())

    def _replace(
        self,
        candidates: list[ProcedureRevisionCandidate],
        *,
        revision: int,
        validation_total: int,
    ) -> None:
        self.state = ProcedureRevisionStoreState.model_validate(
            {
                **self.state.model_dump(
                    mode="python",
                    exclude={"fingerprint", "revision", "candidates", "validation_total"},
                ),
                "revision": revision,
                "candidates": tuple(candidates),
                "validation_total": validation_total,
            }
        )
        self._write_state()

    def _load_state(self) -> ProcedureRevisionStoreState:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load procedure revision state: {exc}") from exc
        stored = str(raw.get("fingerprint", ""))
        state = ProcedureRevisionStoreState.model_validate(raw)
        if stored != state.fingerprint:
            raise ValueError("procedure revision state fingerprint mismatch")
        return state

    def _reconcile_validation_total(self) -> None:
        count = self._validation_ledger_count()
        if count == 0 and not self.validation_path.exists():
            if self.state.validation_total != 0:
                raise ValueError("procedure revision validation ledger is missing")
            return
        if count < self.state.validation_total:
            raise ValueError("procedure revision validation ledger is shorter than committed state")
        if count == self.state.validation_total:
            return
        self.state = ProcedureRevisionStoreState.model_validate(
            {
                **self.state.model_dump(
                    mode="python", exclude={"fingerprint", "revision", "validation_total"}
                ),
                "revision": self.state.revision + 1,
                "validation_total": count,
            }
        )
        self._write_state()

    def _write_state(self) -> None:
        payload = self.state.model_dump_json(indent=2) + "\n"
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, self.state_path)
