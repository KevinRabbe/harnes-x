"""Error/anomaly memory that keeps hypotheses separate from confirmed causes."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from harness_x.core.errors import MemoryNotFoundError, MemorySubsystemError
from harness_x.core.events import EventType
from harness_x.core.ids import EventId, MemoryId, TaskId
from harness_x.core.provenance import Provenance
from harness_x.telemetry import TraceRecorder

from .base import MemoryClass


class ErrorSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ErrorStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class CauseHypothesis(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("cause hypothesis cannot be blank")
        return value


class ErrorRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory_id: MemoryId
    task_id: TaskId
    anomaly: str = Field(min_length=1)
    source_event_id: EventId
    severity: ErrorSeverity
    status: ErrorStatus = ErrorStatus.OPEN
    suspected_causes: tuple[CauseHypothesis, ...] = ()
    confirmed_cause: str | None = None
    resolution_evidence: tuple[str, ...] = ()
    provenance: Provenance
    revision: int = Field(default=1, ge=1)

    @field_validator("anomaly")
    @classmethod
    def normalize_anomaly(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("anomaly cannot be blank")
        return value

    @model_validator(mode="after")
    def preserve_epistemic_state(self) -> "ErrorRecord":
        if self.status == ErrorStatus.RESOLVED:
            if not self.resolution_evidence:
                raise ValueError("resolved errors require resolution evidence")
        elif self.confirmed_cause is not None:
            raise ValueError("a cause can only be confirmed when the error is resolved")
        return self


class ErrorBuffer:
    """Structured unresolved-error memory with explicit evidence transitions."""

    def __init__(self, recorder: TraceRecorder):
        self.recorder = recorder
        self._records: dict[str, ErrorRecord] = {}

    def record(
        self,
        *,
        anomaly: str,
        source_event_id: EventId,
        severity: ErrorSeverity,
        provenance: Provenance,
        memory_id: MemoryId | None = None,
    ) -> ErrorRecord:
        candidate = ErrorRecord(
            memory_id=memory_id or MemoryId.new(),
            task_id=self.recorder.task_id,
            anomaly=anomaly,
            source_event_id=source_event_id,
            severity=severity,
            provenance=provenance,
        )
        key = str(candidate.memory_id)
        if key in self._records:
            raise MemorySubsystemError(f"error record {candidate.memory_id} already exists")

        self.recorder.emit(
            EventType.ERROR_RECORDED,
            "memory.error_buffer",
            input_refs=(str(source_event_id),),
            output_refs=(key,),
            metadata={
                "memory_class": MemoryClass.ERROR.value,
                "code": "observed_anomaly",
                "severity": candidate.severity.value,
                "status": candidate.status.value,
                "snapshot": candidate.model_dump(mode="json"),
            },
        )
        self._records[key] = candidate
        return candidate

    def add_suspected_cause(
        self,
        memory_id: MemoryId,
        description: str,
        *,
        evidence_refs: tuple[str, ...] = (),
        confidence: float | None = None,
    ) -> ErrorRecord:
        current = self._require(memory_id)
        if current.status in {ErrorStatus.RESOLVED, ErrorStatus.DISMISSED}:
            raise MemorySubsystemError("terminal error records cannot gain new hypotheses")

        hypothesis = CauseHypothesis(
            description=description,
            evidence_refs=evidence_refs,
            confidence=confidence,
        )
        updated = current.model_copy(
            update={
                "status": ErrorStatus.INVESTIGATING,
                "suspected_causes": current.suspected_causes + (hypothesis,),
                "revision": current.revision + 1,
            }
        )
        self._commit_update(current, updated, operation="add_suspected_cause")
        return updated

    def resolve(
        self,
        memory_id: MemoryId,
        *,
        resolution_evidence: tuple[str, ...],
        confirmed_cause: str | None = None,
    ) -> ErrorRecord:
        current = self._require(memory_id)
        if current.status in {ErrorStatus.RESOLVED, ErrorStatus.DISMISSED}:
            raise MemorySubsystemError("terminal error record cannot be resolved again")
        evidence = tuple(ref.strip() for ref in resolution_evidence)
        if not evidence or any(not ref for ref in evidence):
            raise ValueError("resolution requires non-empty evidence references")
        if confirmed_cause is not None:
            confirmed_cause = confirmed_cause.strip()
            if not confirmed_cause:
                raise ValueError("confirmed cause cannot be blank")

        updated = ErrorRecord(
            **current.model_dump(),
            status=ErrorStatus.RESOLVED,
            confirmed_cause=confirmed_cause,
            resolution_evidence=evidence,
            revision=current.revision + 1,
        )
        self._commit_update(current, updated, operation="resolve")
        return updated

    def dismiss(
        self,
        memory_id: MemoryId,
        *,
        evidence_refs: tuple[str, ...],
    ) -> ErrorRecord:
        current = self._require(memory_id)
        if current.status in {ErrorStatus.RESOLVED, ErrorStatus.DISMISSED}:
            raise MemorySubsystemError("terminal error record cannot be dismissed again")
        evidence = tuple(ref.strip() for ref in evidence_refs)
        if not evidence or any(not ref for ref in evidence):
            raise ValueError("dismissal requires evidence")
        updated = current.model_copy(
            update={
                "status": ErrorStatus.DISMISSED,
                "resolution_evidence": evidence,
                "revision": current.revision + 1,
            }
        )
        self._commit_update(current, updated, operation="dismiss")
        return updated

    def get(self, memory_id: MemoryId) -> ErrorRecord:
        return self._require(memory_id)

    def unresolved(self) -> tuple[ErrorRecord, ...]:
        records = tuple(
            sorted(
                (
                    record
                    for record in self._records.values()
                    if record.status in {ErrorStatus.OPEN, ErrorStatus.INVESTIGATING}
                ),
                key=lambda record: (record.severity.value, str(record.memory_id)),
            )
        )
        self.recorder.emit(
            EventType.MEMORY_RETRIEVED,
            "memory.error_buffer",
            input_refs=tuple(str(record.memory_id) for record in records),
            metadata={
                "memory_class": MemoryClass.ERROR.value,
                "query": "unresolved",
                "result_count": len(records),
            },
        )
        return records

    def all(self) -> tuple[ErrorRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    def _commit_update(
        self,
        current: ErrorRecord,
        updated: ErrorRecord,
        *,
        operation: str,
    ) -> None:
        self.recorder.emit(
            EventType.MEMORY_WRITTEN,
            "memory.error_buffer",
            input_refs=(str(current.memory_id),),
            output_refs=(str(updated.memory_id),),
            metadata={
                "memory_class": MemoryClass.ERROR.value,
                "operation": operation,
                "status": updated.status.value,
                "revision": updated.revision,
                "snapshot": updated.model_dump(mode="json"),
            },
        )
        self._records[str(updated.memory_id)] = updated

    def _require(self, memory_id: MemoryId) -> ErrorRecord:
        try:
            return self._records[str(memory_id)]
        except KeyError as exc:
            raise MemoryNotFoundError(f"error record {memory_id} does not exist") from exc
