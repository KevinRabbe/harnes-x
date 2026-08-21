"""Durable, bounded long-horizon task state for autonomous coding.

The active state is intentionally small and structured. Full bounded evidence records are
append-only on disk and can be recalled on demand, so model context does not grow with the
raw trajectory length.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from harness_x.tools import ToolResult


_STATE_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".harness-x",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".next",
        ".turbo",
        "dist",
        "build",
        "coverage",
    }
)
_MAX_ACTIVE_EVIDENCE = 256


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


def _bounded_json(value: object, *, max_chars: int = 3000) -> object:
    """Return JSON-compatible evidence bounded without hiding that it was reduced."""

    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        serialized = str(value)
    if len(serialized) <= max_chars:
        try:
            return json.loads(serialized)
        except Exception:
            return serialized
    return {
        "truncated": True,
        "excerpt": serialized[:max_chars],
        "original_chars": len(serialized),
    }


def workspace_content_fingerprint(root: str | Path, *, max_files: int = 20000) -> str:
    """Exact source-relevant workspace fingerprint used by M27 resume checkpoints."""

    workspace = Path(root).resolve()
    if not workspace.is_dir():
        raise ValueError("long-horizon workspace must be an existing directory")
    rows: list[tuple[str, str, int]] = []
    count = 0
    for current, dirs, names in os.walk(workspace):
        dirs[:] = sorted(name for name in dirs if name not in _STATE_IGNORED_DIRS)
        base = Path(current)
        for name in sorted(names):
            path = base / name
            relative = path.relative_to(workspace)
            if any(part in _STATE_IGNORED_DIRS for part in relative.parts):
                continue
            count += 1
            if count > max_files:
                raise RuntimeError(
                    f"long-horizon workspace exceeds exact fingerprint limit of {max_files} files"
                )
            if path.is_symlink():
                payload = os.readlink(path).encode("utf-8", errors="surrogateescape")
                rows.append(
                    (
                        relative.as_posix(),
                        _sha256(b"symlink\0" + payload),
                        len(payload),
                    )
                )
                continue
            if not path.is_file():
                continue
            digest = hashlib.sha256()
            size = 0
            try:
                with path.open("rb") as handle:
                    while True:
                        block = handle.read(1024 * 1024)
                        if not block:
                            break
                        size += len(block)
                        digest.update(block)
            except OSError as exc:
                raise RuntimeError(
                    f"cannot fingerprint long-horizon file {relative.as_posix()}: {exc}"
                ) from exc
            rows.append((relative.as_posix(), digest.hexdigest(), size))
    return _sha256(_canonical(rows))


class LongHorizonObligationStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


class LongHorizonDecisionStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class LongHorizonStrategy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    current_focus: str = Field(default="", max_length=1200)
    next_actions: tuple[str, ...] = Field(default=(), max_length=12)
    risks: tuple[str, ...] = Field(default=(), max_length=12)
    updated_revision: int = Field(default=0, ge=0)

    @field_validator("next_actions", "risks")
    @classmethod
    def bound_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip()[:800] for item in values if item.strip())
        if len(normalized) != len(set(normalized)):
            raise ValueError("long-horizon strategy items must be unique")
        return normalized


class LongHorizonObligation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    obligation_id: str
    text: str = Field(min_length=1, max_length=1600)
    rationale: str = Field(default="", max_length=1600)
    priority: float = Field(default=0.7, ge=0.0, le=1.0)
    status: LongHorizonObligationStatus = LongHorizonObligationStatus.OPEN
    source: str = Field(default="model_proposal", min_length=1, max_length=120)
    created_revision: int = Field(ge=1)
    updated_revision: int = Field(ge=1)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=24)


class LongHorizonDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    statement: str = Field(min_length=1, max_length=1800)
    rationale: str = Field(default="", max_length=1800)
    status: LongHorizonDecisionStatus = LongHorizonDecisionStatus.ACTIVE
    source: str = Field(default="model_proposal", min_length=1, max_length=120)
    created_revision: int = Field(ge=1)
    updated_revision: int = Field(ge=1)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=24)
    supersedes: tuple[str, ...] = Field(default=(), max_length=12)


class LongHorizonEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    kind: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=2400)
    source_ref: str = Field(min_length=1, max_length=500)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    success: bool | None = None
    created_revision: int = Field(ge=1)
    metadata: object = Field(default_factory=dict)
    fingerprint: str = Field(min_length=64, max_length=64)


class LongHorizonCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    checkpoint_id: str
    revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=300)
    state_fingerprint: str = Field(min_length=64, max_length=64)
    workspace_fingerprint: str = Field(min_length=64, max_length=64)
    workspace_root: str
    evidence_total: int = Field(ge=0)


class LongHorizonTaskState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["long-horizon-task-state-v1"] = "long-horizon-task-state-v1"
    session_id: str
    task: str = Field(min_length=1, max_length=12000)
    acceptance_requirements: tuple[str, ...] = Field(default=(), max_length=128)
    revision: int = Field(ge=1)
    strategy: LongHorizonStrategy = Field(default_factory=LongHorizonStrategy)
    obligations: tuple[LongHorizonObligation, ...] = ()
    decisions: tuple[LongHorizonDecision, ...] = ()
    evidence_index: tuple[LongHorizonEvidence, ...] = ()
    evidence_total: int = Field(default=0, ge=0)
    evidence_rollups: dict[str, int] = Field(default_factory=dict)
    next_obligation_index: int = Field(default=1, ge=1)
    next_decision_index: int = Field(default=1, ge=1)
    next_evidence_index: int = Field(default=1, ge=1)
    checkpoint_count: int = Field(default=0, ge=0)
    latest_checkpoint: LongHorizonCheckpoint | None = None
    resumed: bool = False
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "LongHorizonTaskState":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", _sha256(_canonical(material)))
        return self


class ProposedLongHorizonObligation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=1600)
    rationale: str = Field(default="", max_length=1600)
    priority: float = Field(default=0.7, ge=0.0, le=1.0)


class ProposedLongHorizonDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str = Field(min_length=1, max_length=1800)
    rationale: str = Field(default="", max_length=1800)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=24)
    supersedes: tuple[str, ...] = Field(default=(), max_length=12)


class ProposedLongHorizonStrategy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    current_focus: str = Field(default="", max_length=1200)
    next_actions: tuple[str, ...] = Field(default=(), max_length=12)
    risks: tuple[str, ...] = Field(default=(), max_length=12)


class LongHorizonStateUpdateProposal(BaseModel):
    """Advisory model proposal; cannot rewrite task or acceptance authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["long_horizon_state_update"] = "long_horizon_state_update"
    strategy: ProposedLongHorizonStrategy | None = None
    add_obligations: tuple[ProposedLongHorizonObligation, ...] = Field(
        default=(), max_length=8
    )
    resolve_obligation_ids: tuple[str, ...] = Field(default=(), max_length=16)
    supersede_obligation_ids: tuple[str, ...] = Field(default=(), max_length=16)
    decisions: tuple[ProposedLongHorizonDecision, ...] = Field(default=(), max_length=8)
    supersede_decision_ids: tuple[str, ...] = Field(default=(), max_length=16)
    checkpoint_reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def require_change(self) -> "LongHorizonStateUpdateProposal":
        if not any(
            (
                self.strategy is not None,
                self.add_obligations,
                self.resolve_obligation_ids,
                self.supersede_obligation_ids,
                self.decisions,
                self.supersede_decision_ids,
                self.checkpoint_reason,
            )
        ):
            raise ValueError("long-horizon state update must contain at least one change")
        return self


class LongHorizonRecallMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    kind: str
    summary: str
    source_ref: str
    importance: float
    success: bool | None = None
    created_revision: int
    metadata: object = Field(default_factory=dict)


class LongHorizonStateStore:
    """Atomic active state plus append-only bounded evidence records."""

    def __init__(
        self,
        artifact_root: str | Path,
        workspace_root: str | Path,
        *,
        resume_state_path: str | Path | None = None,
        require_resume_workspace_match: bool = True,
    ) -> None:
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.workspace_root = Path(workspace_root).resolve()
        if not self.workspace_root.is_dir():
            raise ValueError("long-horizon workspace must be an existing directory")
        self.state_path = (
            Path(resume_state_path).resolve()
            if resume_state_path is not None
            else self.artifact_root / "long-horizon-state.json"
        )
        self.evidence_path = self.state_path.with_name("long-horizon-evidence.jsonl")
        self.checkpoint_root = self.state_path.with_name("long-horizon-checkpoints")
        self.checkpoint_root.mkdir(parents=True, exist_ok=True)
        self.require_resume_workspace_match = require_resume_workspace_match
        self.state: LongHorizonTaskState | None = None
        self._resume_requested = resume_state_path is not None
        if self._resume_requested:
            self.state = self._load_state(self.state_path).model_copy(update={"resumed": True})

    @property
    def initialized(self) -> bool:
        return self.state is not None

    def initialize(
        self,
        *,
        task: str,
        acceptance_requirements: tuple[str, ...],
    ) -> LongHorizonTaskState:
        normalized_task = task.strip()
        if not normalized_task:
            raise ValueError("long-horizon task cannot be blank")
        normalized_acceptance = tuple(
            dict.fromkeys(item.strip() for item in acceptance_requirements if item.strip())
        )
        if self.state is None:
            state = LongHorizonTaskState(
                session_id=f"longtask_{uuid.uuid4().hex[:20]}",
                task=normalized_task,
                acceptance_requirements=normalized_acceptance,
                revision=1,
            )
            self.state = state
            self._write_state()
            return state

        state = self.state
        if state.task != normalized_task:
            raise ValueError("resume state task does not match the requested coding task")
        if state.acceptance_requirements != normalized_acceptance:
            raise ValueError("resume state acceptance requirements changed")
        checkpoint = state.latest_checkpoint
        if self.require_resume_workspace_match and checkpoint is not None:
            current = workspace_content_fingerprint(self.workspace_root)
            if current != checkpoint.workspace_fingerprint:
                raise ValueError(
                    "resume workspace fingerprint does not match the latest long-horizon checkpoint"
                )
        self._write_state()
        return state

    def apply_model_update(
        self,
        update: LongHorizonStateUpdateProposal,
    ) -> LongHorizonTaskState:
        state = self._require_state()
        next_revision = state.revision + 1
        obligations = list(state.obligations)
        decisions = list(state.decisions)
        next_obligation = state.next_obligation_index
        next_decision = state.next_decision_index

        by_obligation = {item.obligation_id: index for index, item in enumerate(obligations)}
        for obligation_id in update.resolve_obligation_ids:
            if obligation_id not in by_obligation:
                raise ValueError(f"unknown obligation ID {obligation_id}")
            index = by_obligation[obligation_id]
            current = obligations[index]
            if current.status != LongHorizonObligationStatus.OPEN:
                raise ValueError(f"obligation {obligation_id} is not open")
            obligations[index] = current.model_copy(
                update={
                    "status": LongHorizonObligationStatus.RESOLVED,
                    "updated_revision": next_revision,
                }
            )
        for obligation_id in update.supersede_obligation_ids:
            if obligation_id not in by_obligation:
                raise ValueError(f"unknown obligation ID {obligation_id}")
            index = by_obligation[obligation_id]
            current = obligations[index]
            if current.status != LongHorizonObligationStatus.OPEN:
                raise ValueError(f"obligation {obligation_id} is not open")
            obligations[index] = current.model_copy(
                update={
                    "status": LongHorizonObligationStatus.SUPERSEDED,
                    "updated_revision": next_revision,
                }
            )
        for proposed in update.add_obligations:
            obligation_id = f"obl_{next_obligation:06d}"
            next_obligation += 1
            obligations.append(
                LongHorizonObligation(
                    obligation_id=obligation_id,
                    text=proposed.text.strip(),
                    rationale=proposed.rationale.strip(),
                    priority=proposed.priority,
                    created_revision=next_revision,
                    updated_revision=next_revision,
                )
            )

        by_decision = {item.decision_id: index for index, item in enumerate(decisions)}
        for decision_id in update.supersede_decision_ids:
            if decision_id not in by_decision:
                raise ValueError(f"unknown decision ID {decision_id}")
            index = by_decision[decision_id]
            current = decisions[index]
            if current.status != LongHorizonDecisionStatus.ACTIVE:
                raise ValueError(f"decision {decision_id} is not active")
            decisions[index] = current.model_copy(
                update={
                    "status": LongHorizonDecisionStatus.SUPERSEDED,
                    "updated_revision": next_revision,
                }
            )
        for proposed in update.decisions:
            for superseded in proposed.supersedes:
                if superseded not in by_decision:
                    raise ValueError(f"unknown superseded decision ID {superseded}")
            decision_id = f"decision_{next_decision:06d}"
            next_decision += 1
            decisions.append(
                LongHorizonDecision(
                    decision_id=decision_id,
                    statement=proposed.statement.strip(),
                    rationale=proposed.rationale.strip(),
                    evidence_refs=proposed.evidence_refs,
                    supersedes=proposed.supersedes,
                    created_revision=next_revision,
                    updated_revision=next_revision,
                )
            )
            for superseded in proposed.supersedes:
                index = by_decision[superseded]
                current = decisions[index]
                if current.status == LongHorizonDecisionStatus.ACTIVE:
                    decisions[index] = current.model_copy(
                        update={
                            "status": LongHorizonDecisionStatus.SUPERSEDED,
                            "updated_revision": next_revision,
                        }
                    )

        strategy = state.strategy
        if update.strategy is not None:
            strategy = LongHorizonStrategy(
                current_focus=update.strategy.current_focus.strip(),
                next_actions=update.strategy.next_actions,
                risks=update.strategy.risks,
                updated_revision=next_revision,
            )
        updated = self._replace_state(
            revision=next_revision,
            strategy=strategy,
            obligations=tuple(obligations),
            decisions=tuple(decisions),
            next_obligation_index=next_obligation,
            next_decision_index=next_decision,
        )
        if update.checkpoint_reason:
            self.checkpoint(update.checkpoint_reason)
            updated = self._require_state()
        return updated

    def record_tool_result(self, result: ToolResult) -> LongHorizonEvidence:
        output = dict(result.output)
        tool = result.tool_name
        summary_parts = [f"{tool}: {result.status.value}"]
        importance = 0.55
        success: bool | None = result.succeeded
        metadata: dict[str, Any] = {
            "tool_version": result.tool_version,
            "duration_ms": result.duration_ms,
            "execution_may_continue": result.execution_may_continue,
        }
        if result.error:
            summary_parts.append(result.error[:1200])
            importance = 0.9
        if tool in {"workspace_write", "workspace_patch"}:
            path = output.get("path")
            summary_parts.append(f"workspace mutation {path}")
            metadata["path"] = path
            metadata["sha256_before"] = output.get("sha256_before")
            metadata["sha256_after"] = output.get("sha256_after")
            importance = 0.9
        elif tool == "process_run":
            argv = output.get("argv", ())
            returncode = output.get("returncode")
            summary_parts.append(f"argv={argv} returncode={returncode}")
            metadata.update(
                {
                    "argv": argv,
                    "returncode": returncode,
                    "stdout_tail": str(output.get("stdout", ""))[-1200:],
                    "stderr_tail": str(output.get("stderr", ""))[-1200:],
                    "output_truncated": output.get("output_truncated", False),
                }
            )
            importance = 0.88 if returncode not in {0, None} else 0.72
        elif tool.startswith("browser_"):
            summary_parts.append(
                f"url={output.get('url', '')} title={output.get('title', '')}"
            )
            metadata.update(
                {
                    "url": output.get("url"),
                    "title": output.get("title"),
                    "aria_truncated": output.get("aria_truncated", False),
                    "console_truncated": output.get("console_truncated", False),
                    "page_errors_truncated": output.get("page_errors_truncated", False),
                }
            )
            importance = 0.68
        else:
            for key in ("path", "query", "name", "fingerprint"):
                if key in output:
                    metadata[key] = output[key]
            if "path" in output:
                summary_parts.append(f"path={output['path']}")
        return self.record_evidence(
            kind=f"tool:{tool}",
            summary="; ".join(str(item) for item in summary_parts)[:2400],
            source_ref=f"tool:{result.candidate_id}",
            importance=importance,
            success=success,
            metadata=metadata,
        )

    def record_evidence(
        self,
        *,
        kind: str,
        summary: str,
        source_ref: str,
        importance: float,
        success: bool | None = None,
        metadata: object = None,
    ) -> LongHorizonEvidence:
        state = self._require_state()
        next_revision = state.revision + 1
        evidence_id = f"evidence_{state.next_evidence_index:08d}"
        bounded_metadata = _bounded_json({} if metadata is None else metadata)
        fingerprint_material = {
            "kind": kind,
            "summary": summary,
            "source_ref": source_ref,
            "success": success,
            "metadata": bounded_metadata,
        }
        evidence = LongHorizonEvidence(
            evidence_id=evidence_id,
            kind=kind.strip(),
            summary=summary.strip()[:2400],
            source_ref=source_ref.strip()[:500],
            importance=importance,
            success=success,
            created_revision=next_revision,
            metadata=bounded_metadata,
            fingerprint=_sha256(_canonical(fingerprint_material)),
        )
        self._append_evidence(evidence)
        active = self._trim_active_evidence((*state.evidence_index, evidence))
        rollups = dict(state.evidence_rollups)
        rollups[evidence.kind] = rollups.get(evidence.kind, 0) + 1
        self._replace_state(
            revision=next_revision,
            evidence_index=active,
            evidence_total=state.evidence_total + 1,
            evidence_rollups=rollups,
            next_evidence_index=state.next_evidence_index + 1,
        )
        return evidence

    def record_control_intervention(
        self,
        *,
        phase: str,
        reason: str,
        intervention_kind: str,
        reasoning_step: int,
    ) -> LongHorizonEvidence:
        return self.record_evidence(
            kind="control_intervention",
            summary=(
                f"controller intervention {intervention_kind} in phase {phase}: {reason}"
            ),
            source_ref=f"controller:reasoning-step:{reasoning_step}",
            importance=0.84,
            success=None,
            metadata={
                "phase": phase,
                "reason": reason,
                "intervention_kind": intervention_kind,
                "reasoning_step": reasoning_step,
            },
        )

    def checkpoint(self, reason: str) -> LongHorizonCheckpoint:
        state = self._require_state()
        normalized = reason.strip()
        if not normalized:
            raise ValueError("checkpoint reason cannot be blank")
        workspace_fingerprint = workspace_content_fingerprint(self.workspace_root)
        checkpoint_id = f"checkpoint_{state.checkpoint_count + 1:05d}"
        checkpoint = LongHorizonCheckpoint(
            checkpoint_id=checkpoint_id,
            revision=state.revision + 1,
            reason=normalized,
            state_fingerprint=state.fingerprint,
            workspace_fingerprint=workspace_fingerprint,
            workspace_root=str(self.workspace_root),
            evidence_total=state.evidence_total,
        )
        updated = self._replace_state(
            revision=state.revision + 1,
            checkpoint_count=state.checkpoint_count + 1,
            latest_checkpoint=checkpoint,
        )
        snapshot = self.checkpoint_root / f"{checkpoint_id}.json"
        self._atomic_json(snapshot, updated.model_dump(mode="json"))
        return checkpoint

    def recall(
        self,
        *,
        query: str = "",
        kinds: tuple[str, ...] = (),
        limit: int = 20,
    ) -> tuple[LongHorizonRecallMatch, ...]:
        normalized_query = query.strip().casefold()
        normalized_kinds = {item.strip() for item in kinds if item.strip()}
        if not normalized_query and not normalized_kinds:
            raise ValueError("long-horizon recall requires a query or evidence kind")
        if limit < 1 or limit > 50:
            raise ValueError("long-horizon recall limit must be between 1 and 50")
        if not self.evidence_path.exists():
            return ()
        matches: list[LongHorizonEvidence] = []
        with self.evidence_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    item = LongHorizonEvidence.model_validate_json(raw)
                except Exception:
                    continue
                if normalized_kinds and item.kind not in normalized_kinds:
                    continue
                searchable = (
                    item.summary
                    + "\n"
                    + item.source_ref
                    + "\n"
                    + json.dumps(item.metadata, ensure_ascii=False, default=str)
                ).casefold()
                if normalized_query and normalized_query not in searchable:
                    continue
                matches.append(item)
        matches.sort(
            key=lambda item: (item.importance, item.created_revision, item.evidence_id),
            reverse=True,
        )
        return tuple(
            LongHorizonRecallMatch(
                evidence_id=item.evidence_id,
                kind=item.kind,
                summary=item.summary,
                source_ref=item.source_ref,
                importance=item.importance,
                success=item.success,
                created_revision=item.created_revision,
                metadata=item.metadata,
            )
            for item in matches[:limit]
        )

    def context_projection(
        self,
        *,
        max_open_obligations: int = 24,
        max_decisions: int = 16,
        max_evidence: int = 18,
    ) -> dict[str, object]:
        if self.state is None:
            return {"configured": False}
        state = self.state
        obligations = sorted(
            (
                item
                for item in state.obligations
                if item.status == LongHorizonObligationStatus.OPEN
            ),
            key=lambda item: (item.priority, item.updated_revision, item.obligation_id),
            reverse=True,
        )[:max_open_obligations]
        decisions = sorted(
            (
                item
                for item in state.decisions
                if item.status == LongHorizonDecisionStatus.ACTIVE
            ),
            key=lambda item: (item.updated_revision, item.decision_id),
            reverse=True,
        )[:max_decisions]
        evidence = sorted(
            state.evidence_index,
            key=lambda item: (item.importance, item.created_revision, item.evidence_id),
            reverse=True,
        )[:max_evidence]
        return {
            "configured": True,
            "schema_version": state.schema_version,
            "session_id": state.session_id,
            "revision": state.revision,
            "fingerprint": state.fingerprint,
            "resumed": state.resumed,
            "immutable_task": state.task,
            "immutable_acceptance_requirements": list(state.acceptance_requirements),
            "strategy": state.strategy.model_dump(mode="json"),
            "open_obligations": [item.model_dump(mode="json") for item in obligations],
            "active_decisions": [item.model_dump(mode="json") for item in decisions],
            "selected_evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "kind": item.kind,
                    "summary": item.summary,
                    "source_ref": item.source_ref,
                    "importance": item.importance,
                    "success": item.success,
                    "created_revision": item.created_revision,
                }
                for item in evidence
            ],
            "counts": {
                "obligations_total": len(state.obligations),
                "obligations_open": sum(
                    item.status == LongHorizonObligationStatus.OPEN
                    for item in state.obligations
                ),
                "decisions_total": len(state.decisions),
                "decisions_active": sum(
                    item.status == LongHorizonDecisionStatus.ACTIVE
                    for item in state.decisions
                ),
                "evidence_total": state.evidence_total,
                "active_evidence_index": len(state.evidence_index),
                "checkpoint_count": state.checkpoint_count,
            },
            "evidence_rollups": dict(sorted(state.evidence_rollups.items())),
            "latest_checkpoint": (
                None
                if state.latest_checkpoint is None
                else state.latest_checkpoint.model_dump(mode="json")
            ),
            "recall_rule": (
                "The selected evidence is a bounded active index, not the full history. "
                "Use task_state_recall when an older fact, failure, file, command, or decision "
                "may matter."
            ),
        }

    def _trim_active_evidence(
        self, evidence: tuple[LongHorizonEvidence, ...]
    ) -> tuple[LongHorizonEvidence, ...]:
        if len(evidence) <= _MAX_ACTIVE_EVIDENCE:
            return evidence
        by_importance = sorted(
            evidence,
            key=lambda item: (item.importance, item.created_revision, item.evidence_id),
            reverse=True,
        )[:128]
        recent = sorted(
            evidence,
            key=lambda item: (item.created_revision, item.evidence_id),
            reverse=True,
        )[:160]
        selected = {item.evidence_id: item for item in (*by_importance, *recent)}
        rows = sorted(
            selected.values(),
            key=lambda item: (item.created_revision, item.evidence_id),
        )
        return tuple(rows[-_MAX_ACTIVE_EVIDENCE:])

    def _append_evidence(self, evidence: LongHorizonEvidence) -> None:
        self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
        with self.evidence_path.open("a", encoding="utf-8") as handle:
            handle.write(evidence.model_dump_json() + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass

    def _replace_state(self, **updates: object) -> LongHorizonTaskState:
        current = self._require_state()
        payload = current.model_dump(mode="python", exclude={"fingerprint"})
        payload.update(updates)
        payload["fingerprint"] = ""
        state = LongHorizonTaskState.model_validate(payload)
        self.state = state
        self._write_state()
        return state

    def _write_state(self) -> None:
        state = self._require_state()
        self._atomic_json(self.state_path, state.model_dump(mode="json"))

    @staticmethod
    def _atomic_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _load_state(path: Path) -> LongHorizonTaskState:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"cannot read long-horizon state {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid long-horizon state JSON {path}: {exc}") from exc
        stored = str(payload.get("fingerprint", ""))
        state = LongHorizonTaskState.model_validate(payload)
        if not stored or stored != state.fingerprint:
            raise ValueError("long-horizon state fingerprint verification failed")
        return state

    def _require_state(self) -> LongHorizonTaskState:
        if self.state is None:
            raise RuntimeError("long-horizon state is not initialized")
        return self.state
