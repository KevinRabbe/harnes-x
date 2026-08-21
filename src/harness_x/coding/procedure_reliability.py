"""Software-owned reliability gate for M28 active project procedures.

M28 answers whether repeated verified tasks supported a procedure. M29 keeps a separate
reuse-outcome history and can temporarily suspend a still-supported procedure when verified
reuse outcomes show it has become unreliable. Historical M28 support is never rewritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .project_memory import ProjectMemoryEntry, ProjectMemoryEntryKind, ProjectMemoryTaskEpisode


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


class ProcedureReliabilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    SUSPENDED = "suspended"


class ProcedureReliabilityPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["procedure-reliability-policy-v1"] = "procedure-reliability-policy-v1"
    consecutive_failures_to_suspend: int = Field(default=2, ge=1, le=10)
    min_usages_for_rate_check: int = Field(default=4, ge=2, le=100)
    min_success_rate: float = Field(default=0.5, ge=0.0, le=1.0)
    successful_supports_to_revalidate: int = Field(default=2, ge=1, le=10)


class ProcedureUsageEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["procedure-usage-evidence-v1"] = "procedure-usage-evidence-v1"
    usage_id: str
    project_id: str
    procedure_id: str
    episode_id: str
    success: bool
    failure_mode: str | None = Field(default=None, max_length=1600)
    created_at: datetime
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ProcedureUsageEvidence":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", _sha256(_canonical(material)))
        return self


class ProcedureReliabilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    procedure_id: str
    status: ProcedureReliabilityStatus = ProcedureReliabilityStatus.ELIGIBLE
    usage_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    consecutive_failures: int = Field(default=0, ge=0)
    suspended_at_support_count: int | None = Field(default=None, ge=0)
    revalidation_episode_ids: tuple[str, ...] = Field(default=(), max_length=32)
    suspension_reason: str | None = Field(default=None, max_length=800)
    last_episode_id: str | None = None
    updated_revision: int = Field(default=1, ge=1)

    @property
    def success_rate(self) -> float | None:
        return None if self.usage_count == 0 else self.success_count / self.usage_count


class ProcedureReliabilityState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["procedure-reliability-state-v1"] = "procedure-reliability-state-v1"
    project_id: str
    revision: int = Field(ge=1)
    policy: ProcedureReliabilityPolicy
    records: tuple[ProcedureReliabilityRecord, ...] = ()
    usage_total: int = Field(default=0, ge=0)
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ProcedureReliabilityState":
        ids = [item.procedure_id for item in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("procedure reliability IDs must be unique")
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", _sha256(_canonical(material)))
        return self


class ProcedureReliabilityStore:
    """Atomic reliability state plus append-only verified reuse outcomes."""

    def __init__(
        self,
        project_memory_root: str | Path,
        *,
        project_id: str,
        policy: ProcedureReliabilityPolicy | None = None,
    ) -> None:
        self.root = Path(project_memory_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "procedure-reliability.json"
        self.usage_path = self.root / "procedure-reliability-usage.jsonl"
        requested_policy = policy or ProcedureReliabilityPolicy()
        if self.state_path.exists():
            self.state = self._load_state()
            if self.state.project_id != project_id:
                raise ValueError("procedure reliability state belongs to a different project")
            if policy is not None and self.state.policy != requested_policy:
                raise ValueError("procedure reliability policy does not match persisted state")
            self._reconcile_usage_total()
        else:
            self.state = ProcedureReliabilityState(
                project_id=project_id,
                revision=1,
                policy=requested_policy,
            )
            self._write_state()

    def is_eligible(self, procedure_id: str) -> bool:
        record = self._get(procedure_id)
        return record is None or record.status == ProcedureReliabilityStatus.ELIGIBLE

    def record_usage(
        self,
        *,
        procedure: ProjectMemoryEntry,
        episode: ProjectMemoryTaskEpisode,
        success: bool,
        failure_mode: str | None = None,
    ) -> ProcedureReliabilityRecord:
        if procedure.kind != ProjectMemoryEntryKind.PROCEDURE:
            raise ValueError("procedure reliability accepts only procedure entries")
        current = self._get(procedure.entry_id)
        existing = self._find_usage(procedure.entry_id, episode.episode_id)
        if existing is None and not self.is_eligible(procedure.entry_id):
            raise ValueError("suspended procedure cannot record a new automatic reuse")
        if existing is not None and existing.success != success:
            raise ValueError("existing procedure usage evidence disagrees with retry outcome")
        if current is not None and current.last_episode_id == episode.episode_id:
            return current

        failure = failure_mode.strip()[:1600] if failure_mode else None
        if not success and not failure:
            failure = "verified_task_failed_after_declared_procedure_reuse"
        if existing is not None:
            failure = existing.failure_mode
        else:
            existing = ProcedureUsageEvidence(
                usage_id=f"pusage_{uuid.uuid4().hex}",
                project_id=self.state.project_id,
                procedure_id=procedure.entry_id,
                episode_id=episode.episode_id,
                success=success,
                failure_mode=failure,
                created_at=datetime.now(timezone.utc),
            )
            self._append_usage(existing)

        records = list(self.state.records)
        index, current = self._record_or_default(records, procedure.entry_id)
        next_revision = self.state.revision + 1
        usage_count = current.usage_count + 1
        success_count = current.success_count + int(existing.success)
        failure_count = current.failure_count + int(not existing.success)
        consecutive = 0 if existing.success else current.consecutive_failures + 1
        status = ProcedureReliabilityStatus.ELIGIBLE
        reason = None
        suspended_support_count = None
        rate = success_count / usage_count
        policy = self.state.policy
        if consecutive >= policy.consecutive_failures_to_suspend:
            status = ProcedureReliabilityStatus.SUSPENDED
            reason = f"consecutive_verified_reuse_failures:{consecutive}"
            suspended_support_count = procedure.support_count
        elif usage_count >= policy.min_usages_for_rate_check and rate < policy.min_success_rate:
            status = ProcedureReliabilityStatus.SUSPENDED
            reason = f"verified_reuse_success_rate_below_threshold:{rate:.4f}"
            suspended_support_count = procedure.support_count

        updated = current.model_copy(
            update={
                "status": status,
                "usage_count": usage_count,
                "success_count": success_count,
                "failure_count": failure_count,
                "consecutive_failures": consecutive,
                "suspended_at_support_count": suspended_support_count,
                "revalidation_episode_ids": (),
                "suspension_reason": reason,
                "last_episode_id": episode.episode_id,
                "updated_revision": next_revision,
            }
        )
        records[index] = updated
        self._replace(
            records,
            revision=next_revision,
            usage_total=self.state.usage_total + int(existing.usage_id.startswith("pusage_") and self._count_usage_matches(procedure.entry_id, episode.episode_id) == 1 and self.state.usage_total < self._usage_ledger_count()),
        )
        return updated

    def observe_verified_support(
        self,
        *,
        procedure: ProjectMemoryEntry,
        episode: ProjectMemoryTaskEpisode,
    ) -> ProcedureReliabilityRecord | None:
        if not episode.succeeded or procedure.kind != ProjectMemoryEntryKind.PROCEDURE:
            return self._get(procedure.entry_id)
        current = self._get(procedure.entry_id)
        if current is None or current.status != ProcedureReliabilityStatus.SUSPENDED:
            return current
        baseline = current.suspended_at_support_count
        if baseline is None or procedure.support_count <= baseline:
            return current
        if episode.episode_id in current.revalidation_episode_ids:
            return current

        episodes = (*current.revalidation_episode_ids, episode.episode_id)
        next_revision = self.state.revision + 1
        recovered = len(episodes) >= self.state.policy.successful_supports_to_revalidate
        updated = current.model_copy(
            update={
                "status": (
                    ProcedureReliabilityStatus.ELIGIBLE
                    if recovered
                    else ProcedureReliabilityStatus.SUSPENDED
                ),
                "consecutive_failures": 0 if recovered else current.consecutive_failures,
                "suspended_at_support_count": None if recovered else baseline,
                "revalidation_episode_ids": () if recovered else episodes,
                "suspension_reason": None if recovered else current.suspension_reason,
                "last_episode_id": episode.episode_id,
                "updated_revision": next_revision,
            }
        )
        records = list(self.state.records)
        records[self._index(procedure.entry_id)] = updated
        self._replace(records, revision=next_revision, usage_total=self.state.usage_total)
        return updated

    def record_for(self, procedure_id: str) -> ProcedureReliabilityRecord | None:
        return self._get(procedure_id)

    def projection(self) -> dict[str, object]:
        suspended = [
            item
            for item in self.state.records
            if item.status == ProcedureReliabilityStatus.SUSPENDED
        ]
        return {
            "schema_version": self.state.schema_version,
            "revision": self.state.revision,
            "fingerprint": self.state.fingerprint,
            "usage_total": self.state.usage_total,
            "policy": self.state.policy.model_dump(mode="json"),
            "eligible_record_count": sum(
                1
                for item in self.state.records
                if item.status == ProcedureReliabilityStatus.ELIGIBLE
            ),
            "suspended_count": len(suspended),
            "suspended": [
                {
                    "procedure_id": item.procedure_id,
                    "usage_count": item.usage_count,
                    "success_count": item.success_count,
                    "failure_count": item.failure_count,
                    "consecutive_failures": item.consecutive_failures,
                    "suspension_reason": item.suspension_reason,
                    "revalidation_supports": len(item.revalidation_episode_ids),
                    "revalidation_required": self.state.policy.successful_supports_to_revalidate,
                }
                for item in suspended[:24]
            ],
        }

    def _record_or_default(
        self,
        records: list[ProcedureReliabilityRecord],
        procedure_id: str,
    ) -> tuple[int, ProcedureReliabilityRecord]:
        for index, item in enumerate(records):
            if item.procedure_id == procedure_id:
                return index, item
        item = ProcedureReliabilityRecord(
            procedure_id=procedure_id,
            updated_revision=self.state.revision,
        )
        records.append(item)
        return len(records) - 1, item

    def _index(self, procedure_id: str) -> int:
        for index, item in enumerate(self.state.records):
            if item.procedure_id == procedure_id:
                return index
        raise KeyError(f"unknown procedure reliability record {procedure_id}")

    def _get(self, procedure_id: str) -> ProcedureReliabilityRecord | None:
        for item in self.state.records:
            if item.procedure_id == procedure_id:
                return item
        return None

    def _append_usage(self, evidence: ProcedureUsageEvidence) -> None:
        with self.usage_path.open("ab") as handle:
            handle.write(_canonical(evidence.model_dump(mode="json")) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _usage_rows(self) -> tuple[ProcedureUsageEvidence, ...]:
        if not self.usage_path.exists():
            return ()
        rows: list[ProcedureUsageEvidence] = []
        with self.usage_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                raw = json.loads(line)
                stored = str(raw.get("fingerprint", ""))
                evidence = ProcedureUsageEvidence.model_validate(raw)
                if stored != evidence.fingerprint:
                    raise ValueError("procedure reliability usage fingerprint mismatch")
                if evidence.project_id != self.state.project_id:
                    raise ValueError("procedure reliability usage belongs to a different project")
                rows.append(evidence)
        return tuple(rows)

    def _find_usage(self, procedure_id: str, episode_id: str) -> ProcedureUsageEvidence | None:
        matches = [
            item
            for item in self._usage_rows()
            if item.procedure_id == procedure_id and item.episode_id == episode_id
        ]
        if len(matches) > 1:
            raise ValueError("duplicate procedure reliability usage evidence for one task episode")
        return matches[0] if matches else None

    def _count_usage_matches(self, procedure_id: str, episode_id: str) -> int:
        return sum(
            1
            for item in self._usage_rows()
            if item.procedure_id == procedure_id and item.episode_id == episode_id
        )

    def _usage_ledger_count(self) -> int:
        return len(self._usage_rows())

    def _replace(
        self,
        records: list[ProcedureReliabilityRecord],
        *,
        revision: int,
        usage_total: int,
    ) -> None:
        self.state = ProcedureReliabilityState.model_validate(
            {
                **self.state.model_dump(
                    mode="python",
                    exclude={"fingerprint", "revision", "records", "usage_total"},
                ),
                "revision": revision,
                "records": tuple(records),
                "usage_total": usage_total,
            }
        )
        self._write_state()

    def _load_state(self) -> ProcedureReliabilityState:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load procedure reliability state: {exc}") from exc
        stored = str(raw.get("fingerprint", ""))
        state = ProcedureReliabilityState.model_validate(raw)
        if stored != state.fingerprint:
            raise ValueError("procedure reliability state fingerprint mismatch")
        return state

    def _reconcile_usage_total(self) -> None:
        count = self._usage_ledger_count()
        if count == 0 and not self.usage_path.exists():
            if self.state.usage_total != 0:
                raise ValueError("procedure reliability usage ledger is missing")
            return
        if count < self.state.usage_total:
            raise ValueError("procedure reliability usage ledger is shorter than committed state")
        if count == self.state.usage_total:
            return
        # Append-first crash recovery: expose the durable evidence count immediately. A caller
        # retry of record_usage for the same procedure/episode finds that existing evidence and
        # applies the missing lifecycle transition without appending a duplicate row.
        self.state = ProcedureReliabilityState.model_validate(
            {
                **self.state.model_dump(
                    mode="python", exclude={"fingerprint", "revision", "usage_total"}
                ),
                "revision": self.state.revision + 1,
                "usage_total": count,
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
