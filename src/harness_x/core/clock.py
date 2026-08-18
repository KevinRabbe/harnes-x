"""Clock abstraction so traces and tests never depend on implicit wall time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return an aware UTC timestamp."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FixedClock:
    instant: datetime

    def __post_init__(self) -> None:
        if self.instant.tzinfo is None or self.instant.utcoffset() is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")

    def now(self) -> datetime:
        return self.instant.astimezone(timezone.utc)
