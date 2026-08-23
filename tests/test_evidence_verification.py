from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness_x.app_server.evidence_manifest import (
    CodingReportEvidenceAvailable,
    CodingReportEvidenceUnavailable,
    LifecycleEvidence,
    TerminalEvidenceManifest,
    TraceEvidenceAvailable,
    TraceEvidenceUnavailable,
)
from harness_x.app_server.trace_projection import verify_trace_payload
from harness_x.core import EventId, EventType, SystemVersion, TaskId, TraceEvent, TraceId
from harness_x.evidence_verification import (
    MAX_EVIDENCE_MANIFEST_BYTES,
    PortableEvidenceVerificationError,
    verify_portable_evidence,
)
from harness_x.telemetry import TraceStore

_SESSION_ID = "app_" + "a" * 32


def _trace_payload(path: Path) -> tuple[str, bytes, int, str | None]:
    trace_id = TraceId.new()
    TraceStore(path).append(
        TraceEvent(
            event_id=EventId.new(),
            trace_id=trace_id,
            task_id=TaskId.new(),
            step=1,
            timestamp=datetime(2026, 8, 23, tzinfo=timezone.utc),
            event_type=EventType.REASONING_COMPLETED,
            component="reasoning.service",
            system_version=SystemVersion(value="test"),
            metadata={"summary": "done"},
        )
    )
    payload = path.read_bytes()
    records, partial = verify_trace_payload(
        payload,
        expected_trace_id=trace_id.value,
        require_complete_final_line=True,
        source_label=str(path),
    )
    assert partial is False
    return (
        trace_id.value,
        payload,
        len(records),
        records[-1].event_hash if records else None,
    )


def _lifecycle() -> LifecycleEvidence:
    return LifecycleEvidence(
        status="succeeded",
        snapshot_revision=5,
        snapshot_fingerprint="1" * 64,
        event_count=5,
        ledger_head_hash="2" * 64,
        ledger_head_kind="session_completed",
        created_at=datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 23, 0, 1, tzinfo=timezone.utc),
    )


def _manifest(
    *,
    report_payload: bytes | None,
    report_status: str = "verified",
    trace_id: str | None = None,
    trace_payload: bytes | None = None,
    trace_records: int = 0,
    trace_final_hash: str | None = None,
) -> TerminalEvidenceManifest:
    if report_payload is None:
        report = CodingReportEvidenceUnavailable()
    else:
        digest = hashlib.sha256(report_payload).hexdigest()
        attested_bytes = len(report_payload) if report_status == "verified" else None
        attested_sha = digest if report_status == "verified" else None
        report = CodingReportEvidenceAvailable(
            source_bytes=len(report_payload),
            source_sha256=digest,
            attestation_status=report_status,
            attested_source_bytes=attested_bytes,
            attested_source_sha256=attested_sha,
            artifact_event_sequence=3,
            artifact_event_hash="3" * 64,
        )

    if trace_payload is None or trace_id is None:
        trace = TraceEvidenceUnavailable()
    else:
        trace = TraceEvidenceAvailable(
            trace_id=trace_id,
            source_bytes=len(trace_payload),
            source_sha256=hashlib.sha256(trace_payload).hexdigest(),
            record_count=trace_records,
            final_event_hash=trace_final_hash,
            attachment_event_sequence=2,
            attachment_event_hash="4" * 64,
        )

    return TerminalEvidenceManifest(
        session_id=_SESSION_ID,
        lifecycle=_lifecycle(),
        coding_report=report,
        causal_trace=trace,
    )


def _write_manifest(path: Path, manifest: TerminalEvidenceManifest) -> bytes:
    payload = manifest.model_dump_json().encode("utf-8") + b"\n"
    path.write_bytes(payload)
    return payload


