from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

import harness_x.evidence_signing as signing
from harness_x.evidence_signing import EvidenceSigningError
from harness_x.evidence_verification import PortableEvidenceVerificationError


def test_cryptography_is_optional_for_base_install_and_present_in_signing_dev_extras() -> None:
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    extras = pyproject["project"]["optional-dependencies"]
    assert all(not item.lower().startswith("cryptography") for item in dependencies)
    assert any(item.lower().startswith("cryptography") for item in extras["evidence-signing"])
    assert any(item.lower().startswith("cryptography") for item in extras["dev"])


def test_exclusive_write_removes_new_partial_file_on_fsync_failure(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "partial.bin"

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(signing.os, "fsync", fail_fsync)
    with pytest.raises(EvidenceSigningError, match="injected fsync failure"):
        signing._exclusive_write(output, b"secret material", mode=0o600)
    assert not output.exists()


def test_keygen_cleans_private_key_when_public_creation_fails(tmp_path: Path, monkeypatch) -> None:
    private_path = tmp_path / "private.pem"
    public_path = tmp_path / "public.pem"
    original = signing._exclusive_write
    calls = 0

    def fail_second(path, payload, *, mode):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise EvidenceSigningError("injected public-key write failure")
        return original(path, payload, mode=mode)

    monkeypatch.setattr(signing, "_exclusive_write", fail_second)
    with pytest.raises(EvidenceSigningError, match="injected public-key write failure"):
        signing.generate_evidence_keypair(
            private_key_path=private_path,
            public_key_path=public_path,
        )
    assert not private_path.exists()
    assert not public_path.exists()


def test_same_key_output_path_is_rejected_without_creation(tmp_path: Path) -> None:
    same = tmp_path / "same.pem"
    with pytest.raises(EvidenceSigningError, match="must be different paths"):
        signing.generate_evidence_keypair(private_key_path=same, public_key_path=same)
    assert not same.exists()


def test_output_parent_symlink_is_rejected(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("symlink parent boundary is POSIX-qualified in CI")
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(EvidenceSigningError, match="output parent"):
        signing._exclusive_write(linked / "out.bin", b"data", mode=0o600)
    assert not (actual / "out.bin").exists()


def test_private_key_input_has_independent_small_size_limit(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.private.pem"
    oversized.write_bytes(b"x" * (signing.MAX_EVIDENCE_KEY_BYTES + 1))
    with pytest.raises(PortableEvidenceVerificationError, match="exceeds 16384 byte limit"):
        signing._load_private_key(oversized)


def test_signature_envelope_has_independent_small_size_limit(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.sig.json"
    oversized.write_bytes(b"x" * (signing.MAX_EVIDENCE_SIGNATURE_BYTES + 1))
    with pytest.raises(PortableEvidenceVerificationError, match="exceeds 65536 byte limit"):
        signing._load_signature_envelope(oversized)
