"""Durable project-scoped memory and evidence-gated procedural reuse.

M27 preserves one long-running task. M28 preserves reusable repository/project knowledge
across independent tasks. Model proposals are never project truth by themselves: candidates
are admitted only from software-verified successful task episodes, and reusable entries are
activated only after support from at least two distinct successful episodes.
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


_MIN_SUPPORT_FOR_ACTIVE = 2
_MAX_ENTRIES = 512
_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")


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


def _tokens(value: str) -> frozenset[str]:
    return frozenset(item.casefold() for item in _TOKEN_RE.findall(value) if len(item) > 1)


def _identity_text(value: str) -> str:
    """Canonicalize presentation-only whitespace/case for candidate identity."""

    return " ".join(value.split()).casefold()


def _normalized_unique(values: tuple[str, ...], *, max_chars: int = 1200) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = raw.strip()[:max_chars]
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


class ProjectMemoryEntryKind(StrEnum):
    FACT = "fact"
    PROCEDURE = "procedure"


class ProjectMemoryEntryState(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    CONFLICTED = "conflicted"
    INVALIDATED = "invalidated"


class ProjectMemoryTaskEpisode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["project-memory-episode-v1"] = "project-memory-episode-v1"
    episode_id: str
    task: str = Field(min_length=1, max_length=12000)
    succeeded: bool
    source_ref: str = Field(min_length=1, max_length=1000)
    long_horizon_session_id: str | None = None
    long_horizon_state_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    workspace_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    changed_files: tuple[str, ...] = Field(default=(), max_length=256)
    verification_refs: tuple[str, ...] = Field(default=(), max_length=128)
    created_at: datetime


class ProjectMemoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str
    kind: ProjectMemoryEntryKind
    key: str = Field(min_length=1, max_length=180)
    statement: str = Field(min_length=1, max_length=3000)
    steps: tuple[str, ...] = Field(default=(), max_length=24)
    task_categories: tuple[str, ...] = Field(default=(), max_length=24)
    state: ProjectMemoryEntryState = ProjectMemoryEntryState.CANDIDATE
    support_episode_ids: tuple[str, ...] = Field(default=(), max_length=128)
    conflicts_with: tuple[str, ...] = Field(default=(), max_length=64)
    usage_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    known_failure_modes: tuple[str, ...] = Field(default=(), max_length=32)
    created_revision: int = Field(ge=1)
    updated_revision: int = Field(ge=1)
    content_fingerprint: str = Field(min_length=64, max_length=64)

    @field_validator("key", "statement")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("project memory text cannot be blank")
        return value

    @field_validator("steps")
    @classmethod
    def normalize_steps(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalized_unique(value, max_chars=1600)

    @field_validator("task_categories", "known_failure_modes")
    @classmethod
    def normalize_lists(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalized_unique(value, max_chars=800)

    @property
    def support_count(self) -> int:
        return len(self.support_episode_ids)

    @property
    def success_rate(self) -> float | None:
        return None if self.usage_count == 0 else self.success_count / self.usage_count


class ProjectMemoryState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["project-memory-state-v1"] = "project-memory-state-v1"
    project_id: str
    project_key: str = Field(min_length=1, max_length=2000)
    revision: int = Field(ge=1)
    entries: tuple[ProjectMemoryEntry, ...] = Field(default=(), max_length=_MAX_ENTRIES)
    episode_count: int = Field(default=0, ge=0)
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ProjectMemoryState":
        ids = [item.entry_id for item in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("project memory entry IDs must be unique")
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", _sha256(_canonical(material)))
        return self


class ProposedProjectFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["fact"] = "fact"
    key: str = Field(min_length=1, max_length=180)
    statement: str = Field(min_length=1, max_length=3000)
    task_categories: tuple[str, ...] = Field(default=(), max_length=24)


class ProposedProjectProcedure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["procedure"] = "procedure"
    key: str = Field(min_length=1, max_length=180)
    statement: str = Field(min_length=1, max_length=3000)
    steps: tuple[str, ...] = Field(min_length=1, max_length=24)
    task_categories: tuple[str, ...] = Field(default=(), max_length=24)


ProjectMemoryCandidate = ProposedProjectFact | ProposedProjectProcedure


class ProjectMemoryUpdateProposal(BaseModel):
    """Model advisory proposal; software admits candidates only after task verification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["project_memory_update"] = "project_memory_update"
    candidates: tuple[ProjectMemoryCandidate, ...] = Field(default=(), max_length=8)
    used_procedure_ids: tuple[str, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def require_change(self) -> "ProjectMemoryUpdateProposal":
        if not self.candidates and not self.used_procedure_ids:
            raise ValueError("project-memory update must contain candidates or used procedures")
        return self


class ProjectMemoryRecallRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    entry_id: str
    kind: ProjectMemoryEntryKind
    key: str
    statement: str
    steps: tuple[str, ...] = ()
    task_categories: tuple[str, ...] = ()
    state: ProjectMemoryEntryState
    support_count: int = Field(ge=0)
    usage_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    known_failure_modes: tuple[str, ...] = ()


class ProjectMemoryStore:
    """Atomic project state plus append-only verified task episodes."""

    def __init__(self, root: str | Path, *, project_key: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "project-memory.json"
        self.episodes_path = self.root / "project-memory-episodes.jsonl"
        normalized_key = project_key.strip()
        if not normalized_key:
            raise ValueError("project memory requires a non-blank project key")
        self.project_key = normalized_key
        self.project_id = "project_" + _sha256(normalized_key.encode("utf-8"))[:24]
        if self.state_path.exists():
            self.state = self._load_state()
            if self.state.project_key != self.project_key or self.state.project_id != self.project_id:
                raise ValueError("project memory root belongs to a different project identity")
            self._reconcile_episode_count()
        else:
            self.state = ProjectMemoryState(
                project_id=self.project_id,
                project_key=self.project_key,
                revision=1,
            )
            self._write_state()

    def record_episode(
        self,
        *,
        task: str,
        succeeded: bool,
        source_ref: str,
        long_horizon_session_id: str | None = None,
        long_horizon_state_fingerprint: str | None = None,
        workspace_fingerprint: str | None = None,
        changed_files: tuple[str, ...] = (),
        verification_refs: tuple[str, ...] = (),
    ) -> ProjectMemoryTaskEpisode:
        episode = ProjectMemoryTaskEpisode(
            episode_id=f"pepisode_{uuid.uuid4().hex}",
            task=task.strip(),
            succeeded=succeeded,
            source_ref=source_ref.strip(),
            long_horizon_session_id=long_horizon_session_id,
            long_horizon_state_fingerprint=long_horizon_state_fingerprint,
            workspace_fingerprint=workspace_fingerprint,
            changed_files=_normalized_unique(changed_files, max_chars=1000),
            verification_refs=_normalized_unique(verification_refs, max_chars=1000),
            created_at=datetime.now(timezone.utc),
        )
        with self.episodes_path.open("ab") as handle:
            handle.write(_canonical(episode.model_dump(mode="json")) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.state = ProjectMemoryState.model_validate(
            {
                **self.state.model_dump(mode="python", exclude={"fingerprint"}),
                "revision": self.state.revision + 1,
                "episode_count": self.state.episode_count + 1,
            }
        )
        self._write_state()
        return episode

    def support_candidates(
        self,
        episode: ProjectMemoryTaskEpisode,
        candidates: tuple[ProjectMemoryCandidate, ...],
    ) -> tuple[ProjectMemoryEntry, ...]:
        if not episode.succeeded:
            raise ValueError("project memory candidates require a verified successful episode")
        admitted: list[ProjectMemoryEntry] = []
        seen: set[tuple[str, str, str]] = set()
        for candidate in candidates:
            kind = ProjectMemoryEntryKind(candidate.kind)
            key = candidate.key.strip()
            statement = candidate.statement.strip()
            steps = (
                _normalized_unique(candidate.steps, max_chars=1600)
                if isinstance(candidate, ProposedProjectProcedure)
                else ()
            )
            categories = _normalized_unique(candidate.task_categories, max_chars=800)
            if kind == ProjectMemoryEntryKind.PROCEDURE and not steps:
                raise ValueError("project procedure requires at least one step")
            content_fingerprint = self._content_fingerprint(
                kind=kind,
                key=key,
                statement=statement,
                steps=steps,
                task_categories=categories,
            )
            candidate_key = (kind.value, key.casefold(), content_fingerprint)
            if candidate_key in seen:
                continue
            seen.add(candidate_key)
            admitted.append(
                self._support_one(
                    episode,
                    kind=kind,
                    key=key,
                    statement=statement,
                    steps=steps,
                    task_categories=categories,
                    content_fingerprint=content_fingerprint,
                )
            )
        return tuple(admitted)

    def record_procedure_usage(
        self,
        entry_id: str,
        *,
        success: bool,
        failure_mode: str | None = None,
    ) -> ProjectMemoryEntry:
        entries = list(self.state.entries)
        index = self._index(entry_id)
        current = entries[index]
        if current.kind != ProjectMemoryEntryKind.PROCEDURE:
            raise ValueError("only project procedures can record procedure usage")
        if current.state != ProjectMemoryEntryState.ACTIVE or current.conflicts_with:
            raise ValueError("only unconflicted active project procedures can be used")
        failure = failure_mode.strip()[:1200] if failure_mode else None
        if not success and not failure:
            failure = "task_failed_after_declared_project_procedure_usage"
        next_revision = self.state.revision + 1
        entries[index] = current.model_copy(
            update={
                "usage_count": current.usage_count + 1,
                "success_count": current.success_count + int(success),
                "failure_count": current.failure_count + int(not success),
                "known_failure_modes": _normalized_unique(
                    (*current.known_failure_modes, *((failure,) if failure else ())),
                    max_chars=1200,
                ),
                "updated_revision": next_revision,
            }
        )
        self._replace_entries(entries, revision=next_revision)
        return entries[index]

    def active_entries(self) -> tuple[ProjectMemoryEntry, ...]:
        return tuple(
            item
            for item in self.state.entries
            if item.state == ProjectMemoryEntryState.ACTIVE and not item.conflicts_with
        )

    def recall(
        self,
        *,
        query: str,
        kinds: tuple[str, ...] = (),
        include_candidates: bool = False,
        limit: int = 12,
    ) -> tuple[ProjectMemoryRecallRow, ...]:
        if limit < 1 or limit > 50:
            raise ValueError("project memory recall limit must be between 1 and 50")
        requested_kinds = {
            ProjectMemoryEntryKind(item.strip()) for item in kinds if item.strip()
        }
        query_tokens = _tokens(query)
        rows: list[tuple[tuple[float, int, int, str], ProjectMemoryEntry]] = []
        for item in self.state.entries:
            if requested_kinds and item.kind not in requested_kinds:
                continue
            if not include_candidates and (
                item.state != ProjectMemoryEntryState.ACTIVE or item.conflicts_with
            ):
                continue
            material = " ".join(
                (item.key, item.statement, *item.steps, *item.task_categories, *item.known_failure_modes)
            )
            overlap = len(query_tokens & _tokens(material)) if query_tokens else 0
            if query_tokens and overlap == 0:
                continue
            state_weight = 2 if item.state == ProjectMemoryEntryState.ACTIVE else 0
            score = (float(overlap), state_weight, item.support_count, item.entry_id)
            rows.append((score, item))
        rows.sort(key=lambda pair: pair[0], reverse=True)
        return tuple(self._recall_row(item) for _, item in rows[:limit])

    def context_projection(self, query: str, *, limit: int = 12) -> dict[str, object]:
        selected = self.recall(query=query, limit=limit)
        counts = {
            state.value: sum(1 for item in self.state.entries if item.state == state)
            for state in ProjectMemoryEntryState
        }
        return {
            "project_id": self.state.project_id,
            "revision": self.state.revision,
            "fingerprint": self.state.fingerprint,
            "episode_count": self.state.episode_count,
            "entry_counts": counts,
            "promotion_rule": (
                "Entries become reusable only after at least two distinct verified successful "
                "task episodes support identical content. Conflicting variants are suspended."
            ),
            "selected_active_memory": [item.model_dump(mode="json") for item in selected],
            "recall_rule": "Use project_memory_recall for additional active or candidate project memory.",
        }

    def _support_one(
        self,
        episode: ProjectMemoryTaskEpisode,
        *,
        kind: ProjectMemoryEntryKind,
        key: str,
        statement: str,
        steps: tuple[str, ...],
        task_categories: tuple[str, ...],
        content_fingerprint: str,
    ) -> ProjectMemoryEntry:
        entries = list(self.state.entries)
        same_identity = [
            (index, item)
            for index, item in enumerate(entries)
            if item.kind == kind
            and item.key.casefold() == key.casefold()
            and item.state != ProjectMemoryEntryState.INVALIDATED
        ]
        match = next(
            (
                (index, item)
                for index, item in same_identity
                if item.content_fingerprint == content_fingerprint
            ),
            None,
        )
        next_revision = self.state.revision + 1
        if match is None:
            if len(entries) >= _MAX_ENTRIES:
                raise RuntimeError("project memory entry capacity reached")
            item = ProjectMemoryEntry(
                entry_id=f"pmem_{uuid.uuid4().hex}",
                kind=kind,
                key=key,
                statement=statement,
                steps=steps,
                task_categories=task_categories,
                support_episode_ids=(episode.episode_id,),
                created_revision=next_revision,
                updated_revision=next_revision,
                content_fingerprint=content_fingerprint,
            )
            entries.append(item)
            index = len(entries) - 1
        else:
            index, current = match
            support = _normalized_unique((*current.support_episode_ids, episode.episode_id), max_chars=120)
            categories = _normalized_unique((*current.task_categories, *task_categories), max_chars=800)
            entries[index] = current.model_copy(
                update={
                    "support_episode_ids": support,
                    "task_categories": categories,
                    "updated_revision": next_revision,
                }
            )

        # Fail closed on contradictions. A new incompatible variant suspends every variant
        # under this kind/key until later software/operator resolution exists.
        identity_indexes = [
            idx
            for idx, candidate in enumerate(entries)
            if candidate.kind == kind
            and candidate.key.casefold() == key.casefold()
            and candidate.state != ProjectMemoryEntryState.INVALIDATED
        ]
        fingerprints = {entries[idx].content_fingerprint for idx in identity_indexes}
        if len(fingerprints) > 1:
            ids = {entries[idx].entry_id for idx in identity_indexes}
            for idx in identity_indexes:
                current = entries[idx]
                entries[idx] = current.model_copy(
                    update={
                        "state": ProjectMemoryEntryState.CONFLICTED,
                        "conflicts_with": tuple(sorted(ids - {current.entry_id})),
                        "updated_revision": next_revision,
                    }
                )
        else:
            for idx in identity_indexes:
                current = entries[idx]
                next_state = (
                    ProjectMemoryEntryState.ACTIVE
                    if current.support_count >= _MIN_SUPPORT_FOR_ACTIVE
                    else ProjectMemoryEntryState.CANDIDATE
                )
                entries[idx] = current.model_copy(
                    update={
                        "state": next_state,
                        "conflicts_with": (),
                        "updated_revision": next_revision,
                    }
                )

        self._replace_entries(entries, revision=next_revision)
        return self.state.entries[index]

    @staticmethod
    def _content_fingerprint(
        *,
        kind: ProjectMemoryEntryKind,
        key: str,
        statement: str,
        steps: tuple[str, ...],
        task_categories: tuple[str, ...],
    ) -> str:
        # task_categories are retrieval metadata, not semantic identity. Otherwise the same
        # verified procedure proposed under slightly different task labels would falsely
        # conflict with itself. Whitespace/case presentation differences are also normalized.
        del task_categories
        return _sha256(
            _canonical(
                {
                    "kind": kind.value,
                    "key": _identity_text(key),
                    "statement": _identity_text(statement),
                    "steps": tuple(_identity_text(item) for item in steps),
                }
            )
        )

    @staticmethod
    def _recall_row(item: ProjectMemoryEntry) -> ProjectMemoryRecallRow:
        return ProjectMemoryRecallRow(
            entry_id=item.entry_id,
            kind=item.kind,
            key=item.key,
            statement=item.statement,
            steps=item.steps,
            task_categories=item.task_categories,
            state=item.state,
            support_count=item.support_count,
            usage_count=item.usage_count,
            success_count=item.success_count,
            failure_count=item.failure_count,
            known_failure_modes=item.known_failure_modes,
        )

    def _index(self, entry_id: str) -> int:
        for index, item in enumerate(self.state.entries):
            if item.entry_id == entry_id:
                return index
        raise KeyError(f"unknown project memory entry {entry_id}")

    def _replace_entries(self, entries: list[ProjectMemoryEntry], *, revision: int) -> None:
        self.state = ProjectMemoryState.model_validate(
            {
                **self.state.model_dump(mode="python", exclude={"fingerprint", "entries", "revision"}),
                "revision": revision,
                "entries": tuple(entries),
            }
        )
        self._write_state()

    def _load_state(self) -> ProjectMemoryState:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load project memory state: {exc}") from exc
        stored_fingerprint = str(raw.get("fingerprint", ""))
        state = ProjectMemoryState.model_validate(raw)
        if stored_fingerprint != state.fingerprint:
            raise ValueError("project memory state fingerprint mismatch")
        return state

    def _reconcile_episode_count(self) -> None:
        if not self.episodes_path.exists():
            if self.state.episode_count != 0:
                raise ValueError("project memory episode ledger is missing")
            return
        count = 0
        with self.episodes_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                ProjectMemoryTaskEpisode.model_validate_json(line)
                count += 1
        if count < self.state.episode_count:
            raise ValueError("project memory episode ledger is shorter than committed state")
        if count == self.state.episode_count:
            return
        # Append-first crash recovery: episode rows can exist even when the state replacement
        # did not complete. Reconcile only the monotonic episode counter; candidate promotion
        # happens after the state update and is therefore safely retried by the caller.
        self.state = ProjectMemoryState.model_validate(
            {
                **self.state.model_dump(mode="python", exclude={"fingerprint", "revision", "episode_count"}),
                "revision": self.state.revision + 1,
                "episode_count": count,
            }
        )
        self._write_state()

    def _write_state(self) -> None:
        payload = (self.state.model_dump_json(indent=2) + "\n").encode("utf-8")
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.state_path)
