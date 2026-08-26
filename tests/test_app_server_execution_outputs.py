from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import harness_x.app_server.execution_outputs as output_module
from harness_x.app_server.execution_outputs import (
    ExecutionArtifactRegistry,
    build_execution_diff_projection,
)
from harness_x.app_server.protocol import (
    AppEvent,
    AppEventKind,
    AppSessionSnapshot,
    AppSessionStatus,
    CodingSessionRequest,
)
from harness_x.repository import RepositoryIdentity
from harness_x.tools.base import SideEffectLevel
from harness_x.tools.git_v2 import git_status_v2_definition
from harness_x.tools.repository import (
    GitDiffOutput,
    GitStatusEntry,
    GitStatusOutput,
    git_diff_definition,
)


def _snapshot(workspace: Path, output_root: Path, *, terminal: bool = False) -> AppSessionSnapshot:
    now = datetime.now(timezone.utc)
    return AppSessionSnapshot(
        session_id="app_" + "1" * 32,
        status=AppSessionStatus.SUCCEEDED if terminal else AppSessionStatus.RUNNING,
        request=CodingSessionRequest(
            workspace_root=workspace,
            task="test execution outputs",
            model_profile="main",
            verification_commands=("git diff --check",),
        ),
        output_root=str(output_root),
        created_at=now,
        started_at=now,
        completed_at=now if terminal else None,
    )


def _status(root: Path, *entries: GitStatusEntry, truncated: bool = False) -> GitStatusOutput:
    return GitStatusOutput(
        identity=RepositoryIdentity(
            root=str(root),
            is_git_repository=True,
            head_sha="a" * 40,
            branch="main",
            dirty=bool(entries),
        ),
        entries=entries,
        truncated=truncated,
    )


def test_execution_diff_reuses_side_effect_free_repository_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    status_tool = git_status_v2_definition(workspace)
    diff_tool = git_diff_definition(workspace)
    assert status_tool.spec.side_effect_level == SideEffectLevel.NONE
    assert diff_tool.spec.side_effect_level == SideEffectLevel.NONE
    assert status_tool.spec.permissions == ("workspace.read",)
    assert diff_tool.spec.permissions == ("workspace.read",)

    monkeypatch.setattr(
        output_module,
        "_status_output",
        lambda _root: _status(
            workspace,
            GitStatusEntry(path="tracked.txt", index_status=" ", worktree_status="M"),
            GitStatusEntry(path="new.txt", index_status="?", worktree_status="?"),
            GitStatusEntry(path="deleted.txt", index_status="D", worktree_status=" "),
        ),
    )
    calls: list[tuple[str, bool]] = []

    def fake_diff(_root: Path, path: str, *, staged: bool) -> GitDiffOutput:
        calls.append((path, staged))
        if path == "tracked.txt" and not staged:
            text = "--- a/tracked.txt\n+++ b/tracked.txt\n@@ -1 +1,2 @@\n before\n+after\n"
        elif path == "deleted.txt" and staged:
            text = "--- a/deleted.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-delete me\n"
        else:
            text = ""
        return GitDiffOutput(path=path, staged=staged, diff=text, truncated=False)

    monkeypatch.setattr(output_module, "_diff_output", fake_diff)
    projection = build_execution_diff_projection(
        project_id="project_" + "2" * 32,
        chat_id="chat_" + "3" * 32,
        execution_id="exec_" + "4" * 32,
        snapshot=_snapshot(workspace, tmp_path / "runs" / "run"),
        workspace_root=workspace,
    )
    assert projection.available is True
    by_path = {item.path: item for item in projection.files}
    assert by_path["tracked.txt"].status == "modified"
    assert by_path["new.txt"].status == "added"
    assert by_path["deleted.txt"].status == "deleted"
    assert "after" in (by_path["tracked.txt"].patch or "")
    assert "delete me" in (by_path["deleted.txt"].patch or "")
    assert by_path["new.txt"].patch is None
    assert calls == [("tracked.txt", False), ("deleted.txt", True)]
    assert projection.read_only is True
    assert projection.execution_authorship_proven is False
    assert projection.verification_authority is False
    assert projection.evidence_authority is False
    assert str(workspace) not in projection.model_dump_json()


