from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_x.windows_distribution import (
    WindowsDistributionEntry,
    WindowsDistributionError,
    WindowsDistributionManifest,
    assemble_windows_distribution,
    build_windows_distribution_manifest,
    main,
    persist_windows_distribution_manifest,
    render_windows_distribution_manifest,
    verify_windows_distribution,
)


def _fixture_distribution(tmp_path: Path) -> Path:
    root = tmp_path / "portable"
    (root / "_internal" / "harness_x" / "app_server" / "ui").mkdir(parents=True)
    (root / "HarnessX.exe").write_bytes(b"desktop-binary")
    (root / "harness-x-app-server.exe").write_bytes(b"app-server-binary")
    (root / "_internal" / "python312.dll").write_bytes(b"python-runtime")
    (root / "_internal" / "harness_x" / "app_server" / "ui" / "index.html").write_bytes(
        b"<!doctype html>\n"
    )
    return root


def _fixture_build_roots(tmp_path: Path) -> tuple[Path, Path]:
    desktop = tmp_path / "desktop"
    desktop.mkdir(parents=True)
    (desktop / "HarnessX.exe").write_bytes(b"desktop-binary")
    (desktop / "HarnessX.dll").write_bytes(b"desktop-managed")

    app_server = tmp_path / "app-server"
    (app_server / "_internal" / "harness_x" / "app_server" / "ui").mkdir(parents=True)
    (app_server / "harness-x-app-server.exe").write_bytes(b"app-server-binary")
    (app_server / "_internal" / "python312.dll").write_bytes(b"python-runtime")
    (app_server / "_internal" / "harness_x" / "app_server" / "ui" / "index.html").write_bytes(
        b"<!doctype html>\n"
    )
    return desktop, app_server


def test_assembly_combines_exact_outputs_with_adjacent_executables(tmp_path: Path) -> None:
    desktop, app_server = _fixture_build_roots(tmp_path)
    output = assemble_windows_distribution(desktop, app_server, tmp_path / "portable")
    assert (output / "HarnessX.exe").read_bytes() == b"desktop-binary"
    assert (output / "harness-x-app-server.exe").read_bytes() == b"app-server-binary"
    assert (output / "HarnessX.dll").read_bytes() == b"desktop-managed"
    assert (output / "_internal" / "python312.dll").read_bytes() == b"python-runtime"
    assert (desktop / "HarnessX.exe").read_bytes() == b"desktop-binary"
    assert (app_server / "harness-x-app-server.exe").read_bytes() == b"app-server-binary"


def test_assembly_rejects_case_insensitive_collision_without_partial_output(tmp_path: Path) -> None:
    desktop, app_server = _fixture_build_roots(tmp_path)
    (desktop / "shared.dll").write_bytes(b"desktop")
    (app_server / "SHARED.dll").write_bytes(b"server")
    output = tmp_path / "portable"
    with pytest.raises(WindowsDistributionError, match="source collision"):
        assemble_windows_distribution(desktop, app_server, output)
    assert not output.exists()


def test_assembly_requires_both_named_executables(tmp_path: Path) -> None:
    desktop, app_server = _fixture_build_roots(tmp_path)
    (app_server / "harness-x-app-server.exe").unlink()
    output = tmp_path / "portable"
    with pytest.raises(WindowsDistributionError, match="requires adjacent"):
        assemble_windows_distribution(desktop, app_server, output)
    assert not output.exists()


def test_manifest_is_canonical_deterministic_and_verifies_exact_bytes(tmp_path: Path) -> None:
    root = _fixture_distribution(tmp_path)
    first = build_windows_distribution_manifest(root)
    second = build_windows_distribution_manifest(root)
    assert first == second
    assert first.schema_version == "windows-portable-distribution-manifest-v1"
    assert first.distribution_kind == "portable-directory"
    assert first.target_runtime == "win-x64"
    assert [item.relative_path for item in first.entries] == sorted(
        item.relative_path for item in first.entries
    )
    assert {item.relative_path for item in first.entries} == {
        "HarnessX.exe",
        "harness-x-app-server.exe",
        "_internal/python312.dll",
        "_internal/harness_x/app_server/ui/index.html",
    }

    manifest_path = persist_windows_distribution_manifest(root)
    exact = manifest_path.read_bytes()
    assert exact == render_windows_distribution_manifest(first)
    assert exact.endswith(b"\n")
    assert b"\\\\" not in exact
    assert verify_windows_distribution(root) == first


