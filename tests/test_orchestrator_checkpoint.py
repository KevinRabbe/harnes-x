from datetime import datetime, timezone

import pytest

from harness_x.core import CheckpointError, FixedClock, SystemVersion, TaskId, TraceId
from harness_x.orchestrator import CheckpointStore, OperatingMode, TaskOrchestrator
from harness_x.telemetry import TraceRecorder, TraceStore


def _recorder(tmp_path) -> TraceRecorder:
    return TraceRecorder(
        TraceStore(tmp_path / "trace.jsonl"),
        TraceId(value="trace_checkpoint"),
        TaskId(value="task_checkpoint"),
        SystemVersion(value="test-v1"),
        FixedClock(datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)),
    )


def test_suspend_checkpoint_restore_and_exact_resume(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    path = tmp_path / "checkpoints" / "task.json"
    task = TaskOrchestrator.create(recorder)
    task.start()
    task.suspend("external_interrupt", checkpoint_path=path)

    assert task.session.mode == OperatingMode.SUSPENDED
    checkpoint = CheckpointStore().load(path)
    restored = TaskOrchestrator.restore(checkpoint, recorder)
    assert restored.session.mode == OperatingMode.SUSPENDED
    assert restored.session.resume_mode == OperatingMode.TASK_ACTIVE

    restored.resume()
    assert restored.session.mode == OperatingMode.TASK_ACTIVE
    assert restored.session.resume_mode is None


def test_checkpoint_integrity_detects_tampering(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    path = tmp_path / "task.json"
    task = TaskOrchestrator.create(recorder)
    task.start()
    task.suspend("pause", checkpoint_path=path)

    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace('"task_active"', '"verify"'), encoding="utf-8")

    with pytest.raises(CheckpointError, match="hash mismatch"):
        CheckpointStore().load(path)


def test_stale_checkpoint_cannot_fork_existing_trace(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    path = tmp_path / "task.json"
    task = TaskOrchestrator.create(recorder)
    task.start()
    task.suspend("pause", checkpoint_path=path)
    checkpoint = CheckpointStore().load(path)

    task.resume()

    with pytest.raises(CheckpointError, match="stale"):
        TaskOrchestrator.restore(checkpoint, recorder)


def test_checkpoint_requires_safe_suspension(tmp_path) -> None:
    recorder = _recorder(tmp_path)
    task = TaskOrchestrator.create(recorder)
    task.start()

    with pytest.raises(CheckpointError, match="suspended"):
        task.checkpoint(tmp_path / "unsafe.json")
