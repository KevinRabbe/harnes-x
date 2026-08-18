from pathlib import Path

from harness_x.cli import main


def test_replay_fixture_cli(capsys) -> None:
    fixture = Path(__file__).parent / "fixtures" / "golden_trace.json"

    rc = main(["replay-fixture", str(fixture)])

    assert rc == 0
    assert "valid: trace=trace_golden steps=4" in capsys.readouterr().out
