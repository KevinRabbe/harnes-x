"""Deterministic terminal-session evidence correlation for the local operator."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness_x.core.errors import TraceCorruptionError

from .protocol import AppEvent, AppEventKind, AppSessionSnapshot
from .report_attestation import MAX_REPORT_BYTES
from .report_projection import (
    ReportAttestationStatus,
    ReportCorruptionError,
    ReportUnavailableError,
    read_validated_coding_report,
)
from .trace_export import (
    MAX_TRACE_EXPORT_BYTES,
    TraceExportNotTerminalError,
    TraceExportUnavailableError,
    read_validated_trace_export,
)

TerminalSessionStatus = Literal["succeeded", "failed", "cancelled"]


class EvidenceManifestNotTerminalError(RuntimeError):
    """Evidence manifests are intentionally unavailable for mutable running sessions."""


class EvidenceManifestCorruptionError(RuntimeError):
    """Lifecycle or source evidence disagrees with the terminal-session manifest contract."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


class LifecycleEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-lifecycle-evidence-v1"] = "app-lifecycle-evidence-v1"
    status: TerminalSessionStatus
    snapshot_revision: int = Field(ge=1)
    snapshot_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_count: int = Field(ge=1)
    ledger_head_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ledger_head_kind: str
    created_at: datetime
    completed_at: datetime


class CodingReportEvidenceUnavailable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-coding-report-evidence-v1"] = (
        "app-coding-report-evidence-v1"
    )
    availability: Literal["not_available"] = "not_available"


class CodingReportEvidenceAvailable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-coding-report-evidence-v1"] = (
        "app-coding-report-evidence-v1"
    )
    availability: Literal["available"] = "available"
    source_filename: Literal["coding-task-report.json"] = "coding-task-report.json"
    source_bytes: int = Field(ge=0, le=MAX_REPORT_BYTES)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attestation_status: ReportAttestationStatus
    attested_source_bytes: int | None = Field(default=None, ge=0, le=MAX_REPORT_BYTES)
    attested_source_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    artifact_event_sequence: int = Field(ge=1)
    artifact_event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class TraceEvidenceUnavailable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-causal-trace-evidence-v1"] = (
        "app-causal-trace-evidence-v1"
    )
    availability: Literal["not_available"] = "not_available"


class TraceEvidenceAvailable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-causal-trace-evidence-v1"] = (
        "app-causal-trace-evidence-v1"
    )
    availability: Literal["available"] = "available"
    source_filename: Literal["causal-trace.jsonl"] = "causal-trace.jsonl"
    trace_id: str = Field(pattern=r"^trace_[0-9a-f]{32}$")
    source_bytes: int = Field(ge=0, le=MAX_TRACE_EXPORT_BYTES)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_count: int = Field(ge=0)
    final_event_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    attachment_event_sequence: int = Field(ge=1)
    attachment_event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


CodingReportEvidence = CodingReportEvidenceAvailable | CodingReportEvidenceUnavailable
TraceEvidence = TraceEvidenceAvailable | TraceEvidenceUnavailable


