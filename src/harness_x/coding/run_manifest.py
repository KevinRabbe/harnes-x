"""Pre-run provenance required for strict offline profile comparison.

The final coding report proves what happened. This manifest records the operator-controlled
starting conditions that are otherwise lost after project memory mutates during a run.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .model_selection import ResolvedModelSelection


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def directory_fingerprint(path: str | Path) -> str:
    """Return an exact hash of one persistent directory without following symlinks.

    A missing directory is a meaningful empty starting state. Symlinks are rejected because
    comparison provenance must not depend on mutable files outside the declared memory root.
    """

    root = Path(path).resolve()
    if not root.exists():
        return hashlib.sha256(_canonical([])).hexdigest()
    if not root.is_dir():
        raise ValueError(f"comparison fingerprint target is not a directory: {root}")

    rows: list[tuple[str, str, int]] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        base = Path(current)
        for name in tuple(dirs):
            child = base / name
            if child.is_symlink():
                raise ValueError(
                    f"comparison memory fingerprint rejects directory symlink: {child}"
                )
        for name in sorted(names):
            target = base / name
            if target.is_symlink():
                raise ValueError(
                    f"comparison memory fingerprint rejects file symlink: {target}"
                )
            if not target.is_file():
                continue
            digest = hashlib.sha256()
            size = 0
            with target.open("rb") as handle:
                while True:
                    block = handle.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    digest.update(block)
            rows.append((target.relative_to(root).as_posix(), digest.hexdigest(), size))
    rows.sort()
    return hashlib.sha256(_canonical(rows)).hexdigest()


def clone_comparison_memory_seed(seed_root: str | Path, target_root: str | Path) -> str:
    """Copy one exact project-memory seed into a fresh comparison-owned root.

    The target must not already contain state. This prevents an evaluation run from silently
    combining a baseline snapshot with evidence from an earlier profile run.
    """

    seed = Path(seed_root).resolve()
    target = Path(target_root).resolve()
    if not seed.is_dir():
        raise ValueError(f"comparison memory seed must be an existing directory: {seed}")
    seed_fingerprint = directory_fingerprint(seed)
    if target.exists():
        if not target.is_dir():
            raise ValueError(f"comparison memory target is not a directory: {target}")
        if any(target.iterdir()):
            raise ValueError(
                f"comparison memory target must be absent or empty before seeding: {target}"
            )
    else:
        target.mkdir(parents=True, exist_ok=False)

    for current, dirs, names in os.walk(seed, followlinks=False):
        base = Path(current)
        relative = base.relative_to(seed)
        destination = target / relative
        destination.mkdir(parents=True, exist_ok=True)
        for name in tuple(dirs):
            child = base / name
            if child.is_symlink():
                raise ValueError(
                    f"comparison memory seed rejects directory symlink: {child}"
                )
        for name in sorted(names):
            source = base / name
            if source.is_symlink():
                raise ValueError(f"comparison memory seed rejects file symlink: {source}")
            if source.is_file():
                shutil.copy2(source, destination / name)

    copied_fingerprint = directory_fingerprint(target)
    if copied_fingerprint != seed_fingerprint:
        raise RuntimeError("comparison memory seed copy fingerprint mismatch")
    return copied_fingerprint


class CodingRunManifest(BaseModel):
    """Secret-free immutable starting conditions for one coding run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["coding-run-manifest-v1"] = "coding-run-manifest-v1"
    task: str = Field(min_length=1)
    workspace_root: str
    output_root: str
    isolated: bool
    verification_plan_fingerprint: str = Field(min_length=64, max_length=64)
    browser_verification_plan_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    project_memory_root: str
    project_memory_key: str
    starting_project_memory_fingerprint: str = Field(min_length=64, max_length=64)
    model_selection: ResolvedModelSelection
    max_reasoning_steps: int = Field(ge=1)
    max_tool_actions: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    baseline_verification: bool
    max_idle_turns: int = Field(ge=1)
    max_inspection_streak: int = Field(ge=1)
    max_no_progress_streak: int = Field(ge=1)
    max_same_failure_count: int = Field(ge=1)
    isolation_retention: str | None = None
    isolation_support_paths: tuple[str, ...] = ()
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "CodingRunManifest":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", hashlib.sha256(_canonical(material)).hexdigest())
        return self


def build_coding_run_manifest(
    *,
    task: str,
    workspace_root: str | Path,
    output_root: str | Path,
    isolated: bool,
    verification_plan_fingerprint: str,
    browser_verification_plan_fingerprint: str | None,
    project_memory_root: str | Path | None,
    project_memory_key: str | None,
    model_selection: ResolvedModelSelection,
    max_reasoning_steps: int,
    max_tool_actions: int,
    max_output_tokens: int,
    baseline_verification: bool,
    max_idle_turns: int,
    max_inspection_streak: int,
    max_no_progress_streak: int,
    max_same_failure_count: int,
    isolation_retention: str | None,
    isolation_support_paths: tuple[str, ...] = (),
) -> CodingRunManifest:
    workspace = Path(workspace_root).resolve()
    output = Path(output_root).resolve()
    memory_root = (
        Path(project_memory_root).resolve()
        if project_memory_root is not None
        else workspace / ".harness-x" / "project-memory"
    )
    key = (project_memory_key or str(workspace)).strip()
    if not key:
        raise ValueError("project memory key cannot be blank")
    normalized_task = task.strip()
    if not normalized_task:
        raise ValueError("coding run manifest task cannot be blank")
    return CodingRunManifest(
        task=normalized_task,
        workspace_root=str(workspace),
        output_root=str(output),
        isolated=isolated,
        verification_plan_fingerprint=verification_plan_fingerprint,
        browser_verification_plan_fingerprint=browser_verification_plan_fingerprint,
        project_memory_root=str(memory_root),
        project_memory_key=key,
        starting_project_memory_fingerprint=directory_fingerprint(memory_root),
        model_selection=model_selection,
        max_reasoning_steps=max_reasoning_steps,
        max_tool_actions=max_tool_actions,
        max_output_tokens=max_output_tokens,
        baseline_verification=baseline_verification,
        max_idle_turns=max_idle_turns,
        max_inspection_streak=max_inspection_streak,
        max_no_progress_streak=max_no_progress_streak,
        max_same_failure_count=max_same_failure_count,
        isolation_retention=isolation_retention,
        isolation_support_paths=tuple(isolation_support_paths),
    )


def write_coding_run_manifest(manifest: CodingRunManifest, output_root: str | Path) -> Path:
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "coding-run-manifest.json"
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_coding_run_manifest(path: str | Path) -> CodingRunManifest:
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load coding run manifest {target}: {exc}") from exc
    stored = str(raw.get("fingerprint", ""))
    manifest = CodingRunManifest.model_validate(raw)
    if stored != manifest.fingerprint:
        raise ValueError(f"coding run manifest fingerprint mismatch: {target}")
    return manifest
