from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from harness_x.app_server.evidence_manifest import (
    CodingReportEvidenceUnavailable,
    LifecycleEvidence,
    TerminalEvidenceManifest,
    TraceEvidenceUnavailable,
)
from harness_x.evidence_verification import verify_portable_evidence


def test_explicit_dotdot_path_is_normalized_without_weakening_symlink_checks(
    tmp_path: Path,
) -> None:
    manifest = TerminalEvidenceManifest(
        session_id="app_" + "a" * 32,
        lifecycle=LifecycleEvidence(
            status="succeeded",
            snapshot_revision=2,
            snapshot_fingerprint="1" * 64,
            event_count=2,
            ledger_head_hash="2" * 64,
            ledger_head_kind="session_completed",
            created_at=datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 8, 23, 0, 1, tzinfo=timezone.utc),
        ),
        coding_report=CodingReportEvidenceUnavailable(),
        causal_trace=TraceEvidenceUnavailable(),
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json() + "\n", encoding="utf-8")

    nested = tmp_path / "nested"
    nested.mkdir()
    explicit_path = nested / ".." / "manifest.json"

    result = verify_portable_evidence(explicit_path)

    assert result.session_id == manifest.session_id
    assert result.report_status == "not_available"
    assert result.trace_status == "not_available"