def test_verify_portable_evidence_accepts_exact_report_trace_and_manifest(tmp_path: Path) -> None:
    report_path = tmp_path / "renamed-report.json"
    report_payload = b'{"succeeded":true,"note":"portable"}\n'
    report_path.write_bytes(report_payload)
    trace_path = tmp_path / "renamed-trace.jsonl"
    trace_id, trace_payload, record_count, final_hash = _trace_payload(trace_path)
    manifest = _manifest(
        report_payload=report_payload,
        trace_id=trace_id,
        trace_payload=trace_payload,
        trace_records=record_count,
        trace_final_hash=final_hash,
    )
    manifest_path = tmp_path / "session-evidence-manifest.json"
    manifest_payload = _write_manifest(manifest_path, manifest)

    before = {
        manifest_path: manifest_path.read_bytes(),
        report_path: report_path.read_bytes(),
        trace_path: trace_path.read_bytes(),
    }
    result = verify_portable_evidence(
        manifest_path,
        report_path=report_path,
        trace_path=trace_path,
    )

    assert result.session_id == _SESSION_ID
    assert result.manifest_bytes == len(manifest_payload)
    assert result.manifest_sha256 == hashlib.sha256(manifest_payload).hexdigest()
    assert result.report_status == "verified"
    assert result.trace_status == "verified"
    assert result.trace_records == 1
    assert result.summary().startswith("valid: session=app_")
    assert "report=verified trace=verified trace_records=1" in result.summary()
    for path, payload in before.items():
        assert path.read_bytes() == payload


def test_verify_manifest_rejects_stale_supplied_fingerprint(tmp_path: Path) -> None:
    manifest = _manifest(report_payload=None)
    raw = manifest.model_dump(mode="json")
    raw["lifecycle"]["event_count"] = 6
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(PortableEvidenceVerificationError, match="fingerprint"):
        verify_portable_evidence(manifest_path)


def test_verify_manifest_rejects_duplicate_json_key(tmp_path: Path) -> None:
    manifest = _manifest(report_payload=None)
    body = manifest.model_dump_json()
    duplicate = body[:-1] + ',"session_id":"' + _SESSION_ID + '"}'
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(duplicate, encoding="utf-8")

    with pytest.raises(PortableEvidenceVerificationError, match="duplicate object key"):
        verify_portable_evidence(manifest_path)


def test_verify_manifest_rejects_non_utf8_and_over_limit(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_bytes(b"\xff")
    with pytest.raises(PortableEvidenceVerificationError, match="UTF-8"):
        verify_portable_evidence(invalid)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (MAX_EVIDENCE_MANIFEST_BYTES + 1))
    with pytest.raises(PortableEvidenceVerificationError, match="byte limit"):
        verify_portable_evidence(oversized)


def test_verify_report_requirement_tracks_manifest_availability(tmp_path: Path) -> None:
    report_payload = b'{"succeeded":true}\n'
    report_path = tmp_path / "report.json"
    report_path.write_bytes(report_payload)

    available = tmp_path / "available.json"
    _write_manifest(available, _manifest(report_payload=report_payload))
    with pytest.raises(PortableEvidenceVerificationError, match="supply --report"):
        verify_portable_evidence(available)

    unavailable = tmp_path / "unavailable.json"
    _write_manifest(unavailable, _manifest(report_payload=None))
    with pytest.raises(PortableEvidenceVerificationError, match="not_available"):
        verify_portable_evidence(unavailable, report_path=report_path)


@pytest.mark.parametrize("status", ["verified", "legacy_unattested", "unavailable"])
def test_verify_report_preserves_m39_provenance_states(tmp_path: Path, status: str) -> None:
    report_payload = b'{"succeeded":true}\n'
    report_path = tmp_path / f"{status}.json"
    report_path.write_bytes(report_payload)
    manifest_path = tmp_path / f"{status}-manifest.json"
    _write_manifest(
        manifest_path,
        _manifest(report_payload=report_payload, report_status=status),
    )

    result = verify_portable_evidence(manifest_path, report_path=report_path)
    assert result.report_status == status
    assert result.trace_status == "not_available"


