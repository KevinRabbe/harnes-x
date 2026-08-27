"""Deterministic construction and integrity inventory for the M77 Windows distribution.

The distribution manifest is a packaging inventory only. It is deliberately separate from
Harness X evidence manifests, signatures, verification receipts, and runtime authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from harness_x import __version__

_STRICT = ConfigDict(frozen=True, extra="forbid")
_MANIFEST_NAME = "harness-x-distribution-manifest.json"
_SCHEMA_VERSION = "windows-portable-distribution-manifest-v1"
_MAX_FILES = 16_384
_MAX_DEPTH = 24
_MAX_RELATIVE_PATH = 512
_MAX_FILE_BYTES = 1024 * 1024 * 1024
_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024


class WindowsDistributionError(RuntimeError):
    """Portable-distribution construction or verification failed."""


class WindowsDistributionEntry(BaseModel):
    model_config = _STRICT

    relative_path: str = Field(min_length=1, max_length=_MAX_RELATIVE_PATH)
    size_bytes: int = Field(ge=0, le=_MAX_FILE_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_relative_path(self) -> "WindowsDistributionEntry":
        _require_relative_path(self.relative_path)
        if self.relative_path == _MANIFEST_NAME:
            raise ValueError("distribution manifest cannot inventory itself")
        return self


class WindowsDistributionManifest(BaseModel):
    model_config = _STRICT

    schema_version: Literal["windows-portable-distribution-manifest-v1"] = _SCHEMA_VERSION
    harness_x_version: str = Field(min_length=1, max_length=128)
    distribution_kind: Literal["portable-directory"] = "portable-directory"
    target_runtime: Literal["win-x64"] = "win-x64"
    entries: tuple[WindowsDistributionEntry, ...] = Field(max_length=_MAX_FILES)

    @model_validator(mode="after")
    def validate_entry_order(self) -> "WindowsDistributionManifest":
        paths = tuple(item.relative_path for item in self.entries)
        if paths != tuple(sorted(paths)):
            raise ValueError("distribution entries must be sorted by relative path")
        if len(paths) != len(set(paths)):
            raise ValueError("distribution entries contain duplicate relative paths")
        if len({item.casefold() for item in paths}) != len(paths):
            raise ValueError("distribution entries collide under Windows path casing")
        total = sum(item.size_bytes for item in self.entries)
        if total > _MAX_TOTAL_BYTES:
            raise ValueError("distribution exceeds aggregate byte limit")
        return self


def _require_relative_path(value: str) -> str:
    if "\\" in value:
        raise ValueError("distribution paths must use forward slashes")
    path = Path(value)
    if path.is_absolute() or value.startswith("/"):
        raise ValueError("distribution path must be relative")
    parts = value.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("distribution path contains an unsafe component")
    normalized = Path(*parts).as_posix()
    if normalized != value:
        raise ValueError("distribution path is not canonical")
    return value


def _require_directory(root: str | Path, *, label: str) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink():
        raise WindowsDistributionError(f"{label} must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise WindowsDistributionError(f"{label} is unavailable: {exc}") from exc
    if not resolved.is_dir():
        raise WindowsDistributionError(f"{label} must be a directory")
    return resolved


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _sha256_file(path: Path, *, expected_size: int | None = None) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_FILE_BYTES:
                raise WindowsDistributionError(f"distribution file exceeds byte limit: {path.name}")
            digest.update(chunk)
    if expected_size is not None and size != expected_size:
        raise WindowsDistributionError(f"distribution file size changed while hashing: {path.name}")
    return size, digest.hexdigest()


def _walk_regular_files(root: Path, *, include_manifest: bool = False) -> tuple[Path, ...]:
    pending: list[tuple[Path, int]] = [(root, 0)]
    files: list[Path] = []
    while pending:
        directory, depth = pending.pop()
        if depth > _MAX_DEPTH:
            raise WindowsDistributionError("distribution tree exceeds depth limit")
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise WindowsDistributionError(f"cannot enumerate distribution directory: {exc}") from exc
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink():
                raise WindowsDistributionError(f"distribution contains a symlink: {path.name}")
            if entry.is_dir(follow_symlinks=False):
                pending.append((path, depth + 1))
                continue
            if not entry.is_file(follow_symlinks=False):
                raise WindowsDistributionError(f"distribution contains a non-regular file: {path.name}")
            relative = path.relative_to(root).as_posix()
            _require_relative_path(relative)
            if relative == _MANIFEST_NAME and not include_manifest:
                continue
            files.append(path)
            if len(files) > _MAX_FILES:
                raise WindowsDistributionError("distribution exceeds file-count limit")
    return tuple(sorted(files, key=lambda item: item.relative_to(root).as_posix()))


def assemble_windows_distribution(
    desktop_root: str | Path,
    app_server_root: str | Path,
    output_root: str | Path,
) -> Path:
    """Combine exact desktop/App Server build outputs without following or overwriting paths."""

    desktop = _require_directory(desktop_root, label="desktop publish root")
    app_server = _require_directory(app_server_root, label="App Server package root")
    sources: dict[str, Path] = {}
    source_keys: dict[str, str] = {}

    for label, root in (("desktop", desktop), ("App Server", app_server)):
        for path in _walk_regular_files(root, include_manifest=True):
            relative = path.relative_to(root).as_posix()
            if relative == _MANIFEST_NAME:
                raise WindowsDistributionError(f"{label} source contains reserved distribution manifest")
            key = relative.casefold()
            prior = source_keys.get(key)
            if prior is not None:
                raise WindowsDistributionError(
                    f"portable distribution source collision: {prior} vs {relative}"
                )
            source_keys[key] = relative
            sources[relative] = path

    file_keys = set(source_keys)
    for relative in sources:
        parts = relative.casefold().split("/")
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in file_keys:
                raise WindowsDistributionError(
                    f"portable distribution file/directory collision: {relative}"
                )

    output = Path(output_root).expanduser()
    if output.exists() or output.is_symlink():
        raise WindowsDistributionError("portable distribution output must not already exist")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output_resolved = output.resolve(strict=False)
    except OSError as exc:
        raise WindowsDistributionError(f"cannot prepare portable distribution output: {exc}") from exc
    if _inside(desktop, output_resolved) or _inside(app_server, output_resolved):
        raise WindowsDistributionError("portable distribution output must be outside build input roots")

    try:
        output.mkdir()
        for relative, source in sorted(sources.items()):
            target = output / Path(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise WindowsDistributionError(f"portable distribution target collision: {relative}")
            before = source.stat(follow_symlinks=False)
            if before.st_size > _MAX_FILE_BYTES:
                raise WindowsDistributionError(f"distribution file exceeds byte limit: {relative}")
            shutil.copy2(source, target, follow_symlinks=False)
            if target.stat(follow_symlinks=False).st_size != before.st_size:
                raise WindowsDistributionError(f"distribution copy size mismatch: {relative}")
    except (OSError, WindowsDistributionError) as exc:
        try:
            shutil.rmtree(output)
        except OSError:
            pass
        if isinstance(exc, WindowsDistributionError):
            raise
        raise WindowsDistributionError(f"cannot assemble portable distribution: {exc}") from exc

    required = (output / "HarnessX.exe", output / "harness-x-app-server.exe")
    if not all(path.is_file() and not path.is_symlink() for path in required):
        try:
            shutil.rmtree(output)
        except OSError:
            pass
        raise WindowsDistributionError(
            "portable distribution requires adjacent HarnessX.exe and harness-x-app-server.exe"
        )
    return output


def build_windows_distribution_manifest(root: str | Path) -> WindowsDistributionManifest:
    """Hash one bounded ordinary directory without following symlinks."""

    root_path = _require_directory(root, label="distribution root")
    entries: list[WindowsDistributionEntry] = []
    total = 0
    for path in _walk_regular_files(root_path):
        try:
            stat = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise WindowsDistributionError(f"cannot stat distribution file: {path.name}") from exc
        if stat.st_size > _MAX_FILE_BYTES:
            raise WindowsDistributionError(f"distribution file exceeds byte limit: {path.name}")
        size, digest = _sha256_file(path, expected_size=stat.st_size)
        total += size
        if total > _MAX_TOTAL_BYTES:
            raise WindowsDistributionError("distribution exceeds aggregate byte limit")
        entries.append(
            WindowsDistributionEntry(
                relative_path=path.relative_to(root_path).as_posix(),
                size_bytes=size,
                sha256=digest,
            )
        )
    return WindowsDistributionManifest(
        harness_x_version=__version__,
        entries=tuple(entries),
    )


def render_windows_distribution_manifest(manifest: WindowsDistributionManifest) -> bytes:
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return (payload + "\n").encode("utf-8")


def persist_windows_distribution_manifest(root: str | Path) -> Path:
    root_path = _require_directory(root, label="distribution root")
    manifest_path = root_path / _MANIFEST_NAME
    if manifest_path.exists() or manifest_path.is_symlink():
        raise WindowsDistributionError("distribution manifest already exists")
    manifest = build_windows_distribution_manifest(root_path)
    data = render_windows_distribution_manifest(manifest)
    if len(data) > _MAX_MANIFEST_BYTES:
        raise WindowsDistributionError("distribution manifest exceeds byte limit")
    try:
        with manifest_path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        try:
            manifest_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise WindowsDistributionError(f"cannot persist distribution manifest: {exc}") from exc
    return manifest_path


def verify_windows_distribution(root: str | Path) -> WindowsDistributionManifest:
    root_path = _require_directory(root, label="distribution root")
    manifest_path = root_path / _MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise WindowsDistributionError("distribution manifest is missing or non-regular")
    try:
        data = manifest_path.read_bytes()
    except OSError as exc:
        raise WindowsDistributionError(f"cannot read distribution manifest: {exc}") from exc
    if len(data) > _MAX_MANIFEST_BYTES:
        raise WindowsDistributionError("distribution manifest exceeds byte limit")
    try:
        manifest = WindowsDistributionManifest.model_validate_json(data)
    except ValidationError as exc:
        raise WindowsDistributionError(f"distribution manifest is invalid: {exc}") from exc
    if manifest.harness_x_version != __version__:
        raise WindowsDistributionError("distribution manifest software version mismatch")
    if data != render_windows_distribution_manifest(manifest):
        raise WindowsDistributionError("distribution manifest is not canonical")

    actual = build_windows_distribution_manifest(root_path)
    if actual != manifest:
        expected = {item.relative_path: item for item in manifest.entries}
        observed = {item.relative_path: item for item in actual.entries}
        if expected.keys() != observed.keys():
            raise WindowsDistributionError("distribution file set does not match manifest")
        for relative_path in sorted(expected):
            if expected[relative_path].size_bytes != observed[relative_path].size_bytes:
                raise WindowsDistributionError(f"distribution size mismatch: {relative_path}")
            if expected[relative_path].sha256 != observed[relative_path].sha256:
                raise WindowsDistributionError(f"distribution digest mismatch: {relative_path}")
        raise WindowsDistributionError("distribution manifest mismatch")
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build, verify, or assemble the M77 Windows distribution")
    subparsers = parser.add_subparsers(dest="command", required=True)

    assemble = subparsers.add_parser("assemble")
    assemble.add_argument("desktop_root", type=Path)
    assemble.add_argument("app_server_root", type=Path)
    assemble.add_argument("output_root", type=Path)

    for command in ("build", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("root", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "assemble":
            output = assemble_windows_distribution(
                args.desktop_root,
                args.app_server_root,
                args.output_root,
            )
            print(f"assembled: {output.name}")
        elif args.command == "build":
            path = persist_windows_distribution_manifest(args.root)
            print(f"manifest: {path.name}")
        else:
            manifest = verify_windows_distribution(args.root)
            print(
                f"valid: schema={manifest.schema_version} files={len(manifest.entries)} "
                f"version={manifest.harness_x_version}"
            )
        return 0
    except WindowsDistributionError as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "WindowsDistributionEntry",
    "WindowsDistributionError",
    "WindowsDistributionManifest",
    "assemble_windows_distribution",
    "build_windows_distribution_manifest",
    "persist_windows_distribution_manifest",
    "render_windows_distribution_manifest",
    "verify_windows_distribution",
]
