"""M47 extension for independent portable App Server session-snapshot verification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from harness_x.app_server.evidence_manifest import TerminalEvidenceManifest
from harness_x.app_server.snapshot_export import (
    MAX_SESSION_SNAPSHOT_EXPORT_BYTES,
    PortableSessionSnapshot,
    canonical_snapshot_material,
)

from .evidence_verification import (
    MAX_EVIDENCE_MANIFEST_BYTES,
    BoundedEvidenceSource,
    PortableEvidenceVerification,
    PortableEvidenceVerificationError,
    _bounded_regular_file,
    _load_manifest,
    verify_portable_evidence,
)


@dataclass(frozen=True, slots=True)
class PortableEvidenceVerificationWithSnapshot:
    """Successful M47 portable verification result including snapshot state."""

    base: PortableEvidenceVerification
    snapshot_status: str
    snapshot_revision: int | None

    def summary(self) -> str:
        lifecycle_events = (
            "none" if self.base.lifecycle_events is None else str(self.base.lifecycle_events)
        )
        trace_records = (
            "none" if self.base.trace_records is None else str(self.base.trace_records)
        )
        snapshot_revision = (
            "none" if self.snapshot_revision is None else str(self.snapshot_revision)
        )
        return (
            "valid: "
            f"session={self.base.session_id} "
            f"manifest_bytes={self.base.manifest_bytes} "
            f"manifest_sha256={self.base.manifest_sha256} "
            f"snapshot={self.snapshot_status} "
            f"snapshot_revision={snapshot_revision} "
            f"lifecycle={self.base.lifecycle_status} "
            f"lifecycle_events={lifecycle_events} "
            f"report={self.base.report_status} "
            f"trace={self.base.trace_status} "
            f"trace_records={trace_records}"
        )


def _reject_snapshot_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PortableEvidenceVerificationError(
                f"session snapshot JSON contains duplicate object key: {key}"
            )
        result[key] = value
    return result


def _load_snapshot(source: BoundedEvidenceSource) -> PortableSessionSnapshot:
    try:
        text = source.payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PortableEvidenceVerificationError(
            "session snapshot is not valid UTF-8"
        ) from exc
    try:
        raw = json.loads(text, object_pairs_hook=_reject_snapshot_duplicate_keys)
    except PortableEvidenceVerificationError:
        raise
    except json.JSONDecodeError as exc:
        raise PortableEvidenceVerificationError(
            f"session snapshot is not valid JSON: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise PortableEvidenceVerificationError(
            "session snapshot JSON root must be an object"
        )

    supplied_fingerprint = raw.get("fingerprint")
    if not isinstance(supplied_fingerprint, str):
        raise PortableEvidenceVerificationError(
            "session snapshot fingerprint is missing or invalid"
        )
    material = dict(raw)
    material.pop("fingerprint", None)
    recomputed = hashlib.sha256(canonical_snapshot_material(material)).hexdigest()
    if supplied_fingerprint != recomputed:
        raise PortableEvidenceVerificationError(
            "session snapshot fingerprint does not match downloaded snapshot contents"
        )

    try:
        snapshot = PortableSessionSnapshot.model_validate(raw)
    except ValidationError as exc:
        raise PortableEvidenceVerificationError(
            f"session snapshot does not satisfy app-session-snapshot-v1: {exc}"
        ) from exc
    if snapshot.fingerprint != supplied_fingerprint:
        raise PortableEvidenceVerificationError(
            "session snapshot fingerprint changed during portable schema validation"
        )
    return snapshot


def _verify_snapshot(
    manifest: TerminalEvidenceManifest,
    snapshot_path: str | Path,
) -> tuple[str, int]:
    source = _bounded_regular_file(
        snapshot_path,
        maximum_bytes=MAX_SESSION_SNAPSHOT_EXPORT_BYTES,
    )
    snapshot = _load_snapshot(source)
    expected = manifest.lifecycle

    comparisons = (
        (snapshot.session_id, manifest.session_id, "session ID"),
        (snapshot.status, expected.status, "status"),
        (snapshot.revision, expected.snapshot_revision, "revision"),
        (snapshot.fingerprint, expected.snapshot_fingerprint, "fingerprint"),
        (snapshot.event_count, expected.event_count, "event count"),
        (snapshot.latest_event_hash, expected.ledger_head_hash, "lifecycle head hash"),
        (snapshot.created_at, expected.created_at, "created timestamp"),
        (snapshot.completed_at, expected.completed_at, "completed timestamp"),
    )
    for observed, wanted, label in comparisons:
        if observed != wanted:
            raise PortableEvidenceVerificationError(
                f"session snapshot {label} does not match manifest"
            )
    return "verified", snapshot.revision


def verify_portable_evidence_with_snapshot(
    manifest_path: str | Path,
    *,
    snapshot_path: str | Path | None = None,
    lifecycle_path: str | Path | None = None,
    report_path: str | Path | None = None,
    trace_path: str | Path | None = None,
) -> PortableEvidenceVerificationWithSnapshot:
    """Verify M44/M45 evidence plus an optional complete M47 session snapshot."""

    # When M47 evidence is omitted, delegate through the frozen M45 verifier path exactly.
    # The wrapper adds only explicit snapshot=not_supplied summary state and performs no
    # M47-specific manifest read or correlation work.
    if snapshot_path is None:
        base = verify_portable_evidence(
            manifest_path,
            lifecycle_path=lifecycle_path,
            report_path=report_path,
            trace_path=trace_path,
        )
        return PortableEvidenceVerificationWithSnapshot(
            base=base,
            snapshot_status="not_supplied",
            snapshot_revision=None,
        )

    # Keep the M45 verifier unchanged, but independently pin the manifest bytes used by
    # the M47 correlation step. A combined success is valid only when both reads saw
    # the same bounded manifest identity, preventing cross-read manifest substitution.
    manifest_source = _bounded_regular_file(
        manifest_path,
        maximum_bytes=MAX_EVIDENCE_MANIFEST_BYTES,
    )
    manifest = _load_manifest(manifest_source)
    base = verify_portable_evidence(
        manifest_path,
        lifecycle_path=lifecycle_path,
        report_path=report_path,
        trace_path=trace_path,
    )
    if (
        base.manifest_bytes != manifest_source.source_bytes
        or base.manifest_sha256 != manifest_source.source_sha256
    ):
        raise PortableEvidenceVerificationError(
            "evidence manifest changed between portable verification reads"
        )

    snapshot_status, snapshot_revision = _verify_snapshot(manifest, snapshot_path)
    return PortableEvidenceVerificationWithSnapshot(
        base=base,
        snapshot_status=snapshot_status,
        snapshot_revision=snapshot_revision,
    )
