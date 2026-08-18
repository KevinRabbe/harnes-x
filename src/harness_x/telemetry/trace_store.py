"""Append-only JSONL trace ledger with per-trace integrity chains."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, ValidationError

from harness_x.core.clock import Clock
from harness_x.core.errors import TraceCorruptionError, TraceError
from harness_x.core.events import EventType, TraceEvent
from harness_x.core.ids import EventId, SystemVersion, TaskId, TraceId

RECORD_SCHEMA_VERSION = "1"
FIXTURE_SCHEMA_VERSION = "1"


def _canonical_event(event: TraceEvent) -> str:
    return json.dumps(
        event.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _event_hash(event: TraceEvent, previous_hash: str | None) -> str:
    payload = (
        f"{event.step}\n{previous_hash or ''}\n{_canonical_event(event)}"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class TraceRecord(BaseModel):
    """Storage envelope around a causal event."""

    model_config = ConfigDict(frozen=True)

    record_schema_version: str = RECORD_SCHEMA_VERSION
    previous_hash: str | None = None
    event_hash: str
    event: TraceEvent


class TraceFixture(BaseModel):
    """Portable run fixture independent from the JSONL storage envelope."""

    model_config = ConfigDict(frozen=True)

    fixture_schema_version: str = FIXTURE_SCHEMA_VERSION
    trace_id: TraceId
    events: list[TraceEvent]
    expected_state: dict[str, Any]


class TraceStore:
    """First append-only trace backend.

    The JSONL backend is intentionally simple and inspectable. It is single-writer for
    now; later storage backends can replace it without changing the event contract.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load_verified(self) -> list[TraceRecord]:
        if not self.path.exists():
            return []

        records: list[TraceRecord] = []
        # trace_id -> (last_step, last_hash, last_timestamp)
        chain: dict[str, tuple[int, str, object]] = {}

        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                if not raw.strip():
                    continue
                try:
                    record = TraceRecord.model_validate_json(raw)
                except (ValidationError, ValueError) as exc:
                    raise TraceCorruptionError(
                        f"invalid trace record at line {line_number}: {exc}"
                    ) from exc

                if record.record_schema_version != RECORD_SCHEMA_VERSION:
                    raise TraceCorruptionError(
                        f"unsupported record schema at line {line_number}"
                    )

                key = str(record.event.trace_id)
                prior = chain.get(key)
                expected_step = 1 if prior is None else prior[0] + 1
                expected_previous_hash = None if prior is None else prior[1]

                if record.event.step != expected_step:
                    raise TraceCorruptionError(
                        f"out-of-order trace {key}: expected step {expected_step}, "
                        f"got {record.event.step}"
                    )
                if record.previous_hash != expected_previous_hash:
                    raise TraceCorruptionError(
                        f"broken hash chain for {key} at step {record.event.step}"
                    )

                expected_hash = _event_hash(record.event, record.previous_hash)
                if record.event_hash != expected_hash:
                    raise TraceCorruptionError(
                        f"event hash mismatch for {key} at step {record.event.step}"
                    )

                if prior is not None and record.event.timestamp < prior[2]:
                    raise TraceCorruptionError(
                        f"timestamp moved backwards for {key} at step {record.event.step}"
                    )

                chain[key] = (
                    record.event.step,
                    record.event_hash,
                    record.event.timestamp,
                )
                records.append(record)

        return records

    def append(self, event: TraceEvent) -> TraceRecord:
        records = self._load_verified()
        same_trace = [
            record for record in records if record.event.trace_id == event.trace_id
        ]
        previous = same_trace[-1] if same_trace else None
        expected_step = 1 if previous is None else previous.event.step + 1

        if event.step != expected_step:
            raise TraceError(
                f"trace step must be {expected_step}, got {event.step}"
            )
        if previous is not None and event.timestamp < previous.event.timestamp:
            raise TraceError("event timestamp cannot move backwards within a trace")

        previous_hash = previous.event_hash if previous else None
        record = TraceRecord(
            previous_hash=previous_hash,
            event_hash=_event_hash(event, previous_hash),
            event=event,
        )

        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(record.model_dump_json() + "\n")

        return record

    def events(
        self,
        *,
        trace_id: TraceId | None = None,
        task_id: TaskId | None = None,
        component: str | None = None,
    ) -> list[TraceEvent]:
        events = [record.event for record in self._load_verified()]
        if trace_id is not None:
            events = [event for event in events if event.trace_id == trace_id]
        if task_id is not None:
            events = [event for event in events if event.task_id == task_id]
        if component is not None:
            events = [event for event in events if event.component == component]
        return events

    def next_step(self, trace_id: TraceId) -> int:
        events = self.events(trace_id=trace_id)
        return 1 if not events else events[-1].step + 1

    def export_fixture(
        self,
        trace_id: TraceId,
        expected_state: BaseModel | dict[str, Any],
        path: str | Path | None = None,
    ) -> TraceFixture:
        events = self.events(trace_id=trace_id)
        if not events:
            raise TraceError(f"trace {trace_id} does not exist")

        state = (
            expected_state.model_dump(mode="json")
            if isinstance(expected_state, BaseModel)
            else expected_state
        )
        fixture = TraceFixture(
            trace_id=trace_id,
            events=events,
            expected_state=state,
        )

        if path is not None:
            destination = Path(path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                fixture.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )

        return fixture


class TraceRecorder:
    """Allocates ordered events so components never manage trace steps themselves."""

    def __init__(
        self,
        store: TraceStore,
        trace_id: TraceId,
        task_id: TaskId,
        system_version: SystemVersion,
        clock: Clock,
    ):
        self.store = store
        self.trace_id = trace_id
        self.task_id = task_id
        self.system_version = system_version
        self.clock = clock

    def emit(
        self,
        event_type: EventType,
        component: str,
        *,
        input_refs: Iterable[str] = (),
        output_refs: Iterable[str] = (),
        metadata: dict[str, Any] | None = None,
        event_id: EventId | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            event_id=event_id or EventId.new(),
            trace_id=self.trace_id,
            task_id=self.task_id,
            step=self.store.next_step(self.trace_id),
            timestamp=self.clock.now(),
            event_type=event_type,
            component=component,
            system_version=self.system_version,
            input_refs=list(input_refs),
            output_refs=list(output_refs),
            metadata=metadata or {},
        )
        self.store.append(event)
        return event
