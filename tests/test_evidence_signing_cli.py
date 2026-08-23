from __future__ import annotations

import json
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
        session_id="app_" + "c" * 32,
        lifecycle=LifecycleEvidence(
            status="succeeded",
            snapshot_revision=3,
            snapshot_fingerprint="3" * 64,
            event_count=3,
            ledger_head_hash="4" * 64,
            ledger_head_kind="session_completed",
            created_at=datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 8, 24, 0, 1, tzinfo=timezone.utc),
        ),
        coding_report=CodingReportEvidenceUnavailable(),
        causal_trace=TraceEvidenceUnavailable(),
    )
    path.write_text(manifest.model_dump_json() + "\n", encoding="utf-8")
    return manifest


def test_help_exposes_signing_commands_and_signature_options(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_entry.main(["--help"])
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "evidence-keygen" in output
    assert "sign-evidence" in output
    assert "verify-evidence" in output
    assert "verify-trace" in output

    with pytest.raises(SystemExit) as exc_info:
        cli_entry.main(["verify-evidence", "--help"])
    assert exc_info.value.code == 0
    verify_help = capsys.readouterr().out
    assert "--signature" in verify_help
    assert "--public-key" in verify_help


def test_cli_keygen_sign_and_verify_round_trip(tmp_path: Path, capsys) -> None:
    manifest_path = tmp_path / "session-evidence-manifest.json"
    manifest = _manifest(manifest_path)
    private_path = tmp_path / "private.pem"
    public_path = tmp_path / "public.pem"
    signature_path = tmp_path / "session-evidence-manifest.sig.json"

    assert cli_entry.main([
        "evidence-keygen",
        "--private-key", str(private_path),
        "--public-key", str(public_path),
    ]) == 0
    generated = capsys.readouterr().out.strip()
    assert generated.startswith("generated: key=sha256:")
    assert "PRIVATE KEY" not in generated

    assert cli_entry.main([
        "sign-evidence",
        str(manifest_path),
        "--private-key", str(private_path),
        "--output", str(signature_path),
    ]) == 0
    signed = capsys.readouterr().out.strip()
    assert signed.startswith("signed: manifest_sha256=")
    assert signature_path.exists()
    envelope = json.loads(signature_path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == "app-evidence-signature-v1"

    assert cli_entry.main([
        "verify-evidence",
        str(manifest_path),
        "--signature", str(signature_path),
        "--public-key", str(public_path),
    ]) == 0
    verified = capsys.readouterr().out.strip()
    assert verified.startswith(f"valid: session={manifest.session_id} ")
    assert "signature=verified key=sha256:" in verified


def test_cli_rejects_unpaired_signature_options(tmp_path: Path, capsys) -> None:
    manifest_path = tmp_path / "manifest.json"
    _manifest(manifest_path)

    with pytest.raises(SystemExit) as exc_info:
        cli_entry.main([
            "verify-evidence",
            str(manifest_path),
            "--signature", str(tmp_path / "missing.sig.json"),
        ])
    assert exc_info.value.code == 2
    assert "--signature and --public-key must be supplied together" in capsys.readouterr().err


def test_legacy_cli_import_does_not_eagerly_import_signing_or_crypto() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import harness_x.cli_entry; "
                "assert 'harness_x.evidence_signing' not in sys.modules; "
                "assert 'cryptography' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