def test_verify_report_rejects_inconsistent_weaker_provenance(tmp_path: Path) -> None:
    report_payload = b'{"succeeded":true}\n'
    report_path = tmp_path / "report.json"
    report_path.write_bytes(report_payload)
    digest = hashlib.sha256(report_payload).hexdigest()
    manifest = TerminalEvidenceManifest(
        session_id=_SESSION_ID,
        lifecycle=_lifecycle(),
        coding_report=CodingReportEvidenceAvailable(
            source_bytes=len(report_payload),
            source_sha256=digest,
            attestation_status="legacy_unattested",
            attested_source_bytes=len(report_payload),
            attested_source_sha256=digest,
            artifact_event_sequence=3,
            artifact_event_hash="3" * 64,
        ),
        causal_trace=TraceEvidenceUnavailable(),
    )
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, manifest)

    with pytest.raises(PortableEvidenceVerificationError, match="cannot contain attested"):
        verify_portable_evidence(manifest_path, report_path=report_path)


def test_verify_report_rejects_hash_match_with_invalid_json_object_shape(tmp_path: Path) -> None:
    report_payload = b"[]\n"
    report_path = tmp_path / "report.json"
    report_path.write_bytes(report_payload)
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, _manifest(report_payload=report_payload))

    with pytest.raises(PortableEvidenceVerificationError, match="root must be an object"):
        verify_portable_evidence(manifest_path, report_path=report_path)


def test_verify_trace_requirement_tracks_manifest_availability(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_id, payload, count, final_hash = _trace_payload(trace_path)
    available = tmp_path / "available.json"
    _write_manifest(
        available,
        _manifest(
            report_payload=None,
            trace_id=trace_id,
            trace_payload=payload,
            trace_records=count,
            trace_final_hash=final_hash,
        ),
    )
    with pytest.raises(PortableEvidenceVerificationError, match="supply --trace"):
        verify_portable_evidence(available)

    unavailable = tmp_path / "unavailable.json"
    _write_manifest(unavailable, _manifest(report_payload=None))
    with pytest.raises(PortableEvidenceVerificationError, match="not_available"):
        verify_portable_evidence(unavailable, trace_path=trace_path)


def test_verify_trace_rejects_self_consistency_tamper_even_when_manifest_hash_is_updated(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_id, original, count, final_hash = _trace_payload(trace_path)
    row = json.loads(original.decode("utf-8").strip())
    row["event"]["metadata"]["summary"] = "tampered"
    tampered = (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")
    trace_path.write_bytes(tampered)
    manifest = _manifest(
        report_payload=None,
        trace_id=trace_id,
        trace_payload=tampered,
        trace_records=count,
        trace_final_hash=final_hash,
    )
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, manifest)

    with pytest.raises(PortableEvidenceVerificationError, match="integrity verification failed"):
        verify_portable_evidence(manifest_path, trace_path=trace_path)


def test_verify_trace_rejects_partial_final_record_even_when_manifest_hash_is_updated(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_id, original, count, final_hash = _trace_payload(trace_path)
    partial = original[:-1]
    trace_path.write_bytes(partial)
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(
        manifest_path,
        _manifest(
            report_payload=None,
            trace_id=trace_id,
            trace_payload=partial,
            trace_records=count,
            trace_final_hash=final_hash,
        ),
    )

    with pytest.raises(PortableEvidenceVerificationError, match="integrity verification failed"):
        verify_portable_evidence(manifest_path, trace_path=trace_path)


def test_verify_rejects_leaf_and_parent_symlink_substitution(tmp_path: Path) -> None:
    manifest = _manifest(report_payload=None)
    real = tmp_path / "real"
    real.mkdir()
    manifest_path = real / "manifest.json"
    _write_manifest(manifest_path, manifest)

    leaf = tmp_path / "leaf.json"
    try:
        leaf.symlink_to(manifest_path)
    except OSError:
        pytest.skip("symbolic links are unavailable on this platform")
    with pytest.raises(PortableEvidenceVerificationError, match="symbolic link"):
        verify_portable_evidence(leaf)

    parent_link = tmp_path / "linked-parent"
    parent_link.symlink_to(real, target_is_directory=True)
    with pytest.raises(PortableEvidenceVerificationError, match="path substitution"):
        verify_portable_evidence(parent_link / "manifest.json")


def test_verify_rejects_non_regular_manifest_input(tmp_path: Path) -> None:
    with pytest.raises(PortableEvidenceVerificationError, match="not a regular file"):
        verify_portable_evidence(tmp_path)
