from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_x.app_server.evidence_manifest import (
    build_terminal_evidence_manifest,
    render_terminal_evidence_manifest,
)
from harness_x.app_server.lifecycle_export import (
    MAX_LIFECYCLE_EXPORT_BYTES,
    build_lifecycle_ledger_export,
    render_lifecycle_ledger_export,
)
from harness_x.app_server.protocol import AppEventKind, AppSessionStatus, CodingSessionRequest
from harness_x.app_server.service import AppServerService
from harness_x.evidence_verification import (
    PortableEvidenceVerificationError,
    verify_portable_evidence,
)


def _portable_pair(tmp_path: Path) -> tuple[Path, Path, object, object]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    service = AppServerService(tmp_path / "service")
    request = CodingSessionRequest(
        workspace_root=workspace,
        task="verify lifecycle evidence offline",
        model_profile="main",
        verification_commands=("python -m pytest",),
    )
    snapshot = service.store.create_session(request, output_root=output)
    snapshot = service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.RUNNING,
        kind=AppEventKind.SESSION_STARTED,
    )
    snapshot = service.store.transition(
        snapshot.session_id,
        status=AppSessionStatus.SUCCEEDED,
        kind=AppEventKind.SESSION_COMPLETED,
    )
    events = service.store.events(snapshot.session_id)
    manifest = build_terminal_evidence_manifest(snapshot=snapshot, events=events)
    lifecycle = build_lifecycle_ledger_export(snapshot=snapshot, events=events)
    manifest_path = tmp_path / "session-evidence-manifest.json"
    lifecycle_path = tmp_path / "session-lifecycle-ledger.json"
    manifest_path.write_bytes(render_terminal_evidence_manifest(manifest).payload)
    lifecycle_path.write_bytes(render_lifecycle_ledger_export(lifecycle).payload)
    service.close()
    return manifest_path, lifecycle_path, manifest, lifecycle


def _rewrite(path: Path, mutate) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    mutate(raw)
    path.write_text(json.dumps(raw, separators=(",", ":")) + "\n", encoding="utf-8")


def test_lifecycle_is_optional_and_valid_export_is_independently_verified(tmp_path: Path) -> None:
    manifest_path, lifecycle_path, manifest, lifecycle = _portable_pair(tmp_path)

    partial = verify_portable_evidence(manifest_path)
    assert partial.session_id == manifest.session_id
    assert partial.lifecycle_status == "not_supplied"
    assert partial.lifecycle_events is None
    assert partial.report_status == "not_available"
    assert partial.trace_status == "not_available"
    assert "lifecycle=not_supplied" in partial.summary()
    assert "lifecycle_events=none" in partial.summary()

    complete = verify_portable_evidence(
        manifest_path,
        lifecycle_path=lifecycle_path,
    )
    assert complete.lifecycle_status == "verified"
    assert complete.lifecycle_events == lifecycle.event_count
    assert "lifecycle=verified" in complete.summary()
    assert f"lifecycle_events={lifecycle.event_count}" in complete.summary()


def test_lifecycle_metadata_must_match_manifest_exactly(tmp_path: Path) -> None:
    manifest_path, lifecycle_path, _, _ = _portable_pair(tmp_path)

    mutations = (
        (lambda raw: raw.__setitem__("status", "failed"), "status"),
        (lambda raw: raw.__setitem__("snapshot_revision", raw["snapshot_revision"] + 1), "snapshot revision"),
        (lambda raw: raw.__setitem__("snapshot_fingerprint", "0" * 64), "snapshot fingerprint"),
        (lambda raw: raw.__setitem__("event_count", raw["event_count"] + 1), "event count"),
        (lambda raw: raw.__setitem__("ledger_head_hash", "1" * 64), "ledger head hash"),
        (lambda raw: raw.__setitem__("ledger_head_kind", "session_failed"), "ledger head kind"),
        (lambda raw: raw.__setitem__("session_id", "app_" + "f" * 32), "different manifest session"),
    )
    original = lifecycle_path.read_bytes()
    for mutate, pattern in mutations:
        lifecycle_path.write_bytes(original)
        _rewrite(lifecycle_path, mutate)
        with pytest.raises(PortableEvidenceVerificationError, match=pattern):
            verify_portable_evidence(manifest_path, lifecycle_path=lifecycle_path)


