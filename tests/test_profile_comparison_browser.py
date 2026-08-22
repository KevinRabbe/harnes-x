from __future__ import annotations

import json
from pathlib import Path

from harness_x.coding.model_selection import ResolvedModelSelection
from harness_x.coding.profile_comparison import compare_profile_run_roots
from harness_x.coding.run_manifest import CodingRunManifest


SOURCE_FP = "1" * 64
CODE_PLAN_FP = "2" * 64
BROWSER_PLAN_FP = "3" * 64
MEMORY_FP = "4" * 64


def _write_browser_run(root: Path, profile_id: str, model: str) -> None:
    root.mkdir(parents=True)
    selection = ResolvedModelSelection(
        source="profile",
        profile_id=profile_id,
        role=profile_id,
        backend="openai",
        model=model,
        base_url="http://127.0.0.1:8000/v1",
        max_output_tokens=32768,
    )
    manifest = CodingRunManifest(
        task="Repair the browser-visible feature",
        workspace_root="/source",
        output_root=str(root),
        isolated=True,
        verification_plan_fingerprint=CODE_PLAN_FP,
        browser_verification_plan_fingerprint=BROWSER_PLAN_FP,
        project_memory_root=f"/memory/{profile_id}",
        project_memory_key="browser-project",
        starting_project_memory_fingerprint=MEMORY_FP,
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
        "schema_version": "coding-task-report-v22-isolated-browser-procedure-revision",
        "succeeded": True,
        "status": "completed",
        "task": "Repair the browser-visible feature",
        "reasoning_steps": 9,
        "tool_actions": 7,
        "verification_attempts": 2,
        "final_coding_phase": "complete",
        "pending_commitments": 0,
        "verification_plan": {"fingerprint": CODE_PLAN_FP},
        "verification_runs": [{"run_fingerprint": "5" * 64, "verdict": "pass"}],
        "browser_verification_plan": {"fingerprint": BROWSER_PLAN_FP},
        "browser_verification_runs": [
            {"run_fingerprint": ("6" if profile_id == "main" else "7") * 64, "verdict": "pass"}
        ],
        "isolation": {
            "source": {"fingerprint": SOURCE_FP, "head_sha": "a" * 40},
            "changes": [{"path": "web/app.js"}],
        },
    }
    (root / "coding-task-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


def test_browser_runs_are_strictly_comparable_only_with_same_browser_plan(tmp_path: Path) -> None:
    left = tmp_path / "main"
    right = tmp_path / "coder"
    _write_browser_run(left, "main", "main-model")
    _write_browser_run(right, "coder", "coder-model")

    report = compare_profile_run_roots(left, right)

    assert report.strictly_comparable is True
    assert report.left.latest_browser_verdict == "pass"
    assert report.right.latest_browser_verification_fingerprint == "7" * 64
    assert report.left.browser_verification_plan_fingerprint == BROWSER_PLAN_FP
