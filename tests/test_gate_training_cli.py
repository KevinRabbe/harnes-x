from __future__ import annotations

import json
from datetime import datetime, timezone

from harness_x.cli import main
from harness_x.config import RetrievalGateConfig
from harness_x.controllers import GateTrainingDatasetManifest
from harness_x.core.clock import FixedClock
from harness_x.core.events import EventType
from harness_x.core.ids import SystemVersion, TaskId, TraceId
from harness_x.gates import RetrievalGate, RetrievalRequest
from harness_x.telemetry import TraceRecorder, TraceStore


def test_collect_gate_training_data_cli_writes_verified_bundle(tmp_path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(
        TraceStore(trace_path),
        TraceId.new(),
        TaskId.new(),
        SystemVersion(value="m17-cli-v1"),
        FixedClock(datetime(2026, 8, 20, tzinfo=timezone.utc)),
    )
    recorder.emit(EventType.TASK_CREATED, "test")
    RetrievalGate(recorder, RetrievalGateConfig()).evaluate(
        RetrievalRequest(
            current_routine="research",
            unresolved_entities=("alpha",),
            working_pressure=0.2,
        )
    )
    recorder.emit(
        EventType.MEMORY_RETRIEVED,
        "memory.episodic",
        metadata={"result_count": 1},
    )

    output = tmp_path / "gate-data"
    result = main(
        [
            "collect-gate-training-data",
            str(trace_path),
            "--outcome-horizon-steps",
            "8",
            "--output",
            str(output),
        ]
    )
    assert result == 0
    assert (output / "records.jsonl").is_file()
    manifest = GateTrainingDatasetManifest.model_validate_json(
        (output / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.record_count == 1
    assert manifest.records_by_gate == {"retrieval": 1}
    row = json.loads((output / "records.jsonl").read_text(encoding="utf-8"))
    assert row["later_usefulness"]["state"] == "positive"