def test_manifest_inventory_has_only_bounded_packaging_fields(tmp_path: Path) -> None:
    root = _fixture_distribution(tmp_path)
    manifest = build_windows_distribution_manifest(root)
    payload = json.loads(render_windows_distribution_manifest(manifest))
    assert set(payload) == {
        "distribution_kind",
        "entries",
        "harness_x_version",
        "schema_version",
        "target_runtime",
    }
    assert set(payload["entries"][0]) == {"relative_path", "sha256", "size_bytes"}
    assert str(tmp_path).lower() not in json.dumps(payload).lower()


def test_verifier_rejects_mutated_file_bytes(tmp_path: Path) -> None:
    root = _fixture_distribution(tmp_path)
    persist_windows_distribution_manifest(root)
    (root / "HarnessX.exe").write_bytes(b"desktop-BINARY")
    with pytest.raises(WindowsDistributionError, match="digest mismatch"):
        verify_windows_distribution(root)


def test_verifier_rejects_missing_or_added_file(tmp_path: Path) -> None:
    root = _fixture_distribution(tmp_path)
    persist_windows_distribution_manifest(root)
    (root / "_internal" / "python312.dll").unlink()
    with pytest.raises(WindowsDistributionError, match="file set"):
        verify_windows_distribution(root)

    root = _fixture_distribution(tmp_path / "added")
    persist_windows_distribution_manifest(root)
    (root / "unexpected.dll").write_bytes(b"unexpected")
    with pytest.raises(WindowsDistributionError, match="file set"):
        verify_windows_distribution(root)


def test_manifest_rejects_escape_duplicate_case_collision_and_self_inventory() -> None:
    with pytest.raises(ValueError, match="relative|unsafe"):
        WindowsDistributionEntry(relative_path="../outside.exe", size_bytes=1, sha256="a" * 64)
    with pytest.raises(ValueError, match="forward slashes"):
        WindowsDistributionEntry(relative_path="dir\\file.exe", size_bytes=1, sha256="a" * 64)
    with pytest.raises(ValueError, match="inventory itself"):
        WindowsDistributionEntry(
            relative_path="harness-x-distribution-manifest.json",
            size_bytes=1,
            sha256="a" * 64,
        )

    entry = WindowsDistributionEntry(relative_path="HarnessX.exe", size_bytes=1, sha256="a" * 64)
    with pytest.raises(ValueError, match="duplicate"):
        WindowsDistributionManifest(harness_x_version="0.1", entries=(entry, entry))

    other_case = WindowsDistributionEntry(relative_path="harnessx.EXE", size_bytes=1, sha256="b" * 64)
    with pytest.raises(ValueError, match="Windows path casing"):
        WindowsDistributionManifest(harness_x_version="0.1", entries=(entry, other_case))


def test_verifier_rejects_noncanonical_or_unknown_manifest_fields(tmp_path: Path) -> None:
    root = _fixture_distribution(tmp_path)
    manifest_path = persist_windows_distribution_manifest(root)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WindowsDistributionError, match="invalid"):
        verify_windows_distribution(root)

    root = _fixture_distribution(tmp_path / "noncanonical")
    manifest_path = persist_windows_distribution_manifest(root)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(WindowsDistributionError, match="not canonical"):
        verify_windows_distribution(root)


def test_distribution_walk_rejects_symlinks(tmp_path: Path) -> None:
    root = _fixture_distribution(tmp_path)
    target = tmp_path / "target.bin"
    target.write_bytes(b"target")
    link = root / "linked.bin"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("host does not permit symlink construction")
    with pytest.raises(WindowsDistributionError, match="symlink"):
        build_windows_distribution_manifest(root)


def test_distribution_cli_assembles_builds_and_verifies(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    desktop, app_server = _fixture_build_roots(tmp_path)
    root = tmp_path / "portable"
    assert main(["assemble", str(desktop), str(app_server), str(root)]) == 0
    assert "assembled: portable" in capsys.readouterr().out
    assert main(["build", str(root)]) == 0
    assert "manifest: harness-x-distribution-manifest.json" in capsys.readouterr().out
    assert main(["verify", str(root)]) == 0
    output = capsys.readouterr().out
    assert "valid: schema=windows-portable-distribution-manifest-v1" in output
    assert "files=5" in output
