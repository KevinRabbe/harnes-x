from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from harness_x.coding import (
    IsolatedRepositoryCodingTaskRuntime,
    IsolationRetention,
    IsolationStrategy,
    TaskWorkspaceIsolationManager,
)
from harness_x.reasoning import RawActionProposal, RawReasoningOutput, ReasoningCoreInfo


def _git_init(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.invalid"], cwd=root, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Harness X CI"], cwd=root, check=True
    )
    subprocess.run(["git", "add", "--", *files], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)


class SequenceCore:
    def __init__(self, outputs: list[RawReasoningOutput]) -> None:
        self.outputs = list(outputs)
        self._info = ReasoningCoreInfo(
            name="m24-sequence-core",
            version="m24-sequence-v1",
            model="deterministic-sequence",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        if not self.outputs:
            raise RuntimeError("sequence core ran out of outputs")
        return self.outputs.pop(0)


def _verify_file(path: str, expected: str) -> tuple[str, ...]:
    return (
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            f"assert Path({path!r}).read_text(encoding='utf-8') == {expected!r}"
        ),
    )


def test_clean_git_task_changes_never_touch_source_and_export_before_cleanup(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git_init(source, {"app.py": "VALUE = 1\n"})
    artifacts = tmp_path / "artifacts"
    isolation_root = tmp_path / "isolated"
    manager = TaskWorkspaceIsolationManager(
        source,
        artifacts,
        isolation_root=isolation_root,
        retention=IsolationRetention.NEVER,
    )

    prepared = manager.prepare()
    assert prepared.strategy == IsolationStrategy.GIT_CLONE
    assert prepared.source.dirty is False
    assert prepared.source.head_sha
    assert (prepared.workspace_root / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"

    (prepared.workspace_root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (prepared.workspace_root / "new.txt").write_text("new\n", encoding="utf-8")
    result = manager.finalize(succeeded=True)

    assert source.joinpath("app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert result.retained is False
    assert not prepared.session_root.exists()
    assert {(item.path, item.change_type) for item in result.changes} == {
        ("app.py", "modified"),
        ("new.txt", "added"),
    }
    assert artifacts.joinpath("isolated-changes/files/app.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 2\n"
    assert artifacts.joinpath("isolated-changes/files/new.txt").read_text(
        encoding="utf-8"
    ) == "new\n"
    assert Path(result.change_manifest_path).is_file()


def test_dirty_git_source_is_reproduced_but_task_delta_starts_after_dirty_baseline(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git_init(
        source,
        {
            ".gitignore": "node_modules/\n",
            "app.py": "base\n",
        },
    )
    source.joinpath("app.py").write_text("operator-dirty\n", encoding="utf-8")
    source.joinpath("note.txt").write_text("untracked\n", encoding="utf-8")
    source.joinpath("node_modules/pkg").mkdir(parents=True)
    source.joinpath("node_modules/pkg/index.js").write_text(
        "module.exports = 1\n", encoding="utf-8"
    )
    artifacts = tmp_path / "artifacts"
    manager = TaskWorkspaceIsolationManager(
        source,
        artifacts,
        isolation_root=tmp_path / "isolated",
        retention=IsolationRetention.ALWAYS,
        support_paths=("node_modules",),
    )

    prepared = manager.prepare()
    assert prepared.source.dirty is True
    assert prepared.workspace_root.joinpath("app.py").read_text(
        encoding="utf-8"
    ) == "operator-dirty\n"
    assert prepared.workspace_root.joinpath("note.txt").read_text(
        encoding="utf-8"
    ) == "untracked\n"
    assert prepared.workspace_root.joinpath("node_modules/pkg/index.js").is_file()

    dirty_baseline_sha = hashlib.sha256(b"operator-dirty\n").hexdigest()
    prepared.workspace_root.joinpath("app.py").write_text("agent-result\n", encoding="utf-8")
    result = manager.finalize(succeeded=True)

    assert source.joinpath("app.py").read_text(encoding="utf-8") == "operator-dirty\n"
    assert source.joinpath("note.txt").read_text(encoding="utf-8") == "untracked\n"
    assert [(item.path, item.change_type) for item in result.changes] == [
        ("app.py", "modified")
    ]
    assert result.changes[0].baseline_sha256 == dirty_baseline_sha
    assert prepared.session_root.exists()
    assert Path(result.initial_git_patch_path or "").read_bytes()


def test_non_git_source_uses_snapshot_and_cleanup_policy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("app.txt").write_text("before\n", encoding="utf-8")
    source.joinpath("node_modules/pkg").mkdir(parents=True)
    source.joinpath("node_modules/pkg/data.txt").write_text("dependency\n", encoding="utf-8")
    manager = TaskWorkspaceIsolationManager(
        source,
        tmp_path / "artifacts",
        isolation_root=tmp_path / "isolated",
        retention=IsolationRetention.ON_FAILURE,
    )

    prepared = manager.prepare()
    assert prepared.strategy == IsolationStrategy.SNAPSHOT_COPY
    assert prepared.workspace_root.joinpath("node_modules/pkg/data.txt").is_file()
    prepared.workspace_root.joinpath("app.txt").write_text("after\n", encoding="utf-8")
    result = manager.finalize(succeeded=True)

    assert source.joinpath("app.txt").read_text(encoding="utf-8") == "before\n"
    assert result.retained is False
    assert not prepared.session_root.exists()
    assert [(item.path, item.change_type) for item in result.changes] == [
        ("app.txt", "modified")
    ]


def test_isolation_root_inside_source_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="must not be inside"):
        TaskWorkspaceIsolationManager(
            source,
            tmp_path / "artifacts",
            isolation_root=source / ".task-workspaces",
        )


def test_isolated_runtime_verifies_in_task_workspace_and_leaves_source_unchanged(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git_init(source, {"result.txt": "bad\n"})
    baseline_sha = hashlib.sha256(b"bad\n").hexdigest()
    core = SequenceCore(
        [
            RawReasoningOutput(
                status="continue",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_patch",
                        arguments={
                            "mode": "range",
                            "path": "result.txt",
                            "start_line": 1,
                            "end_line": 1,
                            "expected_sha256": baseline_sha,
                            "replacement": "ok\n",
                        },
                    ),
                ),
            ),
            RawReasoningOutput(status="complete"),
        ]
    )
    output = tmp_path / "run"

    report = IsolatedRepositoryCodingTaskRuntime(
        source,
        core,
        output,
        isolation_root=tmp_path / "isolated",
        retention=IsolationRetention.NEVER,
    ).run(
        "Fix result.txt",
        verification_commands=(_verify_file("result.txt", "ok\n"),),
    )

    assert report.succeeded is True
    assert report.schema_version == "coding-task-report-v2-isolated"
    assert source.joinpath("result.txt").read_text(encoding="utf-8") == "bad\n"
    assert report.isolation.source.source_root == str(source.resolve())
    assert report.isolation.retained is False
    assert report.isolation.changed_file_count == 1
    assert output.joinpath("isolation/isolated-changes/files/result.txt").read_text(
        encoding="utf-8"
    ) == "ok\n"
    assert not Path(report.isolation.workspace_root).exists()
