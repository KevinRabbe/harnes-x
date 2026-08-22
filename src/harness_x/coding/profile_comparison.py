"""Offline, evidence-preserving comparison of two independently executed coding runs.

M33 does not choose models, launch model servers, vote, or route future work. It compares two
existing Harness X run artifact roots and makes comparability explicit before exposing deltas.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .model_selection import ResolvedModelSelection
from .run_manifest import CodingRunManifest, load_coding_run_manifest


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load comparison artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"comparison artifact must contain a JSON object: {path}")
    return value


def _required_dict(value: object, *, field: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"comparison artifact {path} is missing object field {field!r}")
    return value


def _required_text(value: object, *, field: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"comparison artifact {path} is missing text field {field!r}")
    return value


def _latest_run(rows: object) -> dict[str, Any] | None:
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[-1]
    return row if isinstance(row, dict) else None


class ComparableRunSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_root: str
    manifest_path: str
    coding_report_path: str
    model_selection_path: str
    manifest_fingerprint: str = Field(min_length=64, max_length=64)
    selection: ResolvedModelSelection
    report_schema_version: str
    succeeded: bool
    status: str
    task: str
    source_fingerprint: str = Field(min_length=64, max_length=64)
    source_head_sha: str | None = None
    verification_plan_fingerprint: str = Field(min_length=64, max_length=64)
    browser_verification_plan_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    starting_project_memory_fingerprint: str = Field(min_length=64, max_length=64)
    reasoning_steps: int = Field(ge=0)
    tool_actions: int = Field(ge=0)
    verification_attempts: int = Field(ge=0)
    final_coding_phase: str | None = None
    pending_commitments: int = Field(default=0, ge=0)
    failure_reason: str | None = None
    changed_files: tuple[str, ...] = ()
    latest_code_verification_fingerprint: str | None = None
    latest_code_verdict: str | None = None
    latest_browser_verification_fingerprint: str | None = None
    latest_browser_verdict: str | None = None


class ProfileRunMetricDeltas(BaseModel):
    """Right-minus-left descriptive deltas. They are not a score or ranking."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reasoning_steps: int
    tool_actions: int
    verification_attempts: int
    changed_file_count: int


class ProfileRunComparisonReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["profile-run-comparison-v1"] = "profile-run-comparison-v1"
    comparison_id: str
    strictly_comparable: bool
    incompatibilities: tuple[str, ...] = ()
    left: ComparableRunSummary
    right: ComparableRunSummary
    outcome_relation: Literal[
        "both_succeeded", "left_only_succeeded", "right_only_succeeded", "both_failed"
    ]
    metric_deltas_right_minus_left: ProfileRunMetricDeltas
    changed_files_only_left: tuple[str, ...] = ()
    changed_files_only_right: tuple[str, ...] = ()
    changed_files_both: tuple[str, ...] = ()
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_fingerprint(self) -> "ProfileRunComparisonReport":
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", hashlib.sha256(_canonical(material)).hexdigest())
        return self


def _selection_from_artifact(path: Path) -> ResolvedModelSelection:
    raw = _load_json(path)
    return ResolvedModelSelection.model_validate(raw)


def load_comparable_run(run_root: str | Path) -> tuple[ComparableRunSummary, CodingRunManifest]:
    root = Path(run_root).resolve()
    manifest_path = root / "coding-run-manifest.json"
    report_path = root / "coding-task-report.json"
    selection_path = root / "model-selection.json"
    manifest = load_coding_run_manifest(manifest_path)
    report = _load_json(report_path)
    selection = _selection_from_artifact(selection_path)

    # A run root is internally inconsistent before it is compared to anything else if the
    # independently persisted M32 selection artifact disagrees with the pre-run manifest.
    if selection != manifest.model_selection:
        raise ValueError(
            f"model selection artifact disagrees with coding run manifest: {root}"
        )
    task = _required_text(report.get("task"), field="task", path=report_path)
    if task != manifest.task:
        raise ValueError(f"coding report task disagrees with run manifest: {root}")

    verification_plan = _required_dict(
        report.get("verification_plan"), field="verification_plan", path=report_path
    )
    verification_fp = _required_text(
        verification_plan.get("fingerprint"),
        field="verification_plan.fingerprint",
        path=report_path,
    )
    if verification_fp != manifest.verification_plan_fingerprint:
        raise ValueError(f"coding report verification plan disagrees with run manifest: {root}")

    isolation = _required_dict(report.get("isolation"), field="isolation", path=report_path)
    source = _required_dict(isolation.get("source"), field="isolation.source", path=report_path)
    source_fp = _required_text(
        source.get("fingerprint"), field="isolation.source.fingerprint", path=report_path
    )

    browser_plan_raw = report.get("browser_verification_plan")
    browser_fp: str | None = None
    if browser_plan_raw is not None:
        browser_plan = _required_dict(
            browser_plan_raw, field="browser_verification_plan", path=report_path
        )
        browser_fp = _required_text(
            browser_plan.get("fingerprint"),
            field="browser_verification_plan.fingerprint",
            path=report_path,
        )
    if browser_fp != manifest.browser_verification_plan_fingerprint:
        raise ValueError(f"coding report browser plan disagrees with run manifest: {root}")

    code_run = _latest_run(report.get("verification_runs"))
    browser_run = _latest_run(report.get("browser_verification_runs"))
    raw_changes = isolation.get("changes", [])
    changed_files = tuple(
        sorted(
            str(item.get("path"))
            for item in raw_changes
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        )
    )

    summary = ComparableRunSummary(
        run_root=str(root),
        manifest_path=str(manifest_path),
        coding_report_path=str(report_path),
        model_selection_path=str(selection_path),
        manifest_fingerprint=manifest.fingerprint,
        selection=selection,
        report_schema_version=str(report.get("schema_version", "unknown")),
        succeeded=bool(report.get("succeeded", False)),
        status=str(report.get("status", "unknown")),
        task=task,
        source_fingerprint=source_fp,
        source_head_sha=(str(source["head_sha"]) if source.get("head_sha") else None),
        verification_plan_fingerprint=verification_fp,
        browser_verification_plan_fingerprint=browser_fp,
        starting_project_memory_fingerprint=manifest.starting_project_memory_fingerprint,
        reasoning_steps=int(report.get("reasoning_steps", 0)),
        tool_actions=int(report.get("tool_actions", 0)),
        verification_attempts=int(report.get("verification_attempts", 0)),
        final_coding_phase=(
            str(report["final_coding_phase"]) if report.get("final_coding_phase") else None
        ),
        pending_commitments=int(report.get("pending_commitments", 0)),
        failure_reason=(str(report["failure_reason"]) if report.get("failure_reason") else None),
        changed_files=changed_files,
        latest_code_verification_fingerprint=(
            str(code_run["run_fingerprint"])
            if code_run is not None and code_run.get("run_fingerprint")
            else None
        ),
        latest_code_verdict=(
            str(code_run["verdict"])
            if code_run is not None and code_run.get("verdict")
            else None
        ),
        latest_browser_verification_fingerprint=(
            str(browser_run["run_fingerprint"])
            if browser_run is not None and browser_run.get("run_fingerprint")
            else None
        ),
        latest_browser_verdict=(
            str(browser_run["verdict"])
            if browser_run is not None and browser_run.get("verdict")
            else None
        ),
    )
    return summary, manifest


def _comparability_mismatches(
    left: ComparableRunSummary,
    right: ComparableRunSummary,
    left_manifest: CodingRunManifest,
    right_manifest: CodingRunManifest,
) -> tuple[str, ...]:
    mismatches: list[str] = []

    def require_equal(name: str, a: object, b: object) -> None:
        if a != b:
            mismatches.append(name)

    require_equal("task", left.task, right.task)
    require_equal("source_fingerprint", left.source_fingerprint, right.source_fingerprint)
    require_equal(
        "verification_plan_fingerprint",
        left.verification_plan_fingerprint,
        right.verification_plan_fingerprint,
    )
    require_equal(
        "browser_verification_plan_fingerprint",
        left.browser_verification_plan_fingerprint,
        right.browser_verification_plan_fingerprint,
    )
    require_equal(
        "starting_project_memory_fingerprint",
        left.starting_project_memory_fingerprint,
        right.starting_project_memory_fingerprint,
    )
    require_equal(
        "project_memory_key", left_manifest.project_memory_key, right_manifest.project_memory_key
    )
    require_equal("max_reasoning_steps", left_manifest.max_reasoning_steps, right_manifest.max_reasoning_steps)
    require_equal("max_tool_actions", left_manifest.max_tool_actions, right_manifest.max_tool_actions)
    require_equal("max_output_tokens", left_manifest.max_output_tokens, right_manifest.max_output_tokens)
    require_equal("baseline_verification", left_manifest.baseline_verification, right_manifest.baseline_verification)
    require_equal("max_idle_turns", left_manifest.max_idle_turns, right_manifest.max_idle_turns)
    require_equal(
        "max_inspection_streak",
        left_manifest.max_inspection_streak,
        right_manifest.max_inspection_streak,
    )
    require_equal(
        "max_no_progress_streak",
        left_manifest.max_no_progress_streak,
        right_manifest.max_no_progress_streak,
    )
    require_equal(
        "max_same_failure_count",
        left_manifest.max_same_failure_count,
        right_manifest.max_same_failure_count,
    )
    require_equal(
        "isolation_support_paths",
        left_manifest.isolation_support_paths,
        right_manifest.isolation_support_paths,
    )
    if not left_manifest.isolated or not right_manifest.isolated:
        mismatches.append("isolated_run_required")
    return tuple(mismatches)


def compare_profile_run_roots(
    left_root: str | Path,
    right_root: str | Path,
) -> ProfileRunComparisonReport:
    left, left_manifest = load_comparable_run(left_root)
    right, right_manifest = load_comparable_run(right_root)
    incompatibilities = _comparability_mismatches(left, right, left_manifest, right_manifest)

    if left.succeeded and right.succeeded:
        outcome = "both_succeeded"
    elif left.succeeded:
        outcome = "left_only_succeeded"
    elif right.succeeded:
        outcome = "right_only_succeeded"
    else:
        outcome = "both_failed"

    left_files = set(left.changed_files)
    right_files = set(right.changed_files)
    return ProfileRunComparisonReport(
        comparison_id=f"pcmp_{uuid.uuid4().hex}",
        strictly_comparable=not incompatibilities,
        incompatibilities=incompatibilities,
        left=left,
        right=right,
        outcome_relation=outcome,
        metric_deltas_right_minus_left=ProfileRunMetricDeltas(
            reasoning_steps=right.reasoning_steps - left.reasoning_steps,
            tool_actions=right.tool_actions - left.tool_actions,
            verification_attempts=right.verification_attempts - left.verification_attempts,
            changed_file_count=len(right_files) - len(left_files),
        ),
        changed_files_only_left=tuple(sorted(left_files - right_files)),
        changed_files_only_right=tuple(sorted(right_files - left_files)),
        changed_files_both=tuple(sorted(left_files & right_files)),
    )


def write_profile_run_comparison(
    report: ProfileRunComparisonReport,
    output: str | Path,
) -> Path:
    target = Path(output).resolve()
    if target.suffix.casefold() != ".json":
        target.mkdir(parents=True, exist_ok=True)
        target = target / "profile-run-comparison.json"
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return target
