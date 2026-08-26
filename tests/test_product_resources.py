from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from harness_x.product import ProjectChatStore, ProjectResourceStore


def _new_store(tmp_path: Path) -> tuple[ProjectChatStore, ProjectResourceStore, Path, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    product = ProjectChatStore(tmp_path / "state")
    project = product.create_project(name="Resources", workspace_root=workspace)
    return product, ProjectResourceStore(product), workspace, project.project_id


def _attachment_paths(product: ProjectChatStore, project_id: str, attachment_id: str) -> tuple[Path, Path]:
    root = product.projects_root / project_id / "resources" / "attachments"
    return (
        root / "blobs" / f"{attachment_id}.blob",
        root / "metadata" / f"{attachment_id}.json",
    )


def _snapshot_paths(product: ProjectChatStore, project_id: str, snapshot_id: str) -> tuple[Path, Path]:
    root = product.projects_root / project_id / "resources" / "workspace-snapshots"
    return (
        root / "blobs" / f"{snapshot_id}.blob",
        root / "metadata" / f"{snapshot_id}.json",
    )


def test_attachment_is_immutable_digest_verified_restart_durable_and_readable_when_archived(
    tmp_path: Path,
) -> None:
    product, resources, _, project_id = _new_store(tmp_path)
    record = resources.create_attachment(
        project_id,
        filename=" notes.txt ",
        media_type="Text/Plain",
        data=b"immutable attachment\n",
    )
    assert record.project_id == project_id
    assert record.filename == "notes.txt"
    assert record.media_type == "text/plain"
    assert record.text_encoding == "utf-8"
    assert resources.attachment_bytes(project_id, record.attachment_id) == b"immutable attachment\n"

    restarted_product = ProjectChatStore(product.root)
    restarted = ProjectResourceStore(restarted_product)
    assert restarted.attachment(project_id, record.attachment_id) == record
    assert restarted.attachment_bytes(project_id, record.attachment_id) == b"immutable attachment\n"

    restarted_product.archive_project(project_id)
    assert restarted.attachment_bytes(project_id, record.attachment_id) == b"immutable attachment\n"
    with pytest.raises(ValueError, match="archived project"):
        restarted.create_attachment(project_id, filename="later.txt", data=b"blocked")


@pytest.mark.parametrize(
    "filename",
    ["", "   ", ".", "..", "../escape.txt", "folder/name.txt", r"folder\name.txt", "bad\x00name"],
)
def test_attachment_display_filename_tricks_are_rejected(tmp_path: Path, filename: str) -> None:
    _, resources, _, project_id = _new_store(tmp_path)
    with pytest.raises(ValueError):
        resources.create_attachment(project_id, filename=filename, data=b"x")


def test_attachment_size_type_and_cross_project_ownership_fail_closed(tmp_path: Path) -> None:
    product, resources, _, project_id = _new_store(tmp_path)
    with pytest.raises(TypeError, match="must be bytes"):
        resources.create_attachment(project_id, filename="bad.txt", data=bytearray(b"x"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="byte limit"):
        resources.create_attachment(project_id, filename="large.bin", data=b"x" * (8 * 1024 * 1024 + 1))

    record = resources.create_attachment(project_id, filename="owned.txt", data=b"owned")
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    other = product.create_project(name="Other", workspace_root=other_workspace)
    source_blob, source_metadata = _attachment_paths(product, project_id, record.attachment_id)
    target_blob, target_metadata = _attachment_paths(product, other.project_id, record.attachment_id)
    target_blob.parent.mkdir(parents=True, exist_ok=True)
    target_metadata.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_blob, target_blob)
    shutil.copy2(source_metadata, target_metadata)
    with pytest.raises(ValueError, match="owner identity mismatch"):
        resources.attachment(other.project_id, record.attachment_id)


def test_attachment_metadata_and_blob_corruption_fail_closed(tmp_path: Path) -> None:
    product, resources, _, project_id = _new_store(tmp_path)
    record = resources.create_attachment(project_id, filename="safe.txt", data=b"abcd")
    blob, metadata = _attachment_paths(product, project_id, record.attachment_id)

    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["filename"] = "tampered.txt"
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        resources.attachment(project_id, record.attachment_id)

    metadata.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    blob.write_bytes(b"wxyz")
    with pytest.raises(ValueError, match="digest mismatch"):
        resources.attachment_bytes(project_id, record.attachment_id)


def test_workspace_snapshot_is_relative_bounded_text_typed_and_immutable(tmp_path: Path) -> None:
    product, resources, workspace, project_id = _new_store(tmp_path)
    source = workspace / "src" / "module.py"
    source.parent.mkdir()
    source.write_bytes(b"print('before')\n")

    record = resources.snapshot_workspace_file(project_id, source_path="./src/module.py")
    assert record.source_path == "src/module.py"
    assert record.text_encoding == "utf-8"
    assert resources.workspace_file_bytes(project_id, record.snapshot_id) == b"print('before')\n"

    source.write_bytes(b"print('after')\n")
    assert resources.workspace_file_bytes(project_id, record.snapshot_id) == b"print('before')\n"
    assert ProjectResourceStore(ProjectChatStore(product.root)).workspace_file_snapshot(
        project_id, record.snapshot_id
    ) == record

    binary = workspace / "raw.bin"
    binary.write_bytes(b"\xff\x00\x01")
    binary_record = resources.snapshot_workspace_file(project_id, source_path="raw.bin")
    assert binary_record.text_encoding is None
    assert resources.workspace_file_bytes(project_id, binary_record.snapshot_id) == b"\xff\x00\x01"


@pytest.mark.parametrize(
    "source_path",
    [
        "",
        ".",
        "..",
        "../escape.txt",
        "a/../escape.txt",
        "/absolute.txt",
        r"C:\absolute.txt",
        "dir\\file.txt",
        "dir/CON.txt",
        "dir/name.",
        "dir/name ",
    ],
)
def test_workspace_snapshot_rejects_unsafe_path_syntax(tmp_path: Path, source_path: str) -> None:
    _, resources, _, project_id = _new_store(tmp_path)
    with pytest.raises(ValueError):
        resources.snapshot_workspace_file(project_id, source_path=source_path)


def test_workspace_snapshot_rejects_directory_oversize_and_symlink_escape(tmp_path: Path) -> None:
    _, resources, workspace, project_id = _new_store(tmp_path)
    directory = workspace / "directory"
    directory.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        resources.snapshot_workspace_file(project_id, source_path="directory")

    large = workspace / "large.txt"
    large.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="byte limit"):
        resources.snapshot_workspace_file(project_id, source_path="large.txt")

    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = workspace / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("host does not permit symlink creation")
    with pytest.raises(ValueError, match="symlinks"):
        resources.snapshot_workspace_file(project_id, source_path="link.txt")


