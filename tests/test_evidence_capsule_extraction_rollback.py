from __future__ import annotations

from pathlib import Path

import pytest

import harness_x.evidence_capsule_extraction as extraction_module
from harness_x.evidence_capsule_extraction import EvidenceCapsuleExtractionError
from harness_x.evidence_signing import EvidenceSigningError


def test_manifest_rollback_failure_is_visible_and_does_not_claim_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "session-evidence-manifest.json"
    manifest.write_text("partial", encoding="utf-8")

    def fail_unlink(path: str) -> None:
        assert path == str(manifest)
        raise OSError("injected rollback failure")

    monkeypatch.setattr(extraction_module.os, "unlink", fail_unlink)
    with pytest.raises(
        EvidenceCapsuleExtractionError,
        match="additionally failed to roll back newly created manifest output",
    ):
        extraction_module._rollback_created_manifest(
            str(manifest),
            EvidenceSigningError("injected signature-output failure"),
        )
    assert manifest.exists()
