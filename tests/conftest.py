from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _windows_symlink_privilege_guard(monkeypatch: pytest.MonkeyPatch):
    """Skip adversarial symlink fixtures only when Windows denies link creation itself."""

    if os.name != "nt":
        yield
        return

    original = Path.symlink_to

    def guarded_symlink_to(
        path: Path,
        target: str | os.PathLike[str],
        target_is_directory: bool = False,
    ) -> None:
        try:
            original(path, target, target_is_directory=target_is_directory)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip(
                    "Windows denied symlink fixture creation; enable Developer Mode or run "
                    "with symlink privilege to exercise this adversarial boundary locally"
                )
            raise

    monkeypatch.setattr(Path, "symlink_to", guarded_symlink_to)
    yield