def test_workspace_snapshot_mutation_and_storage_corruption_fail_closed(tmp_path: Path) -> None:
    product, resources, workspace, project_id = _new_store(tmp_path)
    source = workspace / "input.txt"
    source.write_text("snapshot", encoding="utf-8")
    record = resources.snapshot_workspace_file(project_id, source_path="input.txt")
    blob, metadata = _snapshot_paths(product, project_id, record.snapshot_id)

    product.archive_project(project_id)
    with pytest.raises(ValueError, match="archived project"):
        resources.snapshot_workspace_file(project_id, source_path="input.txt")
    assert resources.workspace_file_bytes(project_id, record.snapshot_id) == b"snapshot"

    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["source_path"] = "other.txt"
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        resources.workspace_file_snapshot(project_id, record.snapshot_id)

    metadata.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    blob.write_bytes(b"SNAPSHOT")
    with pytest.raises(ValueError, match="digest mismatch"):
        resources.workspace_file_bytes(project_id, record.snapshot_id)


def test_resource_writes_fsync_blob_and_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import harness_x.product.resources as resource_module

    calls: list[int] = []
    real_fsync = resource_module.os.fsync

    def tracking_fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(resource_module.os, "fsync", tracking_fsync)
    product, resources, workspace, project_id = _new_store(tmp_path)
    resources.create_attachment(project_id, filename="one.txt", data=b"one")
    (workspace / "two.txt").write_text("two", encoding="utf-8")
    resources.snapshot_workspace_file(project_id, source_path="two.txt")
    assert len(calls) >= 4

    resource_root = product.projects_root / project_id / "resources"
    assert not list(resource_root.rglob("*.tmp"))
