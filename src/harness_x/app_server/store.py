"""Durable single-user session/event store for the local Harness X App Server."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .protocol import (
    AppEvent,
    AppEventKind,
    AppSessionSnapshot,
    AppSessionStatus,
    CodingSessionRequest,
)

_ALLOWED_TRANSITIONS: dict[AppSessionStatus, frozenset[AppSessionStatus]] = {
    AppSessionStatus.CREATED: frozenset(
        {
            AppSessionStatus.RUNNING,
            AppSessionStatus.CANCEL_REQUESTED,
            AppSessionStatus.FAILED,
        }
    ),
    AppSessionStatus.RUNNING: frozenset(
        {
            AppSessionStatus.SUCCEEDED,
            AppSessionStatus.FAILED,
            AppSessionStatus.CANCEL_REQUESTED,
        }
    ),
    AppSessionStatus.CANCEL_REQUESTED: frozenset(
        {
            AppSessionStatus.CANCELLED,
            AppSessionStatus.SUCCEEDED,
            AppSessionStatus.FAILED,
        }
    ),
    AppSessionStatus.SUCCEEDED: frozenset(),
    AppSessionStatus.FAILED: frozenset(),
    AppSessionStatus.CANCELLED: frozenset(),
}


class AppSessionStore:
    """File-backed snapshots plus hash-chained append-only event ledgers.

    Events are fsynced before the snapshot projection is replaced. On startup, a snapshot that
    trails its ledger is deterministically reconciled. The ledger is the transition evidence;
    the snapshot is only a convenient projection.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._snapshots: dict[str, AppSessionSnapshot] = {}
        self._load_existing()

    @property
    def sessions(self) -> tuple[AppSessionSnapshot, ...]:
        with self._lock:
            return tuple(
                sorted(self._snapshots.values(), key=lambda item: item.created_at)
            )

    def session(self, session_id: str) -> AppSessionSnapshot:
        with self._lock:
            try:
                return self._snapshots[session_id]
            except KeyError as exc:
                raise KeyError(f"unknown app session {session_id}") from exc

    def create_session(
        self,
        request: CodingSessionRequest,
        *,
        output_root: str | Path,
    ) -> AppSessionSnapshot:
        with self._lock:
            session_id = f"app_{uuid.uuid4().hex}"
            session_root = self._session_root(session_id)
            session_root.mkdir(parents=True, exist_ok=False)
            snapshot = AppSessionSnapshot(
                session_id=session_id,
                status=AppSessionStatus.CREATED,
                request=request,
                output_root=str(Path(output_root).resolve()),
                created_at=datetime.now(timezone.utc),
            )
            self._write_snapshot(snapshot)
            self._snapshots[session_id] = snapshot
            event = self._append_event_locked(
                snapshot,
                kind=AppEventKind.SESSION_CREATED,
                payload={
                    "status": AppSessionStatus.CREATED.value,
                    "output_root": snapshot.output_root,
                },
            )
            snapshot = self._apply_event(snapshot, event)
            self._write_snapshot(snapshot)
            self._snapshots[session_id] = snapshot
            return snapshot

    def events(self, session_id: str, *, after_sequence: int = 0) -> tuple[AppEvent, ...]:
        with self._lock:
            self.session(session_id)
            return tuple(
                item
                for item in self._read_events(session_id)
                if item.sequence > after_sequence
            )

    def transition(
        self,
        session_id: str,
        *,
        status: AppSessionStatus,
        kind: AppEventKind,
        payload: dict[str, object] | None = None,
        coding_report_path: str | None = None,
        failure_reason: str | None = None,
    ) -> AppSessionSnapshot:
        with self._lock:
            current = self.session(session_id)
            if status == current.status:
                raise ValueError(f"app session is already {status.value}")
            if status not in _ALLOWED_TRANSITIONS[current.status]:
                raise ValueError(
                    f"invalid app session transition {current.status.value}->{status.value}"
                )
            event_payload: dict[str, object] = dict(payload or {})
            event_payload["status"] = status.value
            if coding_report_path is not None:
                event_payload["coding_report_path"] = coding_report_path
            if failure_reason is not None:
                event_payload["failure_reason"] = failure_reason[:4000]
            event = self._append_event_locked(current, kind=kind, payload=event_payload)
            updated = self._apply_event(current, event)
            self._write_snapshot(updated)
            self._snapshots[session_id] = updated
            return updated

    def request_cancel(self, session_id: str) -> AppSessionSnapshot:
        with self._lock:
            current = self.session(session_id)
            if current.status == AppSessionStatus.CANCEL_REQUESTED:
                return current
            if current.status.terminal:
                raise ValueError("cannot cancel a terminal app session")
            return self.transition(
                session_id,
                status=AppSessionStatus.CANCEL_REQUESTED,
                kind=AppEventKind.SESSION_CANCEL_REQUESTED,
                payload={"cancel_requested": True},
            )

    def add_artifact(
        self,
        session_id: str,
        *,
        artifact_kind: str,
        path: str | Path,
    ) -> AppSessionSnapshot:
        with self._lock:
            current = self.session(session_id)
            event = self._append_event_locked(
                current,
                kind=AppEventKind.ARTIFACT_AVAILABLE,
                payload={
                    "artifact_kind": artifact_kind[:120],
                    "path": str(Path(path).resolve()),
                },
            )
            updated = self._apply_event(current, event)
            self._write_snapshot(updated)
            self._snapshots[session_id] = updated
            return updated

    def _load_existing(self) -> None:
        with self._lock:
            for directory in sorted(self.root.glob("app_*")):
                if not directory.is_dir():
                    continue
                snapshot_path = directory / "snapshot.json"
                if not snapshot_path.is_file():
                    raise RuntimeError(f"app session missing snapshot: {directory}")
                try:
                    snapshot = AppSessionSnapshot.model_validate_json(
                        snapshot_path.read_text(encoding="utf-8")
                    )
                except Exception as exc:
                    raise RuntimeError(f"invalid app session snapshot {snapshot_path}: {exc}") from exc
                if snapshot.session_id != directory.name:
                    raise RuntimeError(f"app session directory/snapshot ID mismatch: {directory}")
                events = self._read_events(snapshot.session_id)
                if not events and snapshot.event_count == 0:
                    event = self._append_event_locked(
                        snapshot,
                        kind=AppEventKind.SESSION_CREATED,
                        payload={
                            "status": snapshot.status.value,
                            "output_root": snapshot.output_root,
                            "recovered_missing_initial_event": True,
                        },
                    )
                    events = (event,)
                if snapshot.event_count > len(events):
                    raise RuntimeError(
                        f"app session snapshot is ahead of event ledger: {snapshot.session_id}"
                    )
                if snapshot.event_count:
                    recorded = events[snapshot.event_count - 1]
                    if snapshot.latest_event_hash != recorded.event_hash:
                        raise RuntimeError(
                            f"app session snapshot event hash mismatch: {snapshot.session_id}"
                        )
                for event in events[snapshot.event_count :]:
                    snapshot = self._apply_event(snapshot, event)
                if snapshot.event_count != len(events):
                    raise RuntimeError(f"app session reconciliation failed: {snapshot.session_id}")
                self._write_snapshot(snapshot)
                self._snapshots[snapshot.session_id] = snapshot

    def _read_events(self, session_id: str) -> tuple[AppEvent, ...]:
        path = self._events_path(session_id)
        if not path.exists():
            return ()
        result: list[AppEvent] = []
        previous: str | None = None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise RuntimeError(f"cannot read app event ledger {path}: {exc}") from exc
        for index, line in enumerate(lines, 1):
            if not line.strip():
                raise RuntimeError(f"blank line in app event ledger {path}:{index}")
            try:
                event = AppEvent.model_validate_json(line)
            except Exception as exc:
                raise RuntimeError(f"invalid app event {path}:{index}: {exc}") from exc
            if event.session_id != session_id:
                raise RuntimeError(f"cross-session event in {path}:{index}")
            if event.sequence != index:
                raise RuntimeError(f"non-contiguous app event sequence in {path}:{index}")
            if event.previous_hash != previous:
                raise RuntimeError(f"broken app event hash chain in {path}:{index}")
            if not event.verify_hash():
                raise RuntimeError(f"app event hash mismatch in {path}:{index}")
            previous = event.event_hash
            result.append(event)
        return tuple(result)

    def _append_event_locked(
        self,
        snapshot: AppSessionSnapshot,
        *,
        kind: AppEventKind,
        payload: dict[str, object] | None = None,
    ) -> AppEvent:
        event = AppEvent.create(
            session_id=snapshot.session_id,
            sequence=snapshot.event_count + 1,
            kind=kind,
            payload=dict(payload or {}),
            previous_hash=snapshot.latest_event_hash,
        )
        path = self._events_path(snapshot.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            handle.write(event.model_dump_json().encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def _apply_event(
        self,
        snapshot: AppSessionSnapshot,
        event: AppEvent,
    ) -> AppSessionSnapshot:
        if event.sequence != snapshot.event_count + 1:
            raise RuntimeError("cannot apply non-next app event to snapshot")
        if event.previous_hash != snapshot.latest_event_hash:
            raise RuntimeError("cannot apply app event with mismatched previous hash")
        updates: dict[str, object] = {
            "event_count": event.sequence,
            "latest_event_hash": event.event_hash,
            "revision": snapshot.revision + 1,
        }
        raw_status = event.payload.get("status")
        if raw_status is not None:
            status = AppSessionStatus(str(raw_status))
            if status != snapshot.status:
                if status not in _ALLOWED_TRANSITIONS[snapshot.status] and not (
                    event.kind == AppEventKind.SESSION_CREATED
                    and snapshot.event_count == 0
                    and status == snapshot.status
                ):
                    raise RuntimeError(
                        f"event encodes invalid app transition {snapshot.status.value}->{status.value}"
                    )
                updates["status"] = status
                if status == AppSessionStatus.RUNNING and snapshot.started_at is None:
                    updates["started_at"] = event.created_at
                if status.terminal:
                    updates["completed_at"] = event.created_at
        if event.kind == AppEventKind.SESSION_CANCEL_REQUESTED:
            updates["cancel_requested"] = True
        if event.payload.get("coding_report_path") is not None:
            updates["coding_report_path"] = str(event.payload["coding_report_path"])
        if event.payload.get("failure_reason") is not None:
            updates["failure_reason"] = str(event.payload["failure_reason"])[:4000]
        return snapshot.model_copy(update=updates)

    def _session_root(self, session_id: str) -> Path:
        return self.root / session_id

    def _snapshot_path(self, session_id: str) -> Path:
        return self._session_root(session_id) / "snapshot.json"

    def _events_path(self, session_id: str) -> Path:
        return self._session_root(session_id) / "events.jsonl"

    def _write_snapshot(self, snapshot: AppSessionSnapshot) -> None:
        path = self._snapshot_path(snapshot.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        payload = snapshot.model_dump_json(indent=2) + "\n"
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