def test_lifecycle_event_hash_is_checked_from_downloaded_stored_hash(tmp_path: Path) -> None:
    manifest_path, lifecycle_path, _, _ = _portable_pair(tmp_path)

    def tamper(raw):
        raw["events"][1]["payload"]["tampered"] = True

    _rewrite(lifecycle_path, tamper)
    with pytest.raises(PortableEvidenceVerificationError, match="event hash mismatch"):
        verify_portable_evidence(manifest_path, lifecycle_path=lifecycle_path)


def test_lifecycle_chain_rejects_previous_hash_sequence_and_final_kind_mismatch(tmp_path: Path) -> None:
    manifest_path, lifecycle_path, _, _ = _portable_pair(tmp_path)
    original = lifecycle_path.read_bytes()

    cases = (
        (lambda raw: raw["events"][1].__setitem__("previous_hash", "1" * 64), "previous hash"),
        (lambda raw: raw["events"][1].__setitem__("sequence", 99), "non-contiguous"),
        (lambda raw: raw.__setitem__("ledger_head_kind", "session_failed"), "ledger head kind"),
    )
    for mutate, pattern in cases:
        lifecycle_path.write_bytes(original)
        _rewrite(lifecycle_path, mutate)
        with pytest.raises(PortableEvidenceVerificationError, match=pattern):
            verify_portable_evidence(manifest_path, lifecycle_path=lifecycle_path)


def test_lifecycle_duplicate_keys_are_rejected_even_inside_event_payload(tmp_path: Path) -> None:
    manifest_path, lifecycle_path, _, _ = _portable_pair(tmp_path)
    text = lifecycle_path.read_text(encoding="utf-8")
    marker = '"payload":{"status":"created","output_root"'
    assert marker in text
    text = text.replace(
        marker,
        '"payload":{"duplicate":1,"duplicate":2,"status":"created","output_root"',
        1,
    )
    lifecycle_path.write_text(text, encoding="utf-8")

    with pytest.raises(PortableEvidenceVerificationError, match="lifecycle JSON contains duplicate object key"):
        verify_portable_evidence(manifest_path, lifecycle_path=lifecycle_path)


def test_lifecycle_schema_extra_field_and_nonobject_fail_closed(tmp_path: Path) -> None:
    manifest_path, lifecycle_path, _, _ = _portable_pair(tmp_path)
    original = lifecycle_path.read_bytes()

    _rewrite(lifecycle_path, lambda raw: raw.__setitem__("unexpected", True))
    with pytest.raises(PortableEvidenceVerificationError, match="app-lifecycle-ledger-export-v1"):
        verify_portable_evidence(manifest_path, lifecycle_path=lifecycle_path)

    lifecycle_path.write_bytes(b"[]\n")
    with pytest.raises(PortableEvidenceVerificationError, match="root must be an object"):
        verify_portable_evidence(manifest_path, lifecycle_path=lifecycle_path)

    lifecycle_path.write_bytes(original)
    lifecycle_path.write_bytes(b"\xff")
    with pytest.raises(PortableEvidenceVerificationError, match="not valid UTF-8"):
        verify_portable_evidence(manifest_path, lifecycle_path=lifecycle_path)


def test_lifecycle_uses_m44_path_and_size_boundary(tmp_path: Path) -> None:
    manifest_path, lifecycle_path, _, _ = _portable_pair(tmp_path)

    symlink = tmp_path / "lifecycle-link.json"
    try:
        symlink.symlink_to(lifecycle_path)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(PortableEvidenceVerificationError, match="symbolic link"):
        verify_portable_evidence(manifest_path, lifecycle_path=symlink)

    oversized = tmp_path / "oversized-lifecycle.json"
    oversized.write_bytes(b"x" * (MAX_LIFECYCLE_EXPORT_BYTES + 1))
    with pytest.raises(PortableEvidenceVerificationError, match="byte limit"):
        verify_portable_evidence(manifest_path, lifecycle_path=oversized)


def test_lifecycle_snapshot_fingerprint_is_correlated_not_recomputed_offline(tmp_path: Path) -> None:
    manifest_path, lifecycle_path, _, _ = _portable_pair(tmp_path)
    manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    lifecycle_raw = json.loads(lifecycle_path.read_text(encoding="utf-8"))

    # The portable lifecycle file deliberately contains no full request/snapshot from which the
    # fingerprint could be recomputed. It must instead match the independently self-fingerprinted
    # M43 manifest exactly.
    assert "request" not in lifecycle_raw
    assert "workspace_root" not in lifecycle_raw
    assert lifecycle_raw["snapshot_fingerprint"] == manifest_raw["lifecycle"]["snapshot_fingerprint"]
