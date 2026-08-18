"""Grounded provenance carried by observations and durable state proposals."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator

from .ids import SystemVersion, TraceId


class SourceKind(StrEnum):
    USER = "user"
    ENVIRONMENT = "environment"
    TOOL = "tool"
    MEMORY = "memory"
    MODEL = "model"
    SYSTEM = "system"
    TEST = "test"


class VerificationState(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_kind: SourceKind
    source_ref: str
    created_at: datetime
    system_version: SystemVersion
    trace_id: TraceId | None = None
    verification: VerificationState = VerificationState.UNVERIFIED

    @field_validator("created_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Provenance.created_at must be timezone-aware")
        return value
