from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from harness_x.app_server.evidence_manifest import (
    build_terminal_evidence_manifest,
    render_terminal_evidence_manifest,
)
from harness_x.app_server.lifecycle_export import (
    build_lifecycle_ledger_export,
    render_lifecycle_ledger_export,
)
from harness_x.app_server.protocol import AppEventKind, AppSessionStatus, CodingSessionRequest
from harness_x.app_server.service import AppServerService
from harness_x.app_server.snapshot_export import (
    MAX_SESSION_SNAPSHOT_EXPORT_BYTES,
    canonical_snapshot_material,
    render_terminal_session_snapshot,
)
from harness_x.evidence_verification import (
    BoundedEvidenceSource,
    PortableEvidenceVerificationError,
    verify_portable_evidence,
)
from harness_x.snapshot_verification import (
    _load_snapshot,
    verify_portable_evidence_with_snapshot,
)


def _portable_set(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    service = AppServerService(tmp_path / "service")
    request = CodingSessionRequest(
        workspace_root=workspace,
        task="verify complete session fingerprint material offline",
        model_profile="main",
        verification_commands=("python -m pytest",),
        project_memory_root=workspace / ".memory",
        project_memory_key="portable/session",
        max_reasoning_steps=19,
        max_tool_actions=29,
        max_output_tokens=8192,
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
    snapshot_path = tmp_path / "session-snapshot.json"
    lifecycle_path = tmp_path / "session-lifecycle-ledger.json"
    manifest_path.write_bytes(render_terminal_evidence_manifest(manifest).payload)
    snapshot_path.write_bytes(render_terminal_session_snapshot(snapshot=snapshot).payload)
    lifecycle_path.write_bytes(render_lifecycle_ledger_export(lifecycle).payload)
    service.close()
    return manifest_path, snapshot_path, lifecycle_path, manifest, snapshot


def _rewrite_self_consistent_snapshot(path: Path, mutate) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    mutate(raw)
    material = dict(raw)
    material.pop("fingerprint", None)
    raw["fingerprint"] = hashlib.sha256(canonical_snapshot_material(material)).hexdigest()
    path.write_text(json.dumps(raw, separators=(",", ":")) + "\n", encoding="utf-8")
    return raw


def test_snapshot_is_optional_and_complete_export_is_independently_verified(tmp_path: Path) -> None:
    manifest_path, snapshot_path, lifecycle_path, manifest, snapshot = _portable_set(tmp_path)

    partial = verify_portable_evidence_with_snapshot(manifest_path)
    assert partial.base.session_id == manifest.session_id
    assert partial.snapshot_status == "not_supplied"
    assert partial.snapshot_revision is None
    assert partial.base.lifecycle_status == "not_supplied"
    assert "snapshot=not_supplied snapshot_revision=none" in partial.summary()

    complete = verify_portable_evidence_with_snapshot(
        manifest_path,
        snapshot_path=snapshot_path,
        lifecycle_path=lifecycle_path,
    )
    assert complete.snapshot_status == "verified"
    assert complete.snapshot_revision == snapshot.revision
    assert complete.base.lifecycle_status == "verified"
    assert "snapshot=verified" in complete.summary()
    assert f"snapshot_revision={snapshot.revision}" in complete.summary()
    assert "lifecycle=verified" in complete.summary()


def test_snapshot_raw_fingerprint_recompute_happens_before_portable_path_schema(tmp_path: Path) -> None:
    manifest_path, snapshot_path, _, _, _ = _portable_set(tmp_path)
    del manifest_path
    raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
    raw["request"]["workspace_root"] = "/portable/../opaque/workspace"
    raw["request"]["project_memory_root"] = "/portable/../opaque/memory"
    raw["output_root"] = "/portable/../opaque/output"
    material = dict(raw)
    material.pop("fingerprint")
    raw["fingerprint"] = hashlib.sha256(canonical_snapshot_material(material)).hexdigest()
    payload = (json.dumps(raw, separators=(",", ":")) + "\n").encode("utf-8")
    source = BoundedEvidenceSource(
        path="/not/consulted/session-snapshot.json",
        payload=payload,
        source_bytes=len(payload),
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )

    snapshot = _load_snapshot(source)
    assert snapshot.request.workspace_root == "/portable/../opaque/workspace"
    assert snapshot.request.project_memory_root == "/portable/../opaque/memory"
    assert snapshot.output_root == "/portable/../opaque/output"


def test_snapshot_rejects_tamper_duplicate_keys_and_unknown_fields(tmp_path: Path) -> None:
    manifest_path, snapshot_path, _, _, _ = _portable_set(tmp_path)
    original = snapshot_path.read_bytes()

    raw = json.loads(original)
    raw["request"]["task"] = "tampered without fingerprint update"
    snapshot_path.write_text(json.dumps(raw, separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(PortableEvidenceVerificationError, match="fingerprint"):
        verify_portable_evidence_with_snapshot(manifest_path, snapshot_path=snapshot_path)

    snapshot_path.write_bytes(original)
    text = snapshot_path.read_text(encoding="utf-8")
    marker = '"request":{"schema_version":"app-coding-session-request-v1"'
    assert marker in text
    snapshot_path.write_text(
        text.replace(
            marker,
            '"request":{"schema_version":"app-coding-session-request-v1","duplicate":1,"duplicate":2',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(PortableEvidenceVerificationError, match="duplicate object key"):
        verify_portable_evidence_with_snapshot(manifest_path, snapshot_path=snapshot_path)

    snapshot_path.write_bytes(original)
    _rewrite_self_consistent_snapshot(snapshot_path, lambda value: value.__setitem__("unexpected", True))
    with pytest.raises(PortableEvidenceVerificationError, match="app-session-snapshot-v1"):
        verify_portable_evidence_with_snapshot(manifest_path, snapshot_path=snapshot_path)

    snapshot_path.write_bytes(original)
    _rewrite_self_consistent_snapshot(
        snapshot_path,
        lambda value: value["request"].__setitem__("unexpected", True),
    )
    with pytest.raises(PortableEvidenceVerificationError, match="app-session-snapshot-v1"):
        verify_portable_evidence_with_snapshot(manifest_path, snapshot_path=snapshot_path)


def test_self_consistent_snapshot_still_must_match_manifest_metadata(tmp_path: Path) -> None:
    manifest_path, snapshot_path, _, _, snapshot = _portable_set(tmp_path)
    _rewrite_self_consistent_snapshot(
        snapshot_path,
        lambda value: value.__setitem__("revision", snapshot.revision + 1),
    )
    with pytest.raises(PortableEvidenceVerificationError, match="revision"):
        verify_portable_evidence_with_snapshot(manifest_path, snapshot_path=snapshot_path)


def test_snapshot_uses_existing_bounded_regular_file_boundary(tmp_path: Path) -> None:
    manifest_path, snapshot_path, _, _, _ = _portable_set(tmp_path)
    link = tmp_path / "snapshot-link.json"
    try:
        link.symlink_to(snapshot_path)
    except OSError:
        pytest.skip("symbolic links are unavailable on this platform")
    with pytest.raises(PortableEvidenceVerificationError, match="symbolic link"):
        verify_portable_evidence_with_snapshot(manifest_path, snapshot_path=link)

    oversized = tmp_path / "oversized-snapshot.json"
    oversized.write_bytes(b"x" * (MAX_SESSION_SNAPSHOT_EXPORT_BYTES + 1))
    with pytest.raises(PortableEvidenceVerificationError, match="byte limit"):
        verify_portable_evidence_with_snapshot(manifest_path, snapshot_path=oversized)


def test_combined_result_rejects_manifest_identity_change_between_verifier_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, snapshot_path, _, _, _ = _portable_set(tmp_path)
    base = verify_portable_evidence(manifest_path)

    import harness_x.snapshot_verification as module

    monkeypatch.setattr(
        module,
        "verify_portable_evidence",
        lambda *args, **kwargs: dataclasses.replace(base, manifest_sha256="0" * 64),
    )
    with pytest.raises(PortableEvidenceVerificationError, match="changed between"):
        verify_portable_evidence_with_snapshot(manifest_path, snapshot_path=snapshot_path)


def test_snapshot_cli_parser_and_summary_expose_optional_snapshot(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path, snapshot_path, _, _, snapshot = _portable_set(tmp_path)
    from harness_x.cli_entry import build_parser, main

    parser = build_parser()
    args = parser.parse_args(["verify-evidence", str(manifest_path), "--snapshot", str(snapshot_path)])
    assert args.snapshot == snapshot_path

    assert main(["verify-evidence", str(manifest_path)]) == 0
    omitted = capsys.readouterr().out.strip()
    assert omitted.startswith("valid:")
    assert "snapshot=not_supplied snapshot_revision=none" in omitted

    assert main(["verify-evidence", str(manifest_path), "--snapshot", str(snapshot_path)]) == 0
    supplied = capsys.readouterr().out.strip()
    assert supplied.startswith("valid:")
    assert f"snapshot=verified snapshot_revision={snapshot.revision}" in supplied
