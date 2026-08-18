from pathlib import Path

from harness_x.telemetry import TraceFixture, TraceReplayer


def test_golden_trace_fixture_is_deterministic() -> None:
    path = Path(__file__).parent / "fixtures" / "golden_trace.json"
    fixture = TraceFixture.model_validate_json(path.read_text(encoding="utf-8"))

    state = TraceReplayer().assert_fixture(fixture)

    assert state.last_step == 4
    assert state.goals == {"goal_primary": "active"}
    assert state.modes == {"task_golden": "TASK_ACTIVE"}
