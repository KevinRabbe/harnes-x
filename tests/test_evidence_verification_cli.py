from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness_x import cli_entry
from harness_x.app_server.evidence_manifest import (
    CodingReportEvidenceUnavailable,
    LifecycleEvidence,
    TerminalEvidenceManifest,
    TraceEvidenceUnavailable,
)


def _manifest(path: Path) -> TerminalEvidenceManifest:
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
    path.write_text(manifest.model_dump_json() + "\n", encoding="utf-8")
    return manifest


def test_augmented_help_exposes_verify_evidence_without_removing_legacy_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_entry.main(["--help"])
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "verify-evidence" in output
    assert "verify-trace" in output
    assert "validate-config" in output
    assert "benchmark-dynamic-compute" in output


def test_verify_evidence_subcommand_help_exposes_optional_lifecycle_input(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_entry.main(["verify-evidence", "--help"])
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--lifecycle" in output
    assert "session-lifecycle-ledger.json" in output
    assert "--report" in output
    assert "--trace" in output


def test_verify_evidence_cli_prints_deterministic_valid_summary(tmp_path: Path, capsys) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest = _manifest(manifest_path)

    assert cli_entry.main(["verify-evidence", str(manifest_path)]) == 0
    output = capsys.readouterr().out.strip()
    assert output.startswith(f"valid: session={manifest.session_id} ")
    assert "manifest_bytes=" in output
    assert "manifest_sha256=" in output
    assert "lifecycle=not_supplied" in output
    assert "lifecycle_events=none" in output
    assert "report=not_available" in output
    assert "trace=not_available" in output
    assert "trace_records=none" in output


def test_verify_evidence_cli_uses_fail_visible_parser_error(tmp_path: Path, capsys) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        cli_entry.main(["verify-evidence", str(manifest_path)])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "manifest fingerprint is missing or invalid" in captured.err
    assert "valid:" not in captured.out


def test_pre_m44_commands_delegate_to_legacy_dispatcher_unchanged(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_main(argv):
        calls.append(list(argv))
        return 17

    monkeypatch.setattr(cli_entry.legacy_cli, "main", fake_main)
    assert cli_entry.main(["verify-trace", "trace.jsonl"]) == 17
    assert calls == [["verify-trace", "trace.jsonl"]]


def test_cli_wrapper_does_not_eagerly_import_evidence_verifier_for_legacy_surface() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import harness_x.cli_entry; "
                "assert 'harness_x.evidence_verification' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_verifier_source_has_no_network_client_surface() -> None:
    source = Path(cli_entry.__file__).with_name("evidence_verification.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "urllib",
        "requests",
        "http.client",
        "socket.create_connection",
        "urlopen",
    ):
        assert forbidden not in source
