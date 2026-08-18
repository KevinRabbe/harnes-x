from pathlib import Path

from harness_x.config import load_config


def test_default_config_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")
    assert config.system_version.value == "0.1.0-alpha.0"
    assert config.budget.max_reasoning_steps == 32
    assert config.gates.retrieval.pressure_suppress_threshold == 0.85
    assert config.gates.focus.pin_priority_threshold == 0.85
    assert config.gates.maintenance.unresolved_error_trigger == 3
    assert config.gates.compute.policy_version == "compute-v0"