def test_execution_diff_bounds_entries_filters_unsafe_paths_and_reports_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    entries = tuple(
        GitStatusEntry(path=f"safe-{index:02d}.txt", index_status="?", worktree_status="?")
        for index in range(60)
    ) + (
        GitStatusEntry(path="../escape.txt", index_status="?", worktree_status="?"),
        GitStatusEntry(path="dir\\unsafe.txt", index_status="?", worktree_status="?"),
    )
    monkeypatch.setattr(
        output_module,
        "_status_output",
        lambda _root: _status(workspace, *entries, truncated=False),
    )
    monkeypatch.setattr(
        output_module,
        "_diff_output",
        lambda _root, path, *, staged: GitDiffOutput(
            path=path, staged=staged, diff="", truncated=False
        ),
    )
    projection = build_execution_diff_projection(
        project_id="project_" + "2" * 32,
        chat_id="chat_" + "3" * 32,
        execution_id="exec_" + "4" * 32,
        snapshot=_snapshot(workspace, tmp_path / "runs" / "run"),
        workspace_root=workspace,
    )
    assert projection.available is True
    assert projection.detected_files == 60
    assert len(projection.files) == 50
    assert projection.unsafe_entries_skipped == 2
    assert projection.truncated is True

    monkeypatch.setattr(
        output_module,
        "_status_output",
        lambda _root: GitStatusOutput(
            identity=RepositoryIdentity(root=str(workspace), is_git_repository=False),
        ),
    )
    unavailable = build_execution_diff_projection(
        project_id="project_" + "5" * 32,
        chat_id="chat_" + "6" * 32,
        execution_id="exec_" + "7" * 32,
        snapshot=_snapshot(workspace, tmp_path / "runs" / "plain"),
        workspace_root=workspace,
    )
    assert unavailable.available is False
    assert unavailable.unavailable_reason == "workspace_not_git_repository"


def test_execution_diff_bounds_patch_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        output_module,
        "_status_output",
        lambda _root: _status(
            workspace,
            GitStatusEntry(path="large.txt", index_status=" ", worktree_status="M"),
        ),
    )
    large = "".join(f"+line-{index:04d} xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n" for index in range(500))
    monkeypatch.setattr(
        output_module,
        "_diff_output",
        lambda _root, path, *, staged: GitDiffOutput(
            path=path,
            staged=staged,
            diff=large[:2800],
            truncated=True,
        ),
    )
    projection = build_execution_diff_projection(
        project_id="project_" + "2" * 32,
        chat_id="chat_" + "3" * 32,
        execution_id="exec_" + "4" * 32,
        snapshot=_snapshot(workspace, tmp_path / "runs" / "run"),
        workspace_root=workspace,
    )
    assert projection.truncated is True
    item = projection.files[0]
    assert item.patch_truncated is True
    assert item.patch is not None and len(item.patch) <= 6000
    assert len(item.patch.splitlines()) <= 120


def _artifact_event(snapshot: AppSessionSnapshot, payload: bytes, path: Path) -> AppEvent:
    return AppEvent.create(
        session_id=snapshot.session_id,
        sequence=1,
        kind=AppEventKind.ARTIFACT_AVAILABLE,
        payload={
            "artifact_kind": "coding_task_report",
            "path": str(path),
            "source_bytes": len(payload),
            "source_sha256": hashlib.sha256(payload).hexdigest(),
        },
    )


