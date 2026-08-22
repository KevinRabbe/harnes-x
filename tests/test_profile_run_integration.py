from __future__ import annotations

import json
from pathlib import Path

from harness_x.coding.profile_run_cli import main
from harness_x.coding.run_manifest import directory_fingerprint, load_coding_run_manifest
from harness_x.coding.verification import FileExistsVerificationCheck, VerificationPlan
from harness_x.reasoning import RawReasoningOutput, StubReasoningCore


def test_profile_run_wrapper_uses_real_isolated_verified_stack_without_model_inference(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("README.md").write_text("comparison fixture\n", encoding="utf-8")
    verification_path = tmp_path / "verification.json"
    VerificationPlan(
        checks=(
            FileExistsVerificationCheck(
                check_id="readme",
                name="README exists",
                path="README.md",
            ),
        )
    ).model_dump_json(indent=2)
    verification_path.write_text(
        VerificationPlan(
            checks=(
                FileExistsVerificationCheck(
                    check_id="readme",
                    name="README exists",
                    path="README.md",
                ),
            )
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "run-main"
    memory = tmp_path / "memory-main"
    empty_memory_fingerprint = directory_fingerprint(memory)

    monkeypatch.setattr(
        "harness_x.coding.profile_run_cli.build_selected_reasoning_core",
        lambda selection: StubReasoningCore(RawReasoningOutput(status="complete")),
    )

    exit_code = main(
        [
            str(workspace),
            "--task",
            "Keep the existing README present",
            "--verification-plan",
            str(verification_path),
            "--model-profile",
            "main",
            "--project-memory-root",
            str(memory),
            "--project-memory-key",
            "comparison-project",
            "--retain-workspace",
            "never",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    manifest = load_coding_run_manifest(output / "coding-run-manifest.json")
    assert manifest.model_selection.profile_id == "main"
    assert manifest.starting_project_memory_fingerprint == empty_memory_fingerprint
    report = json.loads((output / "coding-task-report.json").read_text(encoding="utf-8"))
    assert report["succeeded"] is True
    assert report["isolation"]["source"]["source_root"] == str(workspace.resolve())
    assert (output / "model-selection.json").is_file()