class TerminalEvidenceManifest(BaseModel):
    """One deterministic correlation object for current validated terminal evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["app-terminal-evidence-manifest-v1"] = (
        "app-terminal-evidence-manifest-v1"
    )
    session_id: str = Field(pattern=r"^app_[0-9a-f]{32}$")
    lifecycle: LifecycleEvidence
    coding_report: CodingReportEvidence
    causal_trace: TraceEvidence
    fingerprint: str = ""

    @model_validator(mode="after")
    def _derive_fingerprint(self) -> "TerminalEvidenceManifest":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", hashlib.sha256(_canonical(material)).hexdigest())
        return self


@dataclass(frozen=True, slots=True)
class RenderedEvidenceManifest:
    payload: bytes
    source_bytes: int
    source_sha256: str


def _validated_lifecycle(
    snapshot: AppSessionSnapshot,
    events: tuple[AppEvent, ...],
) -> LifecycleEvidence:
    if not snapshot.status.terminal:
        raise EvidenceManifestNotTerminalError(
            "evidence manifest is available only after the App Server session is terminal"
        )
    if snapshot.completed_at is None:
        raise EvidenceManifestCorruptionError("terminal session is missing completed_at")
    if not events:
        raise EvidenceManifestCorruptionError("terminal session lifecycle ledger is empty")

    previous_hash: str | None = None
    for expected_sequence, event in enumerate(events, start=1):
        if event.session_id != snapshot.session_id:
            raise EvidenceManifestCorruptionError(
                f"cross-session lifecycle event at sequence {expected_sequence}"
            )
        if event.sequence != expected_sequence:
            raise EvidenceManifestCorruptionError(
                f"non-contiguous lifecycle event sequence at {expected_sequence}"
            )
        if event.previous_hash != previous_hash:
            raise EvidenceManifestCorruptionError(
                f"broken lifecycle previous hash at sequence {expected_sequence}"
            )
        if not event.verify_hash():
            raise EvidenceManifestCorruptionError(
                f"lifecycle event hash mismatch at sequence {expected_sequence}"
            )
        previous_hash = event.event_hash

    if snapshot.event_count != len(events):
        raise EvidenceManifestCorruptionError(
            "session snapshot event_count disagrees with lifecycle ledger"
        )
    if snapshot.latest_event_hash != previous_hash:
        raise EvidenceManifestCorruptionError(
            "session snapshot latest_event_hash disagrees with lifecycle ledger head"
        )

    status = snapshot.status.value
    if status not in {"succeeded", "failed", "cancelled"}:
        raise EvidenceManifestCorruptionError("terminal session has an unsupported status")
    return LifecycleEvidence(
        status=status,
        snapshot_revision=snapshot.revision,
        snapshot_fingerprint=snapshot.fingerprint,
        event_count=len(events),
        ledger_head_hash=events[-1].event_hash,
        ledger_head_kind=events[-1].kind.value,
        created_at=snapshot.created_at,
        completed_at=snapshot.completed_at,
    )


def _report_evidence(
    snapshot: AppSessionSnapshot,
    events: tuple[AppEvent, ...],
) -> CodingReportEvidence:
    artifact_events = tuple(
        event
        for event in events
        if event.kind == AppEventKind.ARTIFACT_AVAILABLE
        and event.payload.get("artifact_kind") == "coding_task_report"
    )
    if snapshot.coding_report_path is None:
        if artifact_events:
            raise EvidenceManifestCorruptionError(
                "coding report artifact exists but snapshot has no coding_report_path"
            )
        return CodingReportEvidenceUnavailable()

    try:
        validated = read_validated_coding_report(snapshot=snapshot, events=events)
    except ReportUnavailableError as exc:
        raise EvidenceManifestCorruptionError(
            f"coding report evidence unexpectedly unavailable: {exc}"
        ) from exc
    except ReportCorruptionError as exc:
        raise EvidenceManifestCorruptionError(
            f"coding report evidence is corrupt: {exc}"
        ) from exc

    return CodingReportEvidenceAvailable(
        source_bytes=validated.source.source_bytes,
        source_sha256=validated.source.source_sha256,
        attestation_status=validated.attestation_status,
        attested_source_bytes=validated.attested_source_bytes,
        attested_source_sha256=validated.attested_source_sha256,
        artifact_event_sequence=validated.artifact_event_sequence,
        artifact_event_hash=validated.artifact_event_hash,
    )


def _trace_evidence(
    snapshot: AppSessionSnapshot,
    events: tuple[AppEvent, ...],
) -> TraceEvidence:
    attachment_events = tuple(
        event for event in events if event.kind == AppEventKind.TRACE_ATTACHED
    )
    if snapshot.trace_id is None or snapshot.trace_path is None:
        if attachment_events:
            raise EvidenceManifestCorruptionError(
                "trace attachment exists but snapshot has no trace identity/path"
            )
        return TraceEvidenceUnavailable()

    try:
        validated = read_validated_trace_export(snapshot=snapshot, events=events)
    except TraceExportNotTerminalError as exc:
        raise EvidenceManifestCorruptionError(
            f"trace evidence unexpectedly nonterminal: {exc}"
        ) from exc
    except TraceExportUnavailableError as exc:
        raise EvidenceManifestCorruptionError(
            f"trace evidence unexpectedly unavailable: {exc}"
        ) from exc
    except TraceCorruptionError as exc:
        raise EvidenceManifestCorruptionError(
            f"causal trace evidence is corrupt: {exc}"
        ) from exc

    return TraceEvidenceAvailable(
        trace_id=validated.trace_id,
        source_bytes=validated.source.source_bytes,
        source_sha256=validated.source.source_sha256,
        record_count=len(validated.records),
        final_event_hash=validated.final_event_hash,
        attachment_event_sequence=validated.attachment_event_sequence,
        attachment_event_hash=validated.attachment_event_hash,
    )


def _verify_snapshot_fingerprint(snapshot: AppSessionSnapshot) -> None:
    try:
        recomputed = AppSessionSnapshot.model_validate(snapshot.model_dump(mode="json"))
    except Exception as exc:
        raise EvidenceManifestCorruptionError(
            f"session snapshot cannot be revalidated: {exc}"
        ) from exc
    if snapshot.fingerprint != recomputed.fingerprint:
        raise EvidenceManifestCorruptionError(
            "session snapshot fingerprint does not match snapshot contents"
        )


def build_terminal_evidence_manifest(
    *,
    snapshot: AppSessionSnapshot,
    events: tuple[AppEvent, ...],
) -> TerminalEvidenceManifest:
    """Build one deterministic manifest from validated terminal-session evidence."""

    lifecycle = _validated_lifecycle(snapshot, events)
    coding_report = _report_evidence(snapshot, events)
    causal_trace = _trace_evidence(snapshot, events)
    _verify_snapshot_fingerprint(snapshot)
    return TerminalEvidenceManifest(
        session_id=snapshot.session_id,
        lifecycle=lifecycle,
        coding_report=coding_report,
        causal_trace=causal_trace,
    )


def render_terminal_evidence_manifest(
    manifest: TerminalEvidenceManifest,
) -> RenderedEvidenceManifest:
    """Serialize once and retain the exact generated bytes described by the response digest."""

    payload = manifest.model_dump_json().encode("utf-8") + b"\n"
    return RenderedEvidenceManifest(
        payload=payload,
        source_bytes=len(payload),
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )
