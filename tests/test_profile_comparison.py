from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_x.coding.model_selection import ResolvedModelSelection
from harness_x.coding.profile_comparison import compare_profile_run_roots, load_comparable_run
from harness_x.coding.run_manifest import CodingRunManifest, load_coding_run_manifest


SOURCE_FP = "a" * 64
VERIFY_FP = "b" * 64
MEMORY_FP = "c" * 64
HARNESS_FP = "f" * 64


def _selection(profile_id: str, model: str) -> ResolvedModelSelection:
    return ResolvedModelSelection(
        source="profile",
        profile_id=profile_id,
        role=profile_id,
        backend="openai",
        model=model,
        base_url="http://127.0.0.1:8000/v1",
        max_output_tokens=32768,
    )


def _write_run(
    root: Path,
    *,
    profile_id: str,
    model: str,
    succeeded: bool,
    reasoning_steps: int,
    tool_actions: int,
    verification_attempts: int,
    changed_files: tuple[str, ...],
    source_fingerprint: str = SOURCE_FP,
    verification_fingerprint: str = VERIFY_FP,
    memory_fingerprint: str = MEMORY_FP,
    harness_fingerprint: str = HARNESS_FP,
    task: str = "Implement the exact same feature",
) -> None:
    root.mkdir(parents=True)
    selection = _selection(profile_id, model)
    manifest = CodingRunManifest(
        task=task,
        workspace_root="/source",
        output_root=str(root),
        isolated=True,
        harness_version="0.1.0a0-test",
        harness_package_fingerprint=harness_fingerprint,
        verification_plan_fingerprint=verification_fingerprint,
        project_memory_root=f"/memory/{profile_id}",
        project_memory_key="logical-project",
        starting_project_memory_fingerprint=memory_fingerprint,
        model_selection=selection,
        max_reasoning_steps=32,
        max_tool_actions=48,
        max_output_tokens=65536,
        baseline_verification=True,
        max_idle_turns=3,
        max_inspection_streak=6,
        max_no_progress_streak=4,
        max_same_failure_count=3,
        isolation_retention="always",
    )
    (root / "coding-run-manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (root / "model-selection.json").write_text(
        selection.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    report = {
        "schema_version": "coding-task-report-v21-isolated-procedure-revision",
        "succeeded": succeeded,
        "status": "completed" if succeeded else "failed",
        "task": task,
        "reasoning_steps": reasoning_steps,
        "tool_actions": tool_actions,
        "verification_attempts": verification_attempts,
        "final_coding_phase": "complete" if succeeded else "blocked",
        "pending_commitments": 0 if succeeded else 1,
        "failure_reason": None if succeeded else "verification_failed",
        "verification_plan": {
            "schema_version": "coding-verification-plan-v1",
            "name": "test",
            "checks": [],
            "fail_fast_required": True,
            "fingerprint": verification_fingerprint,
        },
        "verification_runs": [
            {
                "run_fingerprint": ("d" if profile_id == "main" else "e") * 64,
                "verdict": "pass" if succeeded else "fail",
            }
        ],
        "isolation": {
            "source": {
                "fingerprint": source_fingerprint,
                "head_sha": "1" * 40,
            },
            "changes": [{"path": path} for path in changed_files],
        },
    }
    (root / "coding-task-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


def test_strict_comparison_reports_evidence_deltas_without_winner(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_run(
        left,
        profile_id="main",
        model="Qwen/Qwen3.8-27B",
        succeeded=True,
        reasoning_steps=18,
        tool_actions=14,
        verification_attempts=3,
        changed_files=("src/a.py", "tests/test_a.py"),
    )
    _write_run(
        right,
        profile_id="coder",
        model="Qwen/Qwen3-Coder-30B-A3B-Instruct",
        succeeded=True,
        reasoning_steps=12,
        tool_actions=10,
        verification_attempts=2,
        changed_files=("src/a.py", "tests/test_a.py", "docs/a.md"),
    )

    report = compare_profile_run_roots(left, right)

    assert report.strictly_comparable is True
    assert report.incompatibilities == ()
    assert report.outcome_relation == "both_succeeded"
    assert report.metric_deltas_right_minus_left.reasoning_steps == -6
    assert report.metric_deltas_right_minus_left.tool_actions == -4
    assert report.metric_deltas_right_minus_left.verification_attempts == -1
    assert report.changed_files_both == ("src/a.py", "tests/test_a.py")
    assert report.changed_files_only_right == ("docs/a.md",)
    assert report.left.harness_package_fingerprint == HARNESS_FP
    serialized = report.model_dump(mode="json")
    assert "winner" not in serialized
    assert "score" not in serialized


def test_starting_memory_drift_makes_comparison_non_strict(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_run(
        left,
        profile_id="main",
        model="main-model",
        succeeded=True,
        reasoning_steps=5,
        tool_actions=4,
        verification_attempts=1,
        changed_files=(),
    )
    _write_run(
        right,
        profile_id="coder",
        model="coder-model",
        succeeded=True,
        reasoning_steps=5,
        tool_actions=4,
        verification_attempts=1,
        changed_files=(),
        memory_fingerprint="9" * 64,
    )

    report = compare_profile_run_roots(left, right)

    assert report.strictly_comparable is False
    assert "starting_project_memory_fingerprint" in report.incompatibilities


def test_source_verification_and_harness_drift_are_reported_independently(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_run(
        left,
        profile_id="main",
        model="main-model",
        succeeded=False,
        reasoning_steps=8,
        tool_actions=6,
        verification_attempts=2,
        changed_files=("src/x.py",),
    )
    _write_run(
        right,
        profile_id="reasoning",
        model="reasoning-model",
        succeeded=True,
        reasoning_steps=7,
        tool_actions=5,
        verification_attempts=2,
        changed_files=("src/x.py",),
        source_fingerprint="7" * 64,
        verification_fingerprint="8" * 64,
        harness_fingerprint="6" * 64,
    )

    report = compare_profile_run_roots(left, right)

    assert report.outcome_relation == "right_only_succeeded"
    assert "source_fingerprint" in report.incompatibilities
    assert "verification_plan_fingerprint" in report.incompatibilities
    assert "harness_package_fingerprint" in report.incompatibilities


def test_run_artifact_internal_selection_mismatch_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _write_run(
        root,
        profile_id="main",
        model="main-model",
        succeeded=True,
        reasoning_steps=1,
        tool_actions=1,
        verification_attempts=1,
        changed_files=(),
    )
    replacement = _selection("coder", "other-model")
    (root / "model-selection.json").write_text(
        replacement.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="selection artifact disagrees"):
        load_comparable_run(root)


def test_manifest_tamper_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _write_run(
        root,
        profile_id="main",
        model="main-model",
        succeeded=True,
        reasoning_steps=1,
        tool_actions=1,
        verification_attempts=1,
        changed_files=(),
    )
    path = root / "coding-run-manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["max_tool_actions"] = 999
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        load_coding_run_manifest(path)
