from pathlib import Path

from harness_x.benchmarks import run_scripted_autonomy_benchmark
from harness_x.config import load_config


def test_long_horizon_scripted_autonomy_suite(tmp_path) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")

    report = run_scripted_autonomy_benchmark(
        config,
        tmp_path / "scripted_autonomy",
    )

    assert report.passed
    assert report.total_events >= 300
    assert report.total_authoritative_transitions >= 300
    assert report.total_actions >= 60
    assert report.total_recoveries == 3

    by_name = {scenario.scenario: scenario for scenario in report.scenarios}
    assert set(by_name) == {
        "dependency",
        "interruption",
        "memory_pressure",
        "failure_recovery",
        "contradiction",
    }

    for scenario in report.scenarios:
        assert scenario.passed, scenario.model_dump()
        assert scenario.goal_retained
        assert scenario.state_correct
        assert scenario.illegal_transitions == 0
        assert scenario.trace_complete
        assert scenario.replay_valid
        assert all(scenario.checks.values())

    dependency = by_name["dependency"]
    assert dependency.action_count == 14
    assert dependency.useful_retrievals >= 13

    interruption = by_name["interruption"]
    assert interruption.suspensions == 1
    assert interruption.checkpoints == 1
    assert interruption.action_count == 10

    pressure = by_name["memory_pressure"]
    assert pressure.maintenance_cycles > 0
    assert pressure.working_evictions > 0
    assert pressure.max_working_pressure >= 0.85
    assert pressure.action_count == 24

    recovery = by_name["failure_recovery"]
    assert recovery.recoveries == 3
    assert recovery.verification_failures >= 1

    contradiction = by_name["contradiction"]
    assert contradiction.action_count == 2
    assert contradiction.checks["contradiction_links_are_symmetric"]

    encoded = report.model_dump_json()
    assert '"suite_version":"scripted-autonomy-v1"' in encoded