def _artifact_fixture(tmp_path: Path) -> tuple[Path, Path, AppSessionSnapshot, bytes, AppEvent]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run_root = tmp_path / "runs"
    run_root.mkdir()
    output_root = run_root / "conversation_resources_exec_test"
    output_root.mkdir()
    report_path = output_root / "coding-task-report.json"
    payload = b'{"succeeded":true}\n'
    report_path.write_bytes(payload)
    snapshot = _snapshot(workspace, output_root, terminal=True)
    return run_root, report_path, snapshot, payload, _artifact_event(snapshot, payload, report_path)


def test_registered_artifact_is_event_derived_durable_and_digest_verified(tmp_path: Path) -> None:
    run_root, report_path, snapshot, payload, event = _artifact_fixture(tmp_path)
    registry = ExecutionArtifactRegistry(tmp_path / "state")
    records = registry.sync_known_artifacts(
        project_id="project_" + "2" * 32,
        chat_id="chat_" + "3" * 32,
        execution_id="exec_" + "4" * 32,
        snapshot=snapshot,
        events=(event,),
        run_root=run_root,
    )
    assert len(records) == 1
    record = records[0]
    assert record.storage_name == "coding-task-report.json"
    assert record.size_bytes == len(payload)
    assert record.sha256 == hashlib.sha256(payload).hexdigest()
    assert record.verification_authority is False
    assert record.evidence_authority is False
    serialized = record.model_dump_json()
    assert str(report_path.parent) not in serialized
    assert str(run_root) not in serialized
    assert registry.bytes_for(record, snapshot=snapshot, run_root=run_root) == payload

    restarted = ExecutionArtifactRegistry(tmp_path / "state")
    assert restarted.artifact(record.artifact_id) == record
    assert restarted.bytes_for(record, snapshot=snapshot, run_root=run_root) == payload

    report_path.write_bytes(b'{"succeeded":false}\n')
    with pytest.raises(RuntimeError, match="do not match registration"):
        restarted.bytes_for(record, snapshot=snapshot, run_root=run_root)


def test_artifact_registration_rejects_escape_digest_mismatch_and_unregistered_files(
    tmp_path: Path,
) -> None:
    run_root, report_path, snapshot, payload, event = _artifact_fixture(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside")
    escaped = _artifact_event(snapshot, b"outside", outside)
    registry = ExecutionArtifactRegistry(tmp_path / "state-a")
    with pytest.raises(RuntimeError, match="canonical session output"):
        registry.sync_known_artifacts(
            project_id="project_" + "2" * 32,
            chat_id="chat_" + "3" * 32,
            execution_id="exec_" + "4" * 32,
            snapshot=snapshot,
            events=(escaped,),
            run_root=run_root,
        )

    mismatch = AppEvent.create(
        session_id=snapshot.session_id,
        sequence=event.sequence,
        kind=AppEventKind.ARTIFACT_AVAILABLE,
        payload={
            "artifact_kind": "coding_task_report",
            "path": str(report_path),
            "source_bytes": len(payload),
            "source_sha256": "0" * 64,
        },
    )
    registry = ExecutionArtifactRegistry(tmp_path / "state-b")
    with pytest.raises(RuntimeError, match="digest disagrees"):
        registry.sync_known_artifacts(
            project_id="project_" + "2" * 32,
            chat_id="chat_" + "3" * 32,
            execution_id="exec_" + "4" * 32,
            snapshot=snapshot,
            events=(mismatch,),
            run_root=run_root,
        )

    (report_path.parent / "unregistered.txt").write_text("not visible", encoding="utf-8")
    registry = ExecutionArtifactRegistry(tmp_path / "state-c")
    assert registry.sync_known_artifacts(
        project_id="project_" + "2" * 32,
        chat_id="chat_" + "3" * 32,
        execution_id="exec_" + "4" * 32,
        snapshot=snapshot,
        events=(),
        run_root=run_root,
    ) == ()
    assert "unregistered.txt" not in json.dumps(
        [item.model_dump(mode="json") for item in registry.for_execution("exec_" + "4" * 32)],
        sort_keys=True,
    )
